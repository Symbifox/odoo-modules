# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).

import json
import logging
import uuid

import requests

from odoo import api, fields, models
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)


class CalendarEvent(models.Model):
    """Extension of calendar.event for Nextcloud sync."""

    _inherit = "calendar.event"

    # Nextcloud sync fields
    x_caldav_etag = fields.Char(
        string="CalDAV ETag",
        help="ETag from Nextcloud for change detection",
        copy=False,
    )
    x_sync_source = fields.Selection(
        [
            ("odoo", "Created in Odoo"),
            ("nextcloud", "Synced from Nextcloud"),
            ("google", "Synced from Google Calendar"),
        ],
        string="Sync Source",
        help="Origin of this calendar event",
        copy=False,
    )
    x_caldav_href = fields.Char(
        string="CalDAV HREF",
        help="Full CalDAV URL of this event in Nextcloud",
        copy=False,
    )
    x_nc_calendar_id = fields.Many2one(
        "nextcloud.calendar.sync.config",
        string="Nextcloud Calendar",
        help="Nextcloud calendar configuration for sync",
        copy=False,
    )
    x_last_sync = fields.Datetime(
        string="Last Synced",
        help="Timestamp of last synchronization",
        copy=False,
    )
    x_nc_uid = fields.Char(
        string="Nextcloud UID",
        help="Unique identifier in Nextcloud/ICS format",
        copy=False,
    )
    color = fields.Integer(
        string="Color",
        default=0,
        help="Color index for calendar views",
    )

    def _bf_odoo_owns_attendees(self):
        """Odoo est-il la source des participants de cet événement ?

        Vrai pour les événements adossés à une `resource.booking`. Ce prédicat
        est employé aux DEUX bouts de l'aller-retour, et c'est le point : la
        poussée retire les participants pour ces événements-là, donc
        l'ingestion ne doit jamais les remettre en cause. Deux copies du même
        test finissent toujours par diverger.
        """
        self.ensure_one()
        return bool(
            "resource_booking_ids" in self._fields
            and self.resource_booking_ids
        )

    def _get_sync_payload(self, action):
        """Prepare payload for n8n webhook.

        Args:
            action: 'create', 'update', or 'delete'

        Returns:
            dict: Payload to send to n8n webhook
        """
        self.ensure_one()

        # Build attendees list.
        #
        # Booking-originated events (those tied to a resource.booking) MUST be
        # pushed WITHOUT attendees: the booker has already received our own
        # branded confirmation carrying a METHOD:REQUEST invitation. If we also
        # hand the attendee list to Nextcloud, its CalDAV scheduling plugin
        # emits a SECOND iMIP invitation to the same booker. Two competing
        # invitations for one slot is exactly what stops Gmail from auto-adding
        # the event (booker must click "Accept" on a .ics attachment instead),
        # reintroducing the Cal.com no-show problem seen in production.
        # The event itself still syncs to the organizer's calendar as a busy
        # block; only the invitee list is withheld.
        is_booking_event = self._bf_odoo_owns_attendees()
        attendees = []
        if not is_booking_event:
            for attendee in self.attendee_ids:
                attendees.append(
                    {
                        "email": attendee.email,
                        "name": attendee.partner_id.name if attendee.partner_id else "",
                        "status": attendee.state,
                    }
                )

        # Get organizer
        organizer = None
        if self.user_id and self.user_id.partner_id:
            organizer = {
                "email": self.user_id.partner_id.email,
                "name": self.user_id.partner_id.name,
            }

        # Compute full CalDAV resource URL for n8n
        # Handles: full URL already, relative path, or construct from UID
        caldav_resource_url = None
        # Guard: a calendar config without a base URL would crash on
        # `.rstrip()` (AttributeError: 'bool' has no attribute 'rstrip')
        # when nextcloud_base_url is False, silently killing the sync for
        # that event. Skip the URL computation instead of raising.
        if self.x_nc_calendar_id and self.x_nc_calendar_id.nextcloud_base_url:
            config = self.x_nc_calendar_id
            base = config.nextcloud_base_url.rstrip("/")
            if self.x_caldav_href:
                href = self.x_caldav_href
                if href.startswith("http"):
                    caldav_resource_url = href
                else:
                    caldav_resource_url = base + href
            elif self.x_nc_uid:
                # Derive filename from UID (strip @domain)
                filename = self.x_nc_uid.split("@")[0] + ".ics"
                caldav_resource_url = config.caldav_url + filename

        return {
            "action": action,
            "event": {
                "id": self.id,
                "name": self.name,
                "start": self.start.isoformat() if self.start else None,
                "stop": self.stop.isoformat() if self.stop else None,
                "allday": self.allday,
                # CalDAV n'a pas de champ « lien de visioconférence » : le
                # VEVENT ne porte que LOCATION. Les rendez-vous pris via
                # bf_appointment laissent `location` vide et rangent le lien
                # de la salle dans `videocall_location`, qui n'était jamais
                # poussé — l'événement arrivait donc dans Nextcloud sans
                # aucun moyen de rejoindre la rencontre.
                "location": self.location or self.videocall_location or "",
                # DESCRIPTION est du texte brut en RFC 5545 : pousser le champ
                # Html tel quel envoyait des `<p>…</p>` littéraux dans tous les
                # clients d'agenda.
                "description": html2plaintext(self.description or "").strip(),
                "privacy": self.privacy,
                "show_as": self.show_as,
                # STATUS du VEVENT (RFC 5545 §3.8.1.11), quand le champ existe.
                # ⚠️ Lien MOU, comme celui vers `bf_appointment` plus haut :
                # `bf_event_status` appartient à `bf_calendar_invite`, et ce
                # module tourne aussi chez des locataires qui ne l'ont pas. Une
                # dépendance dure ici pour un attribut facultatif du .ics leur
                # imposerait tout un module de courriels d'invitation.
                "status": (
                    self._bf_ics_status()
                    if "bf_event_status" in self._fields
                    else False
                ),
                "attendees": attendees,
                "organizer": organizer,
                "caldav_href": self.x_caldav_href,
                "caldav_etag": self.x_caldav_etag,
                "nc_uid": self.x_nc_uid,
                "caldav_resource_url": caldav_resource_url,
                "calendar_config_id": (
                    self.x_nc_calendar_id.id if self.x_nc_calendar_id else None
                ),
            },
            "calendar_config": (
                {
                    "id": self.x_nc_calendar_id.id,
                    "caldav_url": self.x_nc_calendar_id.caldav_url,
                    "nextcloud_base_url": self.x_nc_calendar_id.nextcloud_base_url,
                    "nextcloud_user": self.x_nc_calendar_id.nextcloud_user,
                }
                if self.x_nc_calendar_id
                else None
            ),
        }

    def _push_via_caldav_enabled(self):
        """Interrupteur du transport de poussée Nextcloud.

        Vrai (défaut) = ``PUT``/``DELETE`` CalDAV depuis Odoo. Faux = ancien
        webhook n8n. C'est un ``ir.config_parameter`` et non un champ pour que
        le repli soit immédiat, sans mise à jour de module ni interruption.
        """
        return (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("calendar_nextcloud_sync.push_via_caldav", "1")
            .strip()
            not in ("0", "false", "False", "")
        )

    def _push_caldav(self, action, event, config):
        """Poussée directe d'UN événement. Rend True si elle a abouti.

        Contrairement au webhook, ``href`` et ``ETag`` sont posés ICI, à la
        seconde de la poussée, au lieu d'attendre le pull suivant. C'est ce qui
        referme la fenêtre pendant laquelle un événement neuf n'avait pas de
        ``x_caldav_href`` et se faisait prendre pour un orphelin.
        """
        backend = self.env["calendar.caldav.backend"]
        if action == "delete":
            url, _href = backend._resource_url(config, event)
            backend.push_delete(config, url=url)
            _logger.info("CalDAV delete for event %s", event.id)
            return True

        href, etag = backend.push(config, event)
        vals = {"x_last_sync": fields.Datetime.now()}
        if href and href != event.x_caldav_href:
            vals["x_caldav_href"] = href
        if etag and etag != event.x_caldav_etag:
            vals["x_caldav_etag"] = etag
        event.with_context(
            skip_nc_sync=True, no_mail_to_attendees=True
        ).write(vals)
        _logger.info("CalDAV %s for event %s (%s)", action, event.id, href)
        return True

    def _trigger_sync_webhook(self, action):
        """Push an event change to Nextcloud.

        Historiquement un POST vers n8n, d'où le nom conservé : six appelants
        pointent dessus. Le transport réel se choisit maintenant dans
        ``_push_via_caldav_enabled`` — CalDAV direct par défaut, webhook en
        repli. Voir ``models/caldav_backend.py`` pour le pourquoi.

        Only fires for configs with backend_type='nextcloud'. Google-backend
        configs use direct API calls via calendar.google.backend (see
        _dispatch_outbound_sync below).

        Args:
            action: 'create', 'update', or 'delete'
        """
        use_caldav = self._push_via_caldav_enabled()
        for event in self:
            # Skip if no calendar config or sync direction doesn't allow it
            if not event.x_nc_calendar_id:
                continue
            config = event.x_nc_calendar_id
            if config.backend_type != "nextcloud":
                continue
            if config.sync_direction == "nc_to_odoo":
                continue

            # Skip create/update for events already sourced from remote
            # (anti-loop). Deletes always propagate — context prevents loops.
            if event.x_sync_source in ("nextcloud", "google") and action != "delete":
                _logger.debug(
                    "Skipping NC sync for event %s (source=%s)",
                    event.id, event.x_sync_source,
                )
                continue

            if use_caldav and config.nextcloud_app_password_encrypted:
                try:
                    self._push_caldav(action, event, config)
                    config.update_sync_status(
                        "success", f"CalDAV {action}: {event.name}"
                    )
                except Exception as e:
                    _logger.error(
                        "CalDAV push %s failed for event %s: %s",
                        action, event.id, e,
                    )
                    config.update_sync_status("error", f"CalDAV {action}: {e}")
                continue

            if not config.webhook_url:
                _logger.warning(
                    "No webhook URL configured for calendar %s", config.name
                )
                continue

            try:
                payload = event._get_sync_payload(action)
                headers = {"Content-Type": "application/json"}
                if config.webhook_secret:
                    headers["X-Webhook-Secret"] = config.webhook_secret

                response = requests.post(
                    config.webhook_url,
                    json=payload,
                    headers=headers,
                    timeout=30,
                )
                response.raise_for_status()
                _logger.info(
                    "Sync webhook triggered for event %s (%s)", event.id, action
                )

                # Update last sync timestamp
                event.with_context(
                    skip_nc_sync=True, no_mail_to_attendees=True
                ).write({"x_last_sync": fields.Datetime.now()})
                config.update_sync_status("success", f"Event {action}: {event.name}")

            except requests.RequestException as e:
                _logger.error(
                    "Failed to trigger sync webhook for event %s: %s", event.id, str(e)
                )
                config.update_sync_status("error", str(e))

    def _trigger_google_sync(self, action):
        """Push event change directly to Google Calendar API.

        Called from create/write/unlink when the event's config uses
        backend_type='google'. Failures are logged but not raised —
        the cron _retry_failed_google_pushes() handles recovery.

        Args:
            action: 'create', 'update', or 'delete'
        """
        GoogleBackend = self.env["calendar.google.backend"]
        for event in self:
            if not event.x_nc_calendar_id:
                continue
            config = event.x_nc_calendar_id
            if config.backend_type != "google":
                continue
            if config.sync_direction == "nc_to_odoo":
                continue
            # Anti-loop: skip push for events just pulled from Google.
            # Deletes still propagate (user-initiated) unless ctx suppresses.
            if event.x_sync_source == "google" and action != "delete":
                continue

            try:
                if action == "create":
                    gid, etag = GoogleBackend.push_create(config, event)
                    event.with_context(
                        skip_nc_sync=True,
                        skip_google_sync=True,
                        no_mail_to_attendees=True,
                    ).write({
                        "x_nc_uid": gid,
                        "x_caldav_etag": etag,
                        "x_sync_source": event.x_sync_source or "odoo",
                        "x_last_sync": fields.Datetime.now(),
                    })
                    config.update_sync_status(
                        "success", f"Google create: {event.name}"
                    )
                elif action == "update":
                    gid, etag = GoogleBackend.push_update(config, event)
                    event.with_context(
                        skip_nc_sync=True,
                        skip_google_sync=True,
                        no_mail_to_attendees=True,
                    ).write({
                        "x_nc_uid": gid,
                        "x_caldav_etag": etag,
                        "x_last_sync": fields.Datetime.now(),
                    })
                    config.update_sync_status(
                        "success", f"Google update: {event.name}"
                    )
                elif action == "delete":
                    GoogleBackend.push_delete(config, event.x_nc_uid)
                    config.update_sync_status(
                        "success", f"Google delete: {event.name}"
                    )
            except Exception as e:
                _logger.error(
                    "Google push %s failed for event %s: %s",
                    action, event.id, e,
                )
                config.update_sync_status("error", f"Google {action}: {e}")

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to trigger sync webhook."""
        # Auto-assign the sync calendar for new events. Route to the calendar
        # OWNED BY THE ORGANIZER (user_id) when one exists, falling back to the
        # global default. Without this, every event lands on the single global
        # default_calendar_id regardless of who the event is for — so an
        # appointment booked for one staff member could sync to a different
        # staff member's calendar (whichever calendar the global default points
        # at). calendar_owner_id maps each sync config to its owner.
        cfg_model = self.env["nextcloud.calendar.sync.config"].sudo()
        default_cal_id = int(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("calendar_nextcloud_sync.default_calendar_id", "0")
        )
        default_cfg = cfg_model.browse(default_cal_id) if default_cal_id else cfg_model
        owner_cal_cache = {}

        def _calendar_for_organizer(organizer_uid):
            if organizer_uid not in owner_cal_cache:
                owned = cfg_model.search(
                    [
                        ("active", "=", True),
                        ("calendar_owner_id", "=", organizer_uid),
                    ]
                )
                # Prefer the global default when the organizer owns it (handles
                # owners with several calendars, e.g. a personal + a
                # non-availability config); otherwise the organizer's own
                # calendar; else fall back to the global default.
                if owned and default_cfg and default_cfg in owned:
                    owner_cal_cache[organizer_uid] = default_cal_id
                elif owned:
                    owner_cal_cache[organizer_uid] = owned[0].id
                else:
                    owner_cal_cache[organizer_uid] = default_cal_id
            return owner_cal_cache[organizer_uid]

        for vals in vals_list:
            if (
                default_cal_id
                and not vals.get("x_nc_calendar_id")
                and not vals.get("x_sync_source")
            ):
                organizer_uid = vals.get("user_id") or self.env.uid
                vals["x_nc_calendar_id"] = _calendar_for_organizer(organizer_uid)

        # Set sync source to 'odoo' if not specified (means created in Odoo)
        # Also inherit color from the calendar config
        nc_config_model = self.env["nextcloud.calendar.sync.config"]
        uid_domain = nc_config_model._get_event_uid_domain()
        color_cache = {}
        for vals in vals_list:
            if "x_sync_source" not in vals and "x_nc_calendar_id" in vals:
                vals["x_sync_source"] = "odoo"
            # Generate NC UID if not provided
            if "x_nc_uid" not in vals and "x_nc_calendar_id" in vals:
                vals["x_nc_uid"] = f"{uuid.uuid4()}@{uid_domain}"
            # Inherit color from calendar config
            cal_id = vals.get("x_nc_calendar_id")
            if cal_id and "color" not in vals:
                if cal_id not in color_cache:
                    config = nc_config_model.browse(cal_id)
                    color_cache[cal_id] = config.odoo_color or 0
                if color_cache[cal_id]:
                    vals["color"] = color_cache[cal_id]

        records = super().create(vals_list)

        # Trigger outbound sync for non-recurring events.
        # Dispatches by backend_type: NC → n8n webhook, Google → direct API.
        if not self.env.context.get("skip_nc_sync"):
            for record in records:
                if not record.x_nc_calendar_id or record.recurrency:
                    continue
                backend = record.x_nc_calendar_id.backend_type
                if backend == "google":
                    if not self.env.context.get("skip_google_sync"):
                        record._trigger_google_sync("create")
                else:
                    record._trigger_sync_webhook("create")

        return records

    def write(self, vals):
        """Override write to trigger sync webhook."""
        # Detect calendar assignment: if x_nc_calendar_id is being set,
        # ensure sync fields are populated before super().write()
        if "x_nc_calendar_id" in vals and vals["x_nc_calendar_id"]:
            config_model = self.env["nextcloud.calendar.sync.config"]
            config = config_model.browse(vals["x_nc_calendar_id"])
            if "color" not in vals and config.odoo_color:
                vals["color"] = config.odoo_color
            uid_domain = config_model._get_event_uid_domain()
            for record in self:
                extras = {}
                if not record.x_nc_uid and "x_nc_uid" not in vals:
                    extras["x_nc_uid"] = f"{uuid.uuid4()}@{uid_domain}"
                if not record.x_sync_source and "x_sync_source" not in vals:
                    extras["x_sync_source"] = "odoo"
                if extras:
                    vals.update(extras)

        result = super().write(vals)

        # If a calendar was just assigned, trigger outbound sync (create).
        if (
            "x_nc_calendar_id" in vals
            and vals["x_nc_calendar_id"]
            and not self.env.context.get("skip_nc_sync")
        ):
            for record in self:
                if not record.x_nc_calendar_id or record.recurrency:
                    continue
                if record.x_sync_source in ("nextcloud", "google"):
                    continue
                backend = record.x_nc_calendar_id.backend_type
                if backend == "google":
                    if not self.env.context.get("skip_google_sync"):
                        record._trigger_google_sync("create")
                else:
                    record._trigger_sync_webhook("create")
            return result

        # Trigger update webhook/API if relevant fields changed
        sync_fields = {
            "name",
            "start",
            "stop",
            "allday",
            "location",
            # Le lien de visioconférence voyage dans LOCATION (voir
            # _get_sync_payload). Sans ce champ ici, un RDV dont la salle est
            # créée juste après l'événement ne repousse jamais son lien.
            "videocall_location",
            "description",
            "privacy",
            "show_as",
            "attendee_ids",
        }
        if not self.env.context.get("skip_nc_sync") and sync_fields & set(vals.keys()):
            for record in self:
                if not record.x_nc_calendar_id or record.recurrency:
                    continue
                if record.x_sync_source in ("nextcloud", "google"):
                    continue
                backend = record.x_nc_calendar_id.backend_type
                if backend == "google":
                    if not self.env.context.get("skip_google_sync"):
                        record._trigger_google_sync("update")
                else:
                    record._trigger_sync_webhook("update")

        return result

    def unlink(self):
        """Override unlink to propagate deletion to remote backends."""
        # Anti-loop: the inverse delete path (from remote → Odoo) sets
        # skip_nc_sync=True (NC) or skip_google_sync=True (Google), so
        # user-initiated deletes of remote-sourced events still propagate.
        if not self.env.context.get("skip_nc_sync"):
            for record in self:
                if not record.x_nc_calendar_id or record.recurrency:
                    continue
                backend = record.x_nc_calendar_id.backend_type
                if backend == "google":
                    if not self.env.context.get("skip_google_sync"):
                        record._trigger_google_sync("delete")
                else:
                    record._trigger_sync_webhook("delete")

        return super().unlink()

    def _delete_recurring_event(self, event):
        """Delete a recurring event, its recurrence, and all instances."""
        ctx = {
            "skip_nc_sync": True,
            "no_mail_to_attendees": True,
            "mail_create_nolog": True,
            "tracking_disable": True,
            "dont_notify": True,
        }
        if event.recurrence_id:
            all_events = event.recurrence_id.calendar_event_ids
            recurrence = event.recurrence_id
            all_events.with_context(**ctx).unlink()
            if recurrence.exists():
                recurrence.with_context(**ctx).unlink()
        else:
            event.with_context(**ctx).unlink()

    def _apply_exdates(self, recurrence, exdates):
        """Delete occurrence instances that match EXDATE dates.

        Args:
            recurrence: calendar.recurrence record
            exdates: list of datetime objects (naive UTC)
        """
        ctx = {
            "skip_nc_sync": True,
            "no_mail_to_attendees": True,
            "mail_create_nolog": True,
            "tracking_disable": True,
            "dont_notify": True,
        }
        to_delete = self.env["calendar.event"]
        for instance in recurrence.calendar_event_ids:
            if instance == recurrence.base_event_id:
                continue
            for exdate in exdates:
                if instance.allday:
                    if instance.start_date == exdate.date():
                        to_delete |= instance
                        break
                else:
                    if abs((instance.start - exdate).total_seconds()) < 60:
                        to_delete |= instance
                        break
        if to_delete:
            to_delete.with_context(**ctx).unlink()

    @api.model
    @api.model
    def _bf_pull_fallback_alarm_minutes(self):
        """Rappel posé d'office sur une rencontre tirée SANS aucun VALARM.

        Le cas visé est étroit et précis : quelqu'un d'autre m'invite, et
        personne n'a posé de rappel pour moi. C'est silencieux par nature, un
        rappel absent ne se remarque jamais.

        ⚠️ Trois bornes, et elles comptent :

        * seulement à la CRÉATION. Sinon, retirer le rappel dans Odoo serait
          défait au pull suivant et on ne pourrait plus jamais s'en défaire ;
        * seulement au-delà d'un participant, donc les vraies rencontres. Sur
          un agenda réel, l'appliquer sans ce filtre a armé des milliers de
          rappels pour des vols, des webinaires et des blocs personnels contre
          une poignée de vraies rencontres ;
        * jamais de réécriture vers Nextcloud. Repousser ce VALARM inventé,
          ce serait une écriture CalDAV par événement, c'est-à-dire un iMIP
          « Invitation updated » à chaque participant.

        ⚠️ Défaut à 0, donc INACTIF tant qu'un locataire n'a pas choisi une
        valeur, alors que le même réglage vaut 15 pour les événements créés
        dans Odoo. L'écart est voulu : poser un défaut sur ce qu'un usager crée
        lui-même est un confort d'interface, inventer un rappel qui n'existe
        dans aucune source pendant une synchronisation doit être un choix
        explicite.

        ⚠️ Le réglage est PARTAGÉ avec `bf_email_management`, qui accepte
        plusieurs délais séparés par des virgules (« 1,15 »). Ce filet-ci n'en
        garde qu'un, le plus GRAND : c'est un filet, pas un jeu de rappels par
        défaut, et un filet posé une minute avant ne rattrape personne. Sans
        cette lecture, un réglage à « 1,15 » ferait échouer la conversion en
        entier et éteindrait le filet en silence.
        """
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "bf_email_management.default_alarm_minutes", "0",
        )
        minutes = []
        for chunk in str(raw or "").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                value = int(chunk)
            except ValueError:
                continue
            if value > 0:
                minutes.append(value)
        return [max(minutes)] if minutes else []

    @api.model
    def _bf_alarm_ids_for_minutes(self, minutes_list):
        """Ids des ``calendar.alarm`` de notification pour ces délais.

        Réutilise une alarme existante quand le délai correspond déjà, et n'en
        crée une que pour un délai inédit. Sans ce rapprochement, chaque pull
        fabriquerait des doublons et la liste des alarmes deviendrait
        illisible dans l'interface.
        """
        Alarm = self.env["calendar.alarm"]
        ids = []
        for minutes in minutes_list:
            alarm = Alarm.search([
                ("alarm_type", "=", "notification"),
                ("duration_minutes", "=", minutes),
            ], limit=1)
            if not alarm:
                alarm = Alarm.create({
                    "name": "%s min avant" % minutes,
                    "alarm_type": "notification",
                    "duration": minutes,
                    "interval": "minutes",
                })
            ids.append(alarm.id)
        return ids

    def create_from_nextcloud(self, data, config_id):
        """Create or update event from Nextcloud webhook data.

        Called by n8n webhook handler via JSON-RPC.

        Args:
            data: dict with event data from Nextcloud/ICS
            config_id: ID of nextcloud.calendar.sync.config

        Returns:
            dict: Result with created/updated event ID
        """
        config = self.env["nextcloud.calendar.sync.config"].browse(config_id)
        if not config.exists():
            return {"error": "Invalid calendar config"}

        # Check sync direction
        if config.sync_direction == "odoo_to_nc":
            return {"error": "Sync direction does not allow NC → Odoo"}

        # Look for existing event by NC UID within this calendar config
        # (scoped to config to avoid ping-pong when the same UID appears
        # in multiple Nextcloud calendars)
        nc_uid = data.get("uid")
        existing = self.search([
            ("x_nc_uid", "=", nc_uid),
            ("x_nc_calendar_id", "=", config_id),
        ], limit=1) if nc_uid else None

        # Prepare values
        vals = {
            "name": data.get("summary", "Untitled Event"),
            "start": data.get("start"),
            "stop": data.get("end"),
            "allday": data.get("allday", False),
            "location": data.get("location", ""),
            "description": data.get("description", ""),
            "x_caldav_etag": data.get("etag"),
            "x_caldav_href": data.get("href"),
            "x_nc_uid": nc_uid,
            "x_nc_calendar_id": config_id,
            "x_sync_source": "nextcloud",
            "x_last_sync": fields.Datetime.now(),
            "color": config.odoo_color or 0,
        }

        # Handle attendees — always include the calendar owner so synced
        # events appear in the main Calendar view (filters by partner_ids).
        # Priority: config.calendar_owner_id > NC username lookup > env.user.
        partner_ids = set()
        owner_partner = None
        if config.calendar_owner_id:
            owner_partner = config.calendar_owner_id.partner_id
        elif config.nextcloud_user:
            owner_user = self.env["res.users"].search(
                [("login", "=like", config.nextcloud_user + "%")], limit=1
            )
            if owner_user:
                owner_partner = owner_user.partner_id
        if not owner_partner:
            owner_partner = self.env.user.partner_id
        if owner_partner:
            partner_ids.add(owner_partner.id)

        attendee_emails = data.get("attendees", [])
        for email in attendee_emails:
            partner = self.env["res.partner"].search(
                [("email", "=ilike", email)], limit=1
            )
            if partner:
                partner_ids.add(partner.id)
            else:
                _logger.warning(
                    "Attendee email %s not found in Odoo partners", email
                )
        if partner_ids:
            # 🔴 L'aller-retour était ASYMÉTRIQUE. `_get_sync_payload` pousse
            # volontairement les événements de réservation SANS participants
            # (sinon le greffon de planification de Nextcloud émet une seconde
            # invitation iMIP, en concurrence avec notre confirmation brandée —
            # voir son commentaire). La copie Nextcloud n'en porte donc jamais,
            # et un `(6, 0, …)` au retour ne peut que les PERDRE : l'événement
            # se réduit au propriétaire du calendrier.
            #
            # Mesuré en production sur une réservation : sept lignes
            # `calendar.attendee` créées puis détruites, un seul participant
            # survivant. L'agenda ne disait plus qui était attendu, et le
            # bouton d'invitation d'Odoo ne s'adressait à personne.
            #
            # Pour ces événements l'ingestion est donc ADDITIVE : elle complète,
            # elle ne remplace pas. Retirer quelqu'un se fait dans Odoo, qui est
            # la source pour eux. Partout ailleurs, Nextcloud reste la source et
            # le remplacement est le bon comportement.
            if existing and existing._bf_odoo_owns_attendees():
                nouveaux = set(partner_ids) - set(existing.partner_ids.ids)
                if nouveaux:
                    vals["partner_ids"] = [(4, pid, 0) for pid in nouveaux]
            else:
                vals["partner_ids"] = [(6, 0, list(partner_ids))]

        # Statut de la rencontre (STATUS du VEVENT). Écrit seulement quand le
        # .ics en porte un ET que le champ existe dans cette base : un .ics
        # muet laisse Odoo tel quel, plutôt que de remettre « Confirmé » sur
        # une rencontre qu'on venait de marquer annulée dans Odoo.
        ics_status = data.get("status")
        if ics_status and "bf_event_status" in self._fields:
            mapped = self._bf_status_from_ics(ics_status)
            if mapped:
                vals["bf_event_status"] = mapped

        # Rappels. Le .ics est la source de vérité : ce que l'usager a posé
        # dans son client d'agenda doit exister dans Odoo, sinon la chaîne de
        # rappel d'Odoo (ntfy) n'a rien sur quoi se déclencher.
        alarm_minutes = data.get("alarms")
        if alarm_minutes is not None:
            if not alarm_minutes and not existing and len(partner_ids) > 1:
                alarm_minutes = self._bf_pull_fallback_alarm_minutes()
            vals["alarm_ids"] = [
                (6, 0, self._bf_alarm_ids_for_minutes(alarm_minutes))
            ]

        # Use context to suppress all email/notification side-effects
        ctx = {
            "no_mail_to_attendees": True,
            "skip_nc_sync": True,
            "mail_create_nolog": True,
            "tracking_disable": True,
            "dont_notify": True,
        }

        rrule = data.get("rrule")
        if rrule:
            # === Recurring event path ===
            # Delete old event + recurrence + instances if it exists
            if existing:
                self._delete_recurring_event(existing)

            # Create recurring event — Odoo auto-creates calendar.recurrence
            # and generates occurrence instances via _apply_recurrence()
            vals["recurrency"] = True
            vals["rrule"] = rrule
            if data.get("event_tz"):
                vals["event_tz"] = data["event_tz"]

            event = self.with_context(**ctx).create(vals)

            # Post-process: set sync fields on generated instances
            # (copy=False fields aren't inherited by _apply_recurrence)
            # Explicitly set x_nc_uid=False to prevent write override from
            # generating random UIDs — instances must NOT have x_nc_uid so
            # orphan detection doesn't treat them as stale NC events.
            # Also set partner_ids so instances appear in the calendar view
            # (Odoo's _apply_recurrence may not copy the organizer partner).
            if event.recurrence_id:
                instances = event.recurrence_id.calendar_event_ids - event
                if instances:
                    instance_vals = {
                        "x_nc_calendar_id": config_id,
                        "x_sync_source": "nextcloud",
                        "x_nc_uid": False,
                    }
                    if partner_ids:
                        instance_vals["partner_ids"] = [
                            (4, pid) for pid in partner_ids
                        ]
                    instances.with_context(**ctx).write(instance_vals)

            # Handle EXDATE: delete instances matching excluded dates
            exdates = data.get("exdates", [])
            if exdates and event.recurrence_id:
                self._apply_exdates(event.recurrence_id, exdates)

            config.update_sync_status("success", f"Created recurring: {vals['name']}")
            return {"id": event.id, "action": "created"}

        # === Non-recurring event path (existing behavior) ===
        if existing:
            existing.with_context(**ctx).write(vals)
            config.update_sync_status("success", f"Updated: {vals['name']}")
            return {"id": existing.id, "action": "updated"}
        else:
            event = self.with_context(**ctx).create(vals)
            config.update_sync_status("success", f"Created: {vals['name']}")
            return {"id": event.id, "action": "created"}

    @api.model
    def delete_from_nextcloud(self, nc_uid, config_id):
        """Delete event that was deleted in Nextcloud.

        Args:
            nc_uid: Nextcloud UID of the event
            config_id: ID of nextcloud.calendar.sync.config

        Returns:
            dict: Result
        """
        config = self.env["nextcloud.calendar.sync.config"].browse(config_id)
        if not config.exists():
            return {"error": "Invalid calendar config"}

        if config.sync_direction == "odoo_to_nc":
            return {"error": "Sync direction does not allow NC → Odoo"}

        event = self.search([
            ("x_nc_uid", "=", nc_uid),
            ("x_nc_calendar_id", "=", config_id),
        ], limit=1)
        if event:
            event_name = event.name
            if event.recurrence_id:
                # Delete entire recurrence and all instances
                self._delete_recurring_event(event)
            else:
                event.with_context(
                    skip_nc_sync=True,
                    no_mail_to_attendees=True,
                    mail_create_nolog=True,
                    tracking_disable=True,
                    dont_notify=True,
                ).unlink()
            config.update_sync_status("success", f"Deleted: {event_name}")
            return {"deleted": True, "name": event_name}

        return {"deleted": False, "reason": "Event not found"}

    @api.model
    def update_etag_from_nextcloud(self, event_id, etag, href=None):
        """Update ETag after successful sync to Nextcloud.

        Called by n8n after PUT to Nextcloud succeeds.

        Args:
            event_id: Odoo event ID
            etag: New ETag from Nextcloud response
            href: Optional new HREF if event was created
        """
        event = self.browse(event_id)
        if event.exists():
            vals = {
                "x_caldav_etag": etag,
                "x_last_sync": fields.Datetime.now(),
            }
            if href:
                vals["x_caldav_href"] = href
            event.with_context(skip_nc_sync=True).write(vals)
            return {"success": True}
        return {"success": False, "reason": "Event not found"}
