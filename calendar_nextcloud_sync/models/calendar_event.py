# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).

import hashlib
import json
import logging
import unicodedata
import uuid
from datetime import timedelta

import requests

from odoo import _, api, fields, models
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
    # Ce qui identifie une occurrence de série dans Nextcloud, c'est
    # l'heure que la règle lui donne (RECURRENCE-ID), pas son heure actuelle :
    # une fois déplacée, Odoo ne la gardait nulle part.
    x_nc_recurrence_id = fields.Datetime(
        string="Original occurrence time",
        copy=False,
        readonly=True,
        help="For an occurrence of a series synced with Nextcloud: the time "
             "the series rule gives it. It identifies the occurrence in "
             "Nextcloud (RECURRENCE-ID) even after it has been moved.",
    )
    # Empreinte du VEVENT maître tel que Nextcloud l'a donné à la dernière
    # lecture. Comparer la série lue à son PORTEUR dans Odoo (la première
    # occurrence) la faisait passer pour changée dès que cette occurrence était
    # renommée ou annulée seule : rasée à chaque passe.
    x_nc_serie_sig = fields.Char(
        string="Nextcloud series fingerprint",
        copy=False,
        readonly=True,
        help="Fingerprint of the series as Nextcloud last gave it, to recognise "
             "an unchanged series on the next pull instead of recreating it.",
    )

    @api.model
    def _normalize_caldav_etag(self, etag):
        """Rendre un ETag comparable, quelle que soit la porte par laquelle il entre.

        Un serveur CalDAV rend son ETag **entre guillemets** (RFC 9110 §8.8.3),
        et c'est cette forme-là que reçoit quiconque lit l'en-tête `ETag` d'une
        réponse HTTP. La passe de lecture, elle, analyse `<d:getetag>` et retire
        les guillemets. Les deux écritures du MÊME ETag ne pouvaient donc jamais
        se comparer égales.

        Ce n'est pas cosmétique : l'événement dont l'etag stocké porte ses
        guillemets rate le saut des événements inchangés à CHAQUE passe, se fait
        réécrire pour rien, et si cette réécriture échoue (une contrainte qui
        porte sur un enregistrement à moitié écrit, par exemple), le savepoint
        annule l'etag avec le reste : la boucle ne se dénoue jamais. Mesuré le
        2026-09-11 sur une base réelle, UN événement sur 841 suffisait à tenir
        tout l'agenda en passe complète.

        Normaliser aux deux bouts — à l'écriture et à la comparaison — répare
        aussi les etags déjà stockés sous leur forme citée, sans migration.
        """
        if not etag:
            return etag
        etag = etag.strip()
        if etag[:2].upper() == "W/":  # validateur faible, RFC 9110 §8.8.3
            etag = etag[2:]
        return etag.strip('"')

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
            # La même adresse que la poussée directe, et la même garde : n8n
            # écrit à cette URL avec les identifiants de la configuration.
            caldav_resource_url, _href = self.env["calendar.caldav.backend"] \
                ._resource_url(self.x_nc_calendar_id, self)

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
                # CATEGORIES du VEVENT (RFC 5545 §3.8.1.2) : les étiquettes
                # d'agenda, par leur nom.
                "categories": self.categ_ids.mapped("name"),
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
            backend.push_delete(config, event=event, url=url)
            _logger.info("CalDAV delete for event %s", event.id)
            return True

        href, etag = backend.push(config, event)
        etag = self._normalize_caldav_etag(etag)
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
            # sudo : le secret est réservé aux administrateurs et l'état de
            # synchronisation s'écrit sur la configuration, que l'usager
            # ordinaire ne peut que lire.
            config = event.x_nc_calendar_id.sudo()
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
                webhook_secret = config._get_webhook_secret()
                if webhook_secret:
                    headers["X-Webhook-Secret"] = webhook_secret

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
            # sudo : même règle que la poussée Nextcloud — l'état de
            # synchronisation s'écrit sur la configuration, que l'usager
            # ordinaire ne peut que lire.
            config = event.x_nc_calendar_id.sudo()
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
        # Lu AVANT super() : le cœur retire `recurrence_update` du
        # dictionnaire, et l'heure d'origine d'une occurrence n'est plus
        # lisible une fois qu'elle est déplacée.
        reglage = vals.get("recurrence_update")
        # Rendre privée ou
        # ré-étiqueter une série « pour tous les événements » ne touche ni ses
        # heures ni sa règle : CLASS et CATEGORIES se retouchent alors dans
        # chaque VEVENT de la ressource, au lieu de marquer la série « à
        # recréer » (qui perdait la confidentialité à la lecture suivante).
        #
        # Second tour : une seule fiche, comme le cœur (qui ne réécrit la série
        # que pour `len(self) == 1`), et les valeurs prises sur l'événement
        # ÉCRIT, le porteur ne donnant que l'adresse (C2) ; poussée CalDAV
        # éteinte = ancien chemin (C9) ; écriture mixte = la série est marquée
        # comme avant ET CLASS/CATEGORIES partent quand même (C10), pour que la
        # recréation suivante les relise.
        champs = set(vals) - {"recurrence_update"}
        classe_de_serie = []
        # Troisième tour, B1 : une heure ou la règle dans la même écriture fait
        # passer le cœur par `_rewrite_recurrence`, qui archive la série et
        # n'écrit que sur sa base. L'occurrence écrite garde alors son ANCIENNE
        # confidentialité, qui partirait dans chaque VEVENT. Dans ce cas, rien
        # ne part : la série est seulement marquée « à recréer », comme avant.
        champs_de_serie = self._get_time_fields() | self._get_recurrent_fields() \
            | {"recurrency", "active"}
        if reglage == "all_events" and len(self) == 1 and self.recurrence_id \
                and champs & {"privacy", "categ_ids"} and not champs & champs_de_serie \
                and not self.env.context.get("skip_nc_sync") and self._push_via_caldav_enabled():
            porteur = self._bf_nc_porteur()
            if porteur and porteur._bf_nc_config_pousse():
                props = {self._BF_NC_RETOUCHE[c] for c in champs & {"privacy", "categ_ids"}}
                classe_de_serie = [(self, porteur, props)]
                if champs <= {"privacy", "categ_ids"}:
                    self = self.with_context(bf_nc_reecriture_serie=True)
                    reglage = "classe_de_serie"
        if reglage in ("all_events", "future_events"):
            # Le cœur réécrit alors la série par des écritures et des
            # suppressions internes, occurrence par occurrence, sans
            # `recurrence_update`. Sans ce drapeau, chacune passerait pour une
            # modification d'UNE occurrence : une exception ou un EXDATE par
            # occurrence dans Nextcloud. Réécrire une série depuis Odoo n'est
            # pas pris en charge ; rien ne part, comme avant la 2.25.
            self = self.with_context(bf_nc_reecriture_serie=True)
            # Rien ne part vers Nextcloud, comme avant ; mais le porteur est
            # marqué « à recréer », pour que la lecture suivante RECRÉE la série
            # depuis Nextcloud au lieu de garder pour toujours la version
            # d'Odoo. C'est ce que faisait la 2.24, qui
            # rasait à chaque lecture. 🔴 Une marque, pas une empreinte vide :
            # vide, la comparaison des séries d'avant la 2.25 prenait le relais
            # et, l'heure d'origine du porteur étant restée l'ancienne, jugeait
            # inchangée une série qu'Odoo avait déplacée.
            porteurs = self.filtered("recurrence_id").mapped(lambda e: e._bf_nc_porteur())
            porteurs.with_context(skip_nc_sync=True).write(
                {"x_nc_serie_sig": self._BF_NC_SERIE_A_RECREER})
            # Et l'usager le sait : la série d'Odoo diverge de Nextcloud jusqu'à
            # ce qu'elle y change, puis revient d'un coup.
            for porteur in porteurs.filtered(lambda p: p._bf_nc_config_pousse()):
                self._bf_nc_avertir_serie(porteur.name or "")
        retouches = self._bf_nc_retouches_a_faire(vals, reglage)
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
            # le statut seul ne repartait pas (lien mou vers
            # bf_calendar_invite, un nom de champ absent ne coûte rien), ni le
            # jour d'une rencontre « toute la journée » déplacée.
            "bf_event_status",
            "start_date",
            "stop_date",
            # les étiquettes partent en CATEGORIES.
            "categ_ids",
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

        if retouches:
            self._bf_nc_pousser_retouches(retouches)
        if classe_de_serie:
            self._bf_nc_pousser_classe_de_serie(classe_de_serie)
        return result

    def _bf_nc_pousser_classe_de_serie(self, series):
        """CLASS / CATEGORIES d'une série entière vers Nextcloud (voir write())."""
        backend = self.env["calendar.caldav.backend"]
        ctx = {"skip_nc_sync": True, "no_mail_to_attendees": True}
        for source, porteur, props in series:
            # Une source archivée (série réécrite par le cœur) porte des valeurs
            # périmées : ne rien pousser.
            if not (source.exists() and porteur.exists() and source.active):
                continue
            config = porteur._bf_nc_config_pousse()
            try:
                url, _href = backend._resource_url(config, porteur)
                etag = backend._retoucher_serie_classe(config, url, source, props)
                tenue = {"x_last_sync": fields.Datetime.now()}
                if etag:
                    tenue["x_caldav_etag"] = self._normalize_caldav_etag(etag)
                porteur.with_context(**ctx).write(tenue)
                config.update_sync_status("success", f"CalDAV update: {porteur.name}")
            except Exception as e:  # noqa: BLE001 (journalisé et dit à l'usager)
                _logger.error("Retouche CalDAV de la série %s en échec : %s", porteur.id, e)
                config.update_sync_status("error", f"CalDAV update: {e}")
                porteur._bf_nc_avertir_echec()

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

        exclusions = self._bf_nc_exclusions_a_faire()
        result = super().unlink()
        if exclusions:
            self._bf_nc_pousser_exclusions(exclusions)
        return result

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
        # appariées par leur heure D'ORIGINE (une occurrence
        # déplacée garde la date que l'EXDATE désigne), et l'événement de base
        # n'est plus épargné. Il l'était parce qu'il porte l'adresse de la
        # série ; il la passe maintenant à la première occurrence qui reste, et
        # la date exclue ne revient plus à chaque lecture.
        for instance in recurrence.calendar_event_ids:
            for exdate in exdates:
                if instance._bf_nc_correspond(exdate, instance.allday):
                    to_delete |= instance
                    break
        if to_delete:
            for porteur in to_delete.filtered("x_caldav_href"):
                heritier = (recurrence.calendar_event_ids - to_delete) \
                    .filtered("active").sorted("start")[:1]
                if heritier:
                    self._bf_nc_transferer_porteur(porteur, heritier)
                else:
                    # Toutes les dates exclues : le porteur reste, comme
                    # l'événement de base avant la 2.25. Sans lui, la lecture
                    # suivante recréerait la série pour la supprimer encore.
                    to_delete -= porteur
            self._bf_nc_retirer_exclues(to_delete, ctx)

    def _bf_nc_archivee_revient(self, data):
        """L'archivée que Nextcloud rend doit-elle revenir ? Non, par défaut.

        Crochet (2.27.0) : `bf_calendar_trash` répond oui pour une rencontre
        mise à la corbeille parce qu'elle avait été supprimée dans Nextcloud,
        et qu'on vient d'y restaurer. Archivée dans Odoo, elle reste archivée.
        """
        return False

    def _bf_nc_retirer_exclues(self, occurrences, ctx):
        """Retire les occurrences qu'une EXDATE de Nextcloud désigne.

        Crochet (2.27.0) : `bf_calendar_trash` les met à la corbeille quand
        l'exclusion est neuve (`bf_nc_exclusion_neuve` : série inchangée, donc
        quelqu'un vient de supprimer cette date dans Nextcloud). Une série
        recréée réapplique ses exclusions à des occurrences qu'elle vient de
        générer : ce ménage-là reste une vraie suppression.
        """
        occurrences.with_context(**ctx).unlink()

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

        # 🔴 Une rencontre ARCHIVÉE dans Odoo n'est pas une rencontre inconnue.
        # La recherche ci-dessus ne voit pas les archivées : tant que Nextcloud
        # gardait l'événement, la lecture suivante le recréait, neuf, actif et
        # « occupé » (mesuré en production : une rencontre annulée puis
        # archivée revenait quelques secondes plus tard). Le connecteur
        # Google faisait déjà la bonne recherche. L'archivée reste archivée :
        # seule l'adresse de la ressource est tenue à jour.
        if nc_uid and not existing:
            archivee = self.with_context(active_test=False).search([
                ("x_nc_uid", "=", nc_uid),
                ("x_nc_calendar_id", "=", config_id),
                ("active", "=", False),
            ], order="id desc", limit=1)
            if archivee and archivee._bf_nc_archivee_revient(data):
                # Supprimée DANS Nextcloud, puis restaurée là-bas : elle
                # revient ici aussi, et la lecture continue sur elle.
                existing = archivee
            elif archivee:
                tenue = {}
                etag = self._normalize_caldav_etag(data.get("etag")) if data.get("etag") else False
                if etag and etag != self._normalize_caldav_etag(archivee.x_caldav_etag):
                    tenue["x_caldav_etag"] = etag
                if data.get("href") and data["href"] != archivee.x_caldav_href:
                    tenue["x_caldav_href"] = data["href"]
                if tenue:
                    archivee.with_context(
                        skip_nc_sync=True, no_mail_to_attendees=True,
                        tracking_disable=True, mail_create_nolog=True,
                    ).write(tenue)
                return {"id": archivee.id, "action": "skipped"}

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

        # Confidentialité (CLASS) et étiquettes (CATEGORIES), même règle que
        # STATUS : un .ics muet laisse Odoo tel quel.
        vals.update(self._bf_nc_vals_classe_categories(data, existing))

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
        #
        # ⚠️ `bf_remote_change` fait exception, et c'est tout le sujet de
        # l'avis de modification de `bf_calendar_invite`. Les quatre autres clés éteignent les effets de bord
        # d'Odoo parce que l'écriture n'est pas une décision d'ici : elle
        # constate ce qu'un autre agenda a déjà fait. Mais tant que Nextcloud
        # écrivait aux invités, ce silence était complet par accident ; le jour
        # où c'est Odoo qui parle, il ne reste plus personne pour le dire.
        #
        # Lien MOU, exprès : `calendar_nextcloud_sync` ne dépend pas de
        # `bf_calendar_invite`. Une clé de contexte qu'un locataire sans ce
        # module ne lit pas ne coûte rien ; une dépendance dure lui imposerait
        # toute une famille de courriels d'invitation.
        ctx = {
            "no_mail_to_attendees": True,
            "skip_nc_sync": True,
            "mail_create_nolog": True,
            "tracking_disable": True,
            "dont_notify": True,
            "bf_remote_change": True,
        }

        rrule = data.get("rrule")
        if rrule:
            overrides = data.get("overrides") or []
            # === Série inchangée : la garder en place ===
            # Raser et recréer la série à chaque passage changeait l'id de
            # TOUTES ses occurrences : un OdJ ou une plage retenue rattachés à
            # l'une d'elles perdaient leur lien, et notre propre retouche d'une
            # occurrence, revenue par la lecture suivante, aurait suffi à le
            # provoquer. Quand rien de la série n'a bougé, on ne touche qu'aux
            # dates exclues et aux exceptions.
            if existing and existing.recurrence_id and \
                    existing._bf_nc_serie_inchangee(vals, rrule, data):
                tenue = {cle: vals[cle] for cle in
                         ("x_caldav_etag", "x_caldav_href", "x_last_sync") if cle in vals}
                signature = self._bf_nc_signature(data)
                if existing.x_nc_serie_sig != signature:
                    tenue["x_nc_serie_sig"] = signature
                existing.with_context(**ctx).write(tenue)
                recurrence = existing.recurrence_id
                self._bf_nc_estampiller(recurrence)
                if data.get("exdates"):
                    # Série inchangée : une date exclue qui a encore son
                    # occurrence vient d'être supprimée DANS Nextcloud, par
                    # quelqu'un. Dit au crochet (bf_calendar_trash).
                    self.with_context(bf_nc_exclusion_neuve=True)._apply_exdates(
                        recurrence, data["exdates"])
                self._bf_nc_appliquer_exceptions(recurrence, overrides, ctx)
                # CLASS et CATEGORIES n'entrent PAS dans l'empreinte : les y
                # ajouter changeait celle de toutes les séries déjà lues, qui
                # auraient été rasées et recréées au passage suivant (OdJ et
                # plages retenues perdus). Elles se posent sur place.
                self._bf_nc_classe_serie(recurrence, data, overrides, ctx)
                config.update_sync_status("success", f"Unchanged series: {vals['name']}")
                # Le porteur a pu passer la main si sa date vient d'être exclue.
                porteur = recurrence.with_context(active_test=False) \
                    .calendar_event_ids.filtered("x_caldav_href")[:1]
                return {"id": (porteur or existing).id, "action": "updated"}

            # === Recurring event path ===
            # Une propriété absente du maître ne change rien, même quand la
            # série est recréée : sans ce report, une série rendue privée dans
            # Odoo redevenait publique à la recréation (webhook n8n sans ces
            # clés compris).
            if existing:
                # La valeur d'une occurrence qui suit la règle, pas celle du
                # porteur : il peut répondre à une exception.
                modele = (existing.recurrence_id.calendar_event_ids.filtered(
                    "follow_recurrence").sorted("start")[:1] or existing) \
                    if existing.recurrence_id else existing
                if data.get("classe") is None and modele.privacy:
                    vals["privacy"] = modele.privacy
                if data.get("categories") is None and modele.categ_ids:
                    vals["categ_ids"] = [(6, 0, modele.categ_ids.ids)]
            # Delete old event + recurrence + instances if it exists
            if existing:
                self._delete_recurring_event(existing)

            # Create recurring event — Odoo auto-creates calendar.recurrence
            # and generates occurrence instances via _apply_recurrence()
            vals["recurrency"] = True
            vals["rrule"] = rrule
            vals["x_nc_serie_sig"] = self._bf_nc_signature(data)
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

            # 🔴 La série est tenue par sa récurrence, pas par `event` : un
            # EXDATE sur le premier jour supprime `event` lui-même (son adresse
            # passe à l'occurrence suivante), et le relire levait MissingError.
            # La série n'entrait alors jamais dans Odoo.
            recurrence = event.recurrence_id
            if recurrence:
                self._bf_nc_estampiller(recurrence)

                # Handle EXDATE: delete instances matching excluded dates
                exdates = data.get("exdates", [])
                if exdates:
                    self._apply_exdates(recurrence, exdates)

                self._bf_nc_appliquer_exceptions(recurrence, overrides, ctx)
                self._bf_nc_classe_serie(recurrence, data, overrides, ctx)
                porteur = recurrence.with_context(active_test=False) \
                    .calendar_event_ids.filtered("x_caldav_href")[:1]
                if porteur:
                    event = porteur

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
                # n8n rappelle cette méthode avec l'en-tête `ETag` verbatim,
                # donc ENTRE GUILLEMETS. C'est par ici que la forme citée
                # entrait en base et rendait l'événement incomparable.
                "x_caldav_etag": self._normalize_caldav_etag(etag),
                "x_last_sync": fields.Datetime.now(),
            }
            if href:
                vals["x_caldav_href"] = href
            event.with_context(skip_nc_sync=True).write(vals)
            return {"success": True}
        return {"success": False, "reason": "Event not found"}

    # ------------------------------------------------------------------
    # Ce qu'Odoo modifie sur un événement déjà dans Nextcloud
    #
    # Jusqu'à la 2.24, un événement passé une fois par la lecture
    # (`x_sync_source = nextcloud`, ce que la lecture écrit AUSSI sur ce
    # qu'Odoo a créé) n'était plus jamais repoussé : déplacé, renommé ou
    # annulé dans Odoo, il restait tel quel dans Nextcloud. Une occurrence de
    # série ne l'était jamais. Ces chemins RETOUCHENT la ressource (heure,
    # titre, statut) sans la réécrire depuis les champs d'Odoo, qui ne
    # connaissent ni ses alarmes de téléphone ni ses propriétés X-.
    # ------------------------------------------------------------------

    # Champ écrit dans Odoo -> propriété retouchée dans Nextcloud.
    # Jamais une empreinte (sha1 en hexadécimal) : la série qui la porte est
    # recréée depuis Nextcloud à la lecture suivante.
    _BF_NC_SERIE_A_RECREER = "a-recreer"

    _BF_NC_RETOUCHE = {
        "start": "DATES", "stop": "DATES", "start_date": "DATES",
        "stop_date": "DATES", "allday": "DATES",
        "name": "SUMMARY", "bf_event_status": "STATUS", "show_as": "TRANSP",
        # Jusqu'à la 2.25, rendre privé dans Odoo un
        # événement venu de Nextcloud n'y changeait rien : il restait lisible
        # par tous ceux avec qui l'agenda est partagé.
        "privacy": "CLASS", "categ_ids": "CATEGORIES",
    }

    def _bf_nc_porteur(self):
        """L'événement qui porte la ressource Nextcloud de la série.

        Pas forcément `base_event_id` : le cœur en désigne un autre quand le
        premier disparaît, et seul celui qui a reçu le href de la lecture sait
        où vit la série.
        """
        self.ensure_one()
        if not self.recurrence_id:
            return self.browse()
        return self.recurrence_id.with_context(active_test=False) \
            .calendar_event_ids.filtered("x_caldav_href")[:1]

    def _bf_nc_origine(self, avant=None):
        """Heure d'origine de l'occurrence dans sa série (UTC naïf), ou False.

        ``avant`` : son heure juste avant une écriture, quand elle suivait
        encore la règle ; c'est alors son heure d'origine.
        """
        self.ensure_one()
        if self.x_nc_recurrence_id:
            return self.x_nc_recurrence_id
        if avant:
            return avant
        if self.follow_recurrence:
            return self.start
        # Détachée avant la 2.25 : son heure d'origine n'a été gardée nulle
        # part, sauf par le suivi des modifications (la lecture écrit sans
        # suivi, donc le premier changement tracé part de l'heure de la règle).
        premier = self.env["mail.tracking.value"].sudo().search([
            ("field_id.model", "=", "calendar.event"),
            ("field_id.name", "=", "start"),
            ("mail_message_id.model", "=", "calendar.event"),
            ("mail_message_id.res_id", "=", self.id),
        ], order="id asc", limit=1)
        if premier.old_value_datetime:
            return premier.old_value_datetime
        return self._bf_nc_origine_par_la_regle()

    def _bf_nc_origine_par_la_regle(self):
        """La date de la règle qu'aucune autre occurrence n'occupe, la plus proche.

        Dernier repli, pour une occurrence détachée sans heure d'origine ni
        suivi (une sur trois sur une base réelle). False si la règle ne
        laisse aucune date libre, ou si l'occurrence était la première.
        """
        self.ensure_one()
        recurrence = self.recurrence_id
        autres = recurrence.with_context(active_test=False).calendar_event_ids - self
        prises = set()
        for autre in autres:
            origine = autre.x_nc_recurrence_id or (autre.start if autre.follow_recurrence else False)
            if origine:
                prises.add(fields.Datetime.to_datetime(origine).replace(second=0, microsecond=0))
        if not prises:
            return False
        limite = max(max(prises), fields.Datetime.to_datetime(self.start)) + timedelta(days=400)
        libres = []
        try:
            for i, date in enumerate(recurrence._get_occurrences(min(prises))):
                date = date.replace(tzinfo=None, second=0, microsecond=0) \
                    if hasattr(date, "tzinfo") else date
                if date > limite or i > 1500:
                    break
                if date not in prises:
                    libres.append(date)
        except Exception:  # noqa: BLE001 (règle illisible : pas d'heure d'origine)
            return False
        if not libres:
            return False
        return min(libres, key=lambda d: abs((d - self.start).total_seconds()))

    def _bf_nc_config_pousse(self):
        """La configuration Nextcloud vers laquelle pousser, ou rien."""
        self.ensure_one()
        config = self.x_nc_calendar_id.sudo()
        if not config or config.backend_type != "nextcloud":
            return config.browse()
        if config.sync_direction == "nc_to_odoo":
            return config.browse()
        if not config.nextcloud_app_password_encrypted:
            return config.browse()
        return config

    def _bf_nc_retouches_a_faire(self, vals, reglage):
        """``[(événement, heure d'origine ou None, propriétés)]`` à retoucher après l'écriture."""
        if self.env.context.get("skip_nc_sync") or self.env.context.get("bf_nc_reecriture_serie"):
            return []
        props = {self._BF_NC_RETOUCHE[cle] for cle in vals if cle in self._BF_NC_RETOUCHE}
        if not props:
            return []
        # bf_calendar_invite couple statut et disponibilité dans son propre
        # write() ; selon l'ordre de chargement, ce write-ci ne voit pas le
        # `show_as` qu'il ajoute. TRANSP part alors seulement si la
        # disponibilité a VRAIMENT changé : la lecture n'importe pas TRANSP, et
        # un événement « libre » dans Nextcloud, lu « occupé » dans Odoo,
        # devenait occupé au premier changement de statut.
        guetter_dispo = "STATUS" in props and "TRANSP" not in props
        # Réécrire « cette occurrence et les suivantes » ou « toute la série »
        # depuis Odoo n'est pas pris en charge : la règle d'Odoo ne se traduit
        # pas en celle de Nextcloud sans perte. Rien ne part, comme avant.
        if reglage in ("all_events", "future_events"):
            return []
        retouches = []
        for event in self:
            if event.recurrence_id:
                porteur = event._bf_nc_porteur()
                # La configuration du PORTEUR : c'est lui qui dit où vit la
                # série, et une occurrence peut en porter une autre après une
                # réécriture de la série dans Odoo.
                if not porteur or not porteur._bf_nc_config_pousse():
                    continue  # série jamais vue dans Nextcloud
                avant = event.start if event.follow_recurrence else None
                retouches.append((event, event._bf_nc_origine(avant=avant), props,
                                  event.show_as if guetter_dispo else None))
            elif event._bf_nc_config_pousse() and event.x_sync_source == "nextcloud" \
                    and event.x_caldav_href:
                # Un événement encore « odoo » passe par la poussée complète
                # de write(), comme avant.
                retouches.append((event, None, props,
                                  event.show_as if guetter_dispo else None))
        return retouches

    def _bf_nc_pousser_retouches(self, retouches):
        if not self._push_via_caldav_enabled():
            _logger.info("Retouches Nextcloud ignorées : poussée CalDAV éteinte")
            return
        backend = self.env["calendar.caldav.backend"]
        maintenant = fields.Datetime.now()
        ctx = {"skip_nc_sync": True, "no_mail_to_attendees": True}
        for event, origine, props, dispo_avant in retouches:
            if not event.exists():
                continue
            if dispo_avant is not None and event.show_as != dispo_avant:
                props = props | {"TRANSP"}
            porteur = event._bf_nc_porteur() if event.recurrence_id else event
            config = porteur._bf_nc_config_pousse() if porteur else event._bf_nc_config_pousse()
            if not config:
                continue
            try:
                if event.recurrence_id:
                    if not origine:
                        raise ValueError(
                            "heure d'origine de l'occurrence introuvable")
                    url, _href = backend._resource_url(config, porteur)
                    etag = backend._retoucher_occurrence(
                        config, url, event, origine, event.allday, props)
                    tenue = {"x_last_sync": maintenant}
                    if etag:
                        tenue["x_caldav_etag"] = self._normalize_caldav_etag(etag)
                    porteur.with_context(**ctx).write(tenue)
                    if not event.x_nc_recurrence_id:
                        event.with_context(**ctx).write({"x_nc_recurrence_id": origine})
                else:
                    url, _href = backend._resource_url(config, event)
                    etag = backend._retoucher_evenement(config, url, event, props)
                    tenue = {"x_last_sync": maintenant}
                    if etag:
                        tenue["x_caldav_etag"] = self._normalize_caldav_etag(etag)
                    event.with_context(**ctx).write(tenue)
                config.update_sync_status("success", f"CalDAV update: {event.name}")
            except Exception as e:  # noqa: BLE001 (journalisé et dit à l'usager)
                _logger.error("Retouche CalDAV de l'événement %s en échec : %s", event.id, e)
                config.update_sync_status("error", f"CalDAV update: {e}")
                event._bf_nc_avertir_echec()

    def _bf_nc_exclusions_a_faire(self):
        """``[(config, url, heure d'origine, journée, titre)]`` des occurrences supprimées seules."""
        if self.env.context.get("skip_nc_sync") or self.env.context.get("bf_nc_reecriture_serie"):
            return []
        backend = self.env["calendar.caldav.backend"]
        exclusions = []
        for event in self.filtered("recurrence_id"):
            serie = event.recurrence_id.with_context(active_test=False).calendar_event_ids
            if not (serie - self):
                continue  # toute la série part : pas une occurrence seule
            porteur = event._bf_nc_porteur()
            config = porteur._bf_nc_config_pousse() if porteur else False
            if not porteur or not config:
                continue
            origine = event._bf_nc_origine()
            if not origine:
                continue
            url, _href = backend._resource_url(config, porteur)
            exclusions.append((config, url, origine, event.allday, event.name or ""))
            if porteur in self:
                # Le porteur part et le reste de la série demeure : il passe
                # son adresse à la PREMIÈRE occurrence active qui reste (l'ordre
                # par défaut du cœur est « start desc », et une occurrence
                # archivée ne se retrouve pas à la lecture), sinon la lecture
                # suivante ne reconnaîtrait plus la série et la recréerait en
                # double à côté de ce qui reste.
                heritier = (serie - self).filtered("active").sorted("start")[:1]
                if heritier:
                    self._bf_nc_transferer_porteur(porteur, heritier)
        return exclusions

    @api.model
    def _bf_nc_transferer_porteur(self, porteur, heritier):
        """Passe l'adresse Nextcloud d'une série d'une occurrence à une autre."""
        heritier.with_context(skip_nc_sync=True, no_mail_to_attendees=True).write({
            cle: porteur[cle] for cle in (
                "x_nc_uid", "x_caldav_href", "x_caldav_etag",
                "x_nc_serie_sig", "x_sync_source", "x_last_sync")
        })
        porteur.with_context(skip_nc_sync=True, no_mail_to_attendees=True).write({
            "x_nc_uid": False, "x_caldav_href": False, "x_caldav_etag": False,
            "x_nc_serie_sig": False,
        })

    # Au-delà, une suppression n'est plus « une occurrence » mais la fin d'une
    # série (« celle-ci et les suivantes ») : des centaines d'EXDATE ne la
    # disent pas, et rien ne part, comme avant la 2.25.
    _BF_NC_EXCLUSIONS_MAX = 50

    def _bf_nc_pousser_exclusions(self, exclusions):
        if not self._push_via_caldav_enabled():
            return
        backend = self.env["calendar.caldav.backend"]
        par_serie = {}
        for config, url, origine, journee, titre in exclusions:
            serie = par_serie.setdefault((config, url, journee), {"origines": [], "titre": titre})
            serie["origines"].append(origine)
        for (config, url, journee), serie in par_serie.items():
            origines = serie["origines"]
            if len(origines) > self._BF_NC_EXCLUSIONS_MAX:
                _logger.warning(
                    "%d occurrences supprimées d'une même série (%s) : non poussées",
                    len(origines), url)
                self._bf_nc_avertir(serie["titre"])
                continue
            try:
                backend._exclure_occurrence(config, url, origines, journee)
                config.update_sync_status("success", "CalDAV EXDATE")
            except Exception as e:  # noqa: BLE001
                _logger.error("EXDATE CalDAV en échec (%s) : %s", url, e)
                config.update_sync_status("error", f"CalDAV EXDATE: {e}")
                self._bf_nc_avertir(serie["titre"])

    def _bf_nc_avertir_echec(self):
        self.ensure_one()
        self._bf_nc_avertir(self.name or "")

    @api.model
    def _bf_nc_avertir(self, titre):
        """Dire à l'usager que sa modification n'a pas atteint Nextcloud.

        Un échec seulement journalisé est exactement ce qui a laissé la
        statutaire du 2026-10-12 à deux heures différentes dans Odoo et dans
        Nextcloud sans que personne le sache.
        """
        user = self.env.user
        if not hasattr(user, "_bus_send"):
            return
        user._bus_send("simple_notification", {
            "type": "warning",
            "sticky": True,
            "title": _("Not sent to Nextcloud"),
            "message": _(
                "“%s” was saved in Odoo, but the change could not be sent "
                "to Nextcloud. Change it in Nextcloud too, or try again.",
                titre,
            ),
        })

    @api.model
    def _bf_nc_avertir_serie(self, titre):
        """Une série née dans Nextcloud vient d'être réécrite en entier dans Odoo."""
        user = self.env.user
        if not hasattr(user, "_bus_send"):
            return
        user._bus_send("simple_notification", {
            "type": "warning",
            "sticky": True,
            "title": _("Not sent to Nextcloud"),
            "message": _(
                "Changes to all or following occurrences of “%s” are not sent to "
                "Nextcloud. The series will be restored from Nextcloud the next "
                "time it changes there. Make the change in Nextcloud instead.",
                titre,
            ),
        })

    # ------------------------------------------------------------------
    # Séries à la lecture
    # ------------------------------------------------------------------

    def _bf_nc_estampiller(self, recurrence):
        """Heure d'origine des occurrences qui suivent encore la règle."""
        self.env["calendar.event"].flush_model(
            ["start", "recurrence_id", "follow_recurrence", "x_nc_recurrence_id"])
        self.env.cr.execute(
            """UPDATE calendar_event SET x_nc_recurrence_id = start
                WHERE recurrence_id = %s AND follow_recurrence
                  AND x_nc_recurrence_id IS NULL""",
            (recurrence.id,),
        )
        recurrence.calendar_event_ids.invalidate_recordset(["x_nc_recurrence_id"])

    def _bf_nc_correspond(self, origine, journee):
        """Cette occurrence est-elle celle que la règle place à ``origine`` ?"""
        self.ensure_one()
        mienne = self._bf_nc_origine()
        if not mienne:
            return False
        mienne = fields.Datetime.to_datetime(mienne)
        origine = fields.Datetime.to_datetime(origine)
        if journee or self.allday:
            return mienne.date() == origine.date()
        return abs((mienne - origine).total_seconds()) < 60

    @api.model
    def _bf_nc_vals_classe_categories(self, data, existant=None, etiquettes=None):
        """``privacy`` et ``categ_ids`` lus dans CLASS et CATEGORIES.

        Jusqu'à la 2.25, ni l'un ni l'autre n'était relu : un événement rendu
        privé dans Nextcloud revenait « sans confidentialité » dans Odoo, et un
        lien de partage bâti sur Odoo l'aurait montré en entier.

        CLASS : PUBLIC et PRIVATE se lisent tels quels. CONFIDENTIAL ne veut PAS
        dire ce que veut dire `confidential` dans Odoo : en RFC 5545 et dans
        Nextcloud, c'est « ne montrer que l'occupation » ; dans Odoo, c'est
        « visible des internes ». Il devient donc `private` (seule l'occupation
        se voit), sauf si Odoo avait déjà `confidential` ou `private` : c'est
        alors Odoo qui l'avait poussé, et on garde sa nuance.

        Une propriété absente ne change rien, comme STATUS : une étiquette
        retirée dans Nextcloud n'est donc relue que s'il en reste une.
        """
        vals = {}
        classe = (data.get("classe") or "").upper()
        if classe == "PUBLIC":
            vals["privacy"] = "public"
        elif classe == "PRIVATE":
            vals["privacy"] = "private"
        elif classe == "CONFIDENTIAL":
            avant = existant.privacy if existant else False
            vals["privacy"] = avant if avant in ("confidential", "private") else "private"
        elif classe:
            # Valeur inconnue (x-name, iana) : PRIVATE, comme le veut la RFC
            # 5545 §3.8.1.3 et comme la traite Nextcloud.
            vals["privacy"] = "private"
        noms = data.get("categories")
        if noms:
            if etiquettes is None:
                etiquettes = self._bf_nc_etiquettes(noms)
            vals["categ_ids"] = [(6, 0, etiquettes.ids)]
        return vals

    @api.model
    def _bf_nc_etiquettes(self, noms):
        """Les étiquettes d'agenda de ces noms, créées au besoin.

        Appariées en Python sur la table entière (elle est minuscule) : casse
        ignorée, Unicode normalisé (NFC), sans troncature. Un `ilike` d'Odoo
        prend « _ » et « % » pour des jokers et n'échappe pas « \\ » : une
        étiquette « A\\B » n'était pas retrouvée, et sa recréation heurtait
        l'unicité du nom à chaque passage.
        """
        Type = self.env["calendar.event.type"].sudo().with_context(active_test=False)

        def cle(nom):
            return unicodedata.normalize("NFC", nom or "").strip().casefold()

        par_cle = {}
        for etiquette in Type.search([]):
            par_cle.setdefault(cle(etiquette.name), etiquette)
        trouvees = Type.browse()
        for nom in noms or []:
            nom = unicodedata.normalize("NFC", nom or "").strip()
            if not nom:
                continue
            etiquette = par_cle.get(cle(nom))
            if not etiquette:
                etiquette = par_cle[cle(nom)] = Type.create({"name": nom})
            trouvees |= etiquette
        return trouvees

    def _bf_nc_rattraper_classe(self, data):
        """Pose CLASS / CATEGORIES d'une ressource que la lecture saute (ETag inchangé).

        Relecture adverse, M1 : un événement déjà privé dans Nextcloud avant la
        2.26, au même ETag, était sauté à chaque lecture, la complète comprise,
        et restait « sans confidentialité » dans Odoo. Ce rattrapage ne pose
        QUE ce qui manque : une confidentialité qu'Odoo n'a
        pas, jamais une confidentialité abaissée (PUBLIC n'est pas lu ici),
        et des étiquettes là où Odoo n'en a aucune. Relire toute la ressource
        écraserait ce qu'Odoo seul connaît.

        Rend True seulement si quelque chose a été écrit : sinon une série dont
        le porteur répond à une exception muette était reparcourue à chaque
        lecture complète.
        """
        self.ensure_one()
        classe = (data.get("classe") or "").upper()
        champs = set()
        if classe and classe != "PUBLIC" and self.privacy not in ("private", "confidential"):
            champs.add("privacy")
        if data.get("categories") and not self.categ_ids:
            champs.add("categ_ids")
        if not champs:
            return False
        ctx = {"no_mail_to_attendees": True, "skip_nc_sync": True,
               "mail_create_nolog": True, "tracking_disable": True, "dont_notify": True}
        if self.recurrence_id:
            return bool(self._bf_nc_classe_serie(
                self.recurrence_id, data, data.get("overrides"), ctx, champs=champs, rattrapage=True))
        vals = {cle: valeur for cle, valeur in
                self._bf_nc_vals_classe_categories(data, self).items() if cle in champs}
        if vals:
            self.with_context(**ctx).write(vals)
        return bool(vals)

    @api.model
    def _bf_nc_classe_serie(self, recurrence, data, overrides, ctx, champs=("privacy", "categ_ids"),
                            rattrapage=False):
        """CLASS et CATEGORIES sur chaque occurrence d'une série lue.

        La cible se calcule PAR OCCURRENCE, avant toute écriture : le maître,
        ou l'exception qui la remplace. Écrire d'abord la valeur du maître
        partout, puis celle des exceptions, faisait deux écritures par
        passage, perdait la nuance `confidential` d'Odoo et remettait sur une
        occurrence l'étiquette qu'on venait de lui retirer.

        CLASS : une exception qui ne la porte pas hérite de celle du maître
. Un client qui rend une série privée en ne touchant
        que le maître laisse des exceptions muettes ; les lire « sans
        confidentialité » rendait publiques, dans Odoo, des occurrences que
        Nextcloud cache. Le retrait sur une seule occurrence se dit, depuis
        Odoo, par un CLASS:PUBLIC explicite dans l'exception.

        CATEGORIES : une exception sans la propriété alors que le maître la
        porte l'a perdue exprès (c'est la forme du retrait fait dans Odoo).

        ``champs`` restreint ce qui s'écrit ; ``rattrapage`` n'abaisse jamais
        une confidentialité. Rend le nombre d'occurrences écrites.
        """
        champs = set(champs)
        # Étiquettes et heures d'origine calculées une fois.
        sources = [data] + [e for e in overrides or [] if e.get("recurrence_id")]
        etiquettes = {id(src): (self._bf_nc_etiquettes(src["categories"])
                                if src.get("categories") else None) for src in sources}
        exceptions = []
        for e in sources[1:]:
            exceptions.append((fields.Datetime.to_datetime(e["recurrence_id"]),
                               e.get("recurrence_id_allday"), e))
        ecrites = 0
        for occurrence in recurrence.calendar_event_ids:
            # L'origine ne se calcule que s'il y a des exceptions.
            mienne = occurrence._bf_nc_origine() if exceptions else None
            source = None
            if mienne and exceptions:
                mienne = fields.Datetime.to_datetime(mienne)
                for origine, journee, e in exceptions:
                    if (journee or occurrence.allday) and mienne.date() == origine.date() or \
                            not (journee or occurrence.allday) and \
                            abs((mienne - origine).total_seconds()) < 60:
                        source = e
                        break
            if source is None:
                cible = self._bf_nc_vals_classe_categories(data, occurrence, etiquettes[id(data)])
            else:
                lue = dict(source)
                if lue.get("classe") is None:
                    lue["classe"] = data.get("classe")
                cible = self._bf_nc_vals_classe_categories(lue, occurrence, etiquettes[id(source)])
                if source.get("categories") is None and data.get("categories"):
                    cible["categ_ids"] = [(6, 0, [])]
            ecart = {}
            if "privacy" in champs and "privacy" in cible and occurrence.privacy != cible["privacy"]:
                if not (rattrapage and (cible["privacy"] in (False, "public")
                                        or occurrence.privacy in ("private", "confidential"))):
                    ecart["privacy"] = cible["privacy"]
            if "categ_ids" in champs and "categ_ids" in cible and \
                    set(cible["categ_ids"][0][2]) != set(occurrence.categ_ids.ids):
                if not (rattrapage and occurrence.categ_ids):
                    ecart["categ_ids"] = cible["categ_ids"]
            if ecart:
                occurrence.with_context(**ctx).write(ecart)
                ecrites += 1
        return ecrites

    def _bf_nc_appliquer_exceptions(self, recurrence, overrides, ctx):
        """Porter sur les occurrences d'Odoo les exceptions lues dans Nextcloud."""
        if not overrides:
            return
        occurrences = recurrence.calendar_event_ids
        for exception in overrides:
            origine = exception.get("recurrence_id")
            if not origine:
                continue
            journee = exception.get("recurrence_id_allday")
            cible = occurrences.filtered(
                lambda e: e._bf_nc_correspond(origine, journee))[:1]
            if not cible:
                # Hors de l'horizon généré par Odoo, ou déjà supprimée.
                continue
            vals = {}
            if exception.get("summary") and exception["summary"] != cible.name:
                vals["name"] = exception["summary"]
            if exception.get("start") and exception.get("end"):
                if exception.get("allday"):
                    debut = fields.Date.to_date(exception["start"])
                    fin = fields.Date.to_date(exception["end"])
                    if not cible.allday or cible.start_date != debut or cible.stop_date != fin:
                        vals.update({"allday": True, "start": exception["start"],
                                     "stop": exception["end"]})
                else:
                    debut = fields.Datetime.to_datetime(exception["start"])
                    fin = fields.Datetime.to_datetime(exception["end"])
                    if cible.allday or cible.start != debut or cible.stop != fin:
                        vals.update({"allday": False, "start": debut, "stop": fin})
            statut = exception.get("status")
            if statut and "bf_event_status" in self._fields:
                mapped = self._bf_status_from_ics(statut)
                if mapped and mapped != cible.bf_event_status:
                    vals["bf_event_status"] = mapped
            if not cible.x_nc_recurrence_id:
                vals["x_nc_recurrence_id"] = origine
            if vals:
                cible.with_context(**ctx).write(vals)

    def _bf_nc_serie_inchangee(self, vals, rrule, data):
        """La série lue est-elle celle qu'Odoo a déjà, à ses exceptions près ?

        Tout ce qui fait la série est comparé : règle, heure de départ et
        durée, titre, lieu, description, participants, rappels, statut,
        fuseau. Le moindre écart et la série est recréée, comme avant la 2.25.
        """
        self.ensure_one()
        recurrence = self.recurrence_id
        if not recurrence:
            return False

        if self.x_nc_serie_sig:
            return self.x_nc_serie_sig == self._bf_nc_signature(data)
        # Série lue avant la 2.25, sans empreinte : comparée à ce qu'Odoo en
        # a, une seule fois ; l'empreinte est posée au passage.
        if not self._bf_nc_regle_equivalente(rrule, vals):
            return False
        if bool(self.allday) != bool(vals.get("allday")):
            return False
        origine = self._bf_nc_origine()
        if not origine or not vals.get("start"):
            return False
        origine = fields.Datetime.to_datetime(origine)
        debut = fields.Datetime.to_datetime(vals["start"])
        if self.allday:
            if origine.date() != debut.date():
                return False
        else:
            if abs((origine - debut).total_seconds()) >= 60:
                return False
            if self.follow_recurrence and vals.get("stop"):
                fin = fields.Datetime.to_datetime(vals["stop"])
                if abs(((self.stop - self.start) - (fin - debut)).total_seconds()) >= 60:
                    return False
        if (vals.get("name") or "") != (self.name or ""):
            return False
        if (vals.get("location") or "") != (self.location or ""):
            return False

        def texte(valeur):
            return " ".join(html2plaintext(valeur or "").split())

        if texte(vals.get("description")) != texte(self.description):
            return False
        for champ in ("partner_ids", "alarm_ids"):
            commande = vals.get(champ)
            if commande and commande[0][0] == 6:
                # Sans active_test : la lecture d'un Many2many cache les fiches
                # archivées, et un propriétaire au partenaire archivé faisait
                # paraître la série changée à chaque passage.
                actuels = self.with_context(active_test=False)[champ].ids
                if set(commande[0][2]) != set(actuels):
                    return False
        if "bf_event_status" in vals and vals["bf_event_status"] != self.bf_event_status:
            return False
        if data.get("event_tz") and data["event_tz"] != (recurrence.event_tz or self.event_tz):
            return False
        return True

    @api.model
    def _bf_nc_signature(self, data):
        """Empreinte du VEVENT maître lu : ce qui fait la série, sans ses exceptions.

        Les EXDATE et les exceptions n'y entrent pas : elles s'appliquent sur
        place. L'ETag non plus : il change à chaque écriture, la nôtre comprise.
        """
        brut = {cle: data.get(cle) for cle in (
            "summary", "start", "end", "allday", "location", "description",
            "rrule", "event_tz", "status")}
        brut["attendees"] = sorted((a or "").strip().lower() for a in data.get("attendees") or [])
        alarmes = data.get("alarms")
        brut["alarms"] = sorted(alarmes) if alarmes is not None else None
        return hashlib.sha1(
            json.dumps(brut, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def _bf_nc_regle_equivalente(self, rrule, vals):
        """Règle lue et règle d'Odoo, comparées par le lecteur d'Odoo lui-même.

        Pour une série créée avant la 2.25, dont la règle brute n'a pas été
        gardée. La série d'Odoo est née de CE lecteur appliqué à cette règle :
        seules les clés qu'il rend sont comparées.
        """
        recurrence = self.recurrence_id
        try:
            lu = recurrence._rrule_parse(rrule, fields.Datetime.to_datetime(vals.get("start")))
        except Exception:  # noqa: BLE001 (règle illisible : on recrée)
            return False
        for cle in ("rrule_type", "interval", "end_type"):
            if cle in lu and (lu[cle] or False) != (recurrence[cle] or False):
                return False
        if lu.get("end_type") == "count" and (lu.get("count") or 0) != (recurrence.count or 0):
            return False
        if lu.get("end_type") == "end_date":
            jusqu = lu.get("until")
            jusqu = jusqu.date() if hasattr(jusqu, "date") else jusqu
            if jusqu != recurrence.until:
                return False
        for jour in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
            if jour in lu and bool(lu[jour]) != bool(recurrence[jour]):
                return False
        for cle in ("month_by", "day", "weekday", "byday"):
            if cle in lu and str(lu[cle] or "") != str(recurrence[cle] or ""):
                return False
        return True
