# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Google Calendar backend: thin wrapper around googleapiclient calendar v3.

Responsibilities
----------------
- Build an authenticated `Resource` for a given Odoo user
- Pull events via events.list with syncToken incremental support
- Push create/update/delete operations to Google
- Convert between Google event JSON and Odoo calendar.event fields

Design notes
------------
- This is a `models.AbstractModel` so it's addressable via
  `self.env["calendar.google.backend"]` but has no DB table.
- No caching of discovery docs: `cache_discovery=False` avoids the
  well-known warning in non-writable filesystems.
- Recurring events: we request `singleEvents=True` initially, so Google
  expands recurrences server-side. Each instance becomes a standalone
  calendar.event in Odoo. This is the MVP shortcut — a V2 upgrade can
  parse RRULE and use Odoo's native calendar.recurrence.
"""

import logging
from datetime import datetime, timedelta, timezone

from dateutil import parser as dt_parser

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    from googleapiclient.discovery import build as google_build
    from googleapiclient.errors import HttpError
except ImportError:
    google_build = None
    HttpError = Exception


# Maximum page size for events.list. Google cap is 2500.
GOOGLE_LIST_PAGE_SIZE = 500

# Default time window for initial pull (Google requires timeMin if no syncToken)
GOOGLE_INITIAL_WINDOW_DAYS_BACK = 30
GOOGLE_INITIAL_WINDOW_DAYS_AHEAD = 365


class GoogleCalendarBackend(models.AbstractModel):
    _name = "calendar.google.backend"
    _description = "Google Calendar API Backend"

    # 2026-07-25 (tache #23886) — nombre d'echecs consecutifs sur un meme
    # evenement Google avant de cesser de le reessayer a chaque passe.
    # 3 laisse la place a un echec transitoire (verrou, timeout) sans laisser
    # un evenement durablement invalide inonder les journaux pendant des jours.
    _QUARANTINE_THRESHOLD = 3

    # === Service builder ===

    @api.model
    def _build_service(self, user):
        """Return an authenticated `calendar` v3 service for the given user.

        Handles token refresh transparently via the Credentials object
        returned by user._get_google_credentials().
        """
        if google_build is None:
            raise UserError(_(
                "google-api-python-client is not installed in the Odoo "
                "container."
            ))
        creds = user._get_google_credentials()
        return google_build(
            "calendar", "v3", credentials=creds, cache_discovery=False
        )

    # === Pull (Google → Odoo) ===

    @api.model
    def pull_events(self, config):
        """Fetch events from Google and upsert into Odoo.

        Uses syncToken when available (stored in config.caldav_sync_token,
        reused field). On 410 Gone (expired token) does a full resync.
        Updates config.caldav_sync_token on success.
        """
        config.ensure_one()
        owner = config.calendar_owner_id
        if not owner:
            config.update_sync_status(
                "error",
                "Google sync needs a calendar_owner_id to authenticate.",
            )
            return {"error": "no_owner"}

        try:
            service = self._build_service(owner)
        except UserError as e:
            config.update_sync_status("error", str(e))
            return {"error": str(e)}

        sync_token = config.caldav_sync_token
        calendar_id = config.google_calendar_id or "primary"

        events_fetched = []
        page_token = None
        next_sync_token = None
        need_full_resync = False

        while True:
            params = {
                "calendarId": calendar_id,
                "maxResults": GOOGLE_LIST_PAGE_SIZE,
                "singleEvents": True,
                "showDeleted": True,
                "pageToken": page_token,
            }
            if sync_token:
                params["syncToken"] = sync_token
            else:
                now = datetime.now(timezone.utc)
                params["timeMin"] = (
                    now - timedelta(days=GOOGLE_INITIAL_WINDOW_DAYS_BACK)
                ).isoformat()
                params["timeMax"] = (
                    now + timedelta(days=GOOGLE_INITIAL_WINDOW_DAYS_AHEAD)
                ).isoformat()
                # orderBy is only valid without syncToken
                params["orderBy"] = "startTime"

            try:
                resp = service.events().list(**params).execute()
            except HttpError as e:
                status = getattr(e, "resp", None) and e.resp.status or None
                if status == 410:
                    _logger.info(
                        "Google syncToken expired for config %s — "
                        "falling back to full resync", config.name,
                    )
                    need_full_resync = True
                    break
                config.update_sync_status(
                    "error", f"Google events.list failed: {e}"
                )
                return {"error": str(e)}

            events_fetched.extend(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                next_sync_token = resp.get("nextSyncToken")
                break

        if need_full_resync:
            config.caldav_sync_token = False
            return self.pull_events(config)

        created = 0
        updated = 0
        deleted = 0
        errors = 0
        quarantined = 0
        CalendarEvent = self.env["calendar.event"]
        # 2026-07-25 (tache #23886) — mise en quarantaine.
        # Sans ce garde-fou, un evenement qui ne passe pas la validation Odoo
        # (contrainte resource_booking par exemple) est reessaye a CHAQUE passe,
        # toutes les 7 minutes, indefiniment. Constate en juillet : deux
        # rendez-vous ont produit ~12 erreurs/heure pendant 4 a 9 jours, noyant
        # les vraies erreurs de synchronisation dans le bruit.
        # On memorise les uid en echec dans le champ texte du config et on les
        # saute, en journalisant UN avertissement au lieu d'une erreur en boucle.
        quarantine = config._get_pull_quarantine()
        for item in events_fetched:
            gid = item.get("id")
            if gid and quarantine.get(gid, 0) >= self._QUARANTINE_THRESHOLD:
                quarantined += 1
                continue
            try:
                with self.env.cr.savepoint():
                    result = self._upsert_event(CalendarEvent, item, config)
                    self.env.flush_all()
                if result == "created":
                    created += 1
                elif result in ("updated", "updated-archived"):
                    updated += 1
                elif result == "deleted":
                    deleted += 1
                if gid:
                    quarantine.pop(gid, None)  # succes : on oublie les echecs passes
            except Exception as e:
                n = quarantine.get(gid, 0) + 1 if gid else 0
                if gid:
                    quarantine[gid] = n
                if n >= self._QUARANTINE_THRESHOLD:
                    _logger.warning(
                        "Google event %s mis en QUARANTAINE pour la config %s "
                        "apres %d echecs consecutifs. Dernier motif : %s. "
                        "Il ne sera plus reessaye tant qu'il n'aura pas change "
                        "cote Google ou que la quarantaine n'aura pas ete videe.",
                        gid, config.name, n, e,
                    )
                else:
                    _logger.exception(
                        "Failed to upsert Google event %s for config %s "
                        "(echec %d/%d avant quarantaine): %s",
                        gid, config.name, n, self._QUARANTINE_THRESHOLD, e,
                    )
                errors += 1
        config._set_pull_quarantine(quarantine)
        if quarantined:
            _logger.info(
                "Google pull %s : %d evenement(s) ignore(s) car en quarantaine.",
                config.name, quarantined,
            )

        if next_sync_token:
            config.caldav_sync_token = next_sync_token

        status = "success" if errors == 0 else ("partial" if created + updated + deleted else "error")
        msg = (
            f"Google pull: {created} created, {updated} updated, "
            f"{deleted} deleted, {errors} errors."
        )
        config.update_sync_status(status, msg)
        return {
            "created": created,
            "updated": updated,
            "deleted": deleted,
            "errors": errors,
        }

    # === Upsert helper ===

    def _upsert_event(self, CalendarEvent, gcal_event, config):
        """Create or update (or delete) a calendar.event from a Google item."""
        gid = gcal_event.get("id")
        if not gid:
            return "skipped"

        # Google marks deletions with status=='cancelled' in incremental pull
        if gcal_event.get("status") == "cancelled":
            existing = CalendarEvent.search(
                [
                    ("x_nc_uid", "=", gid),
                    ("x_nc_calendar_id", "=", config.id),
                ],
                limit=1,
            )
            if existing:
                existing.with_context(
                    skip_nc_sync=True,
                    skip_google_sync=True,
                    no_mail_to_attendees=True,
                    mail_create_nolog=True,
                    tracking_disable=True,
                    dont_notify=True,
                ).unlink()
                return "deleted"
            return "skipped"

        vals = self._google_to_odoo_vals(gcal_event, config)
        vals["x_nc_calendar_id"] = config.id
        vals["x_sync_source"] = "google"
        vals["x_nc_uid"] = gid
        vals["x_caldav_etag"] = gcal_event.get("etag")
        vals["x_last_sync"] = fields.Datetime.now()
        vals["color"] = config.odoo_color or 0

        # Attendee mapping — include calendar owner + match by email
        partner_ids = set()
        if config.calendar_owner_id and config.calendar_owner_id.partner_id:
            partner_ids.add(config.calendar_owner_id.partner_id.id)
        for att in gcal_event.get("attendees", []) or []:
            email = att.get("email")
            if not email:
                continue
            partner = self.env["res.partner"].search(
                [("email", "=ilike", email)], limit=1
            )
            if partner:
                partner_ids.add(partner.id)
        if partner_ids:
            vals["partner_ids"] = [(6, 0, list(partner_ids))]

        # 2026-07-25 (tache #23886) — rapprochement robuste.
        # Deux defauts corriges ici :
        #  1) la recherche ignorait les evenements ARCHIVES (active_test par
        #     defaut). Un doublon archive a la main etait donc recree au pull
        #     suivant, annulant le nettoyage.
        #  2) la recherche etait limitee a x_nc_calendar_id = config.id. Le meme
        #     uid rattache a une autre config produisait un second
        #     enregistrement. C'est l'origine des 338 x_nc_uid en double
        #     constates en production : deux enregistrements distincts portant
        #     le meme uid, crees a un mois d'intervalle par deux passes du puller.
        Ev = CalendarEvent.with_context(active_test=False)
        existing = Ev.search(
            [
                ("x_nc_uid", "=", gid),
                ("x_nc_calendar_id", "=", config.id),
            ],
            limit=1,
        )
        if not existing:
            # Repli : meme uid, toutes configs confondues. Mieux vaut mettre a
            # jour l'enregistrement existant que d'en creer un deuxieme.
            existing = Ev.search([("x_nc_uid", "=", gid)], limit=1)
        ctx = {
            "skip_nc_sync": True,
            "skip_google_sync": True,
            "no_mail_to_attendees": True,
            "mail_create_nolog": True,
            "tracking_disable": True,
            "dont_notify": True,
        }
        if existing:
            # Un enregistrement archive l'a ete deliberement (doublon retire a
            # la main). On le met a jour sans le reactiver, sinon on
            # ressusciterait le doublon que le menage venait d'eliminer.
            write_vals = dict(vals)
            write_vals.pop("active", None)
            existing.with_context(**ctx).write(write_vals)
            return "updated" if existing.active else "updated-archived"
        CalendarEvent.with_context(**ctx).create(vals)
        return "created"

    # === Value converters ===

    def _google_to_odoo_vals(self, gcal_event, config):
        """Convert a Google event JSON payload to Odoo calendar.event vals.

        Handles timed events (dateTime) and all-day events (date).
        Timezones are resolved to UTC for Odoo's naive datetime fields.
        """
        vals = {
            "name": gcal_event.get("summary") or "(Sans titre)",
            "description": gcal_event.get("description") or "",
            "location": gcal_event.get("location") or "",
        }

        start = gcal_event.get("start") or {}
        end = gcal_event.get("end") or {}

        if start.get("date"):
            # All-day event — Google uses exclusive end, Odoo uses inclusive stop_date
            vals["allday"] = True
            start_d = dt_parser.isoparse(start["date"]).date()
            end_d = dt_parser.isoparse(end["date"]).date()
            vals["start"] = datetime.combine(start_d, datetime.min.time())
            vals["stop"] = datetime.combine(
                end_d - timedelta(days=1), datetime.min.time()
            )
            vals["start_date"] = start_d
            vals["stop_date"] = end_d - timedelta(days=1)
        else:
            vals["allday"] = False
            s = dt_parser.isoparse(start["dateTime"]).astimezone(timezone.utc)
            e = dt_parser.isoparse(end["dateTime"]).astimezone(timezone.utc)
            # Odoo stores naive UTC — strip tzinfo
            vals["start"] = s.replace(tzinfo=None)
            vals["stop"] = e.replace(tzinfo=None)

        privacy_map = {"default": "public", "public": "public", "private": "private", "confidential": "private"}
        if gcal_event.get("visibility"):
            vals["privacy"] = privacy_map.get(gcal_event["visibility"], "public")

        transparency = gcal_event.get("transparency")
        if transparency == "transparent":
            vals["show_as"] = "free"
        else:
            vals["show_as"] = "busy"

        return vals

    def _odoo_to_google_vals(self, odoo_event, owner=None):
        """Convert an Odoo calendar.event to a Google events.insert body.

        Assumes Odoo stores naive UTC in `start`/`stop`.

        ``owner`` is the ``res.users`` record that owns the target Google
        calendar (taken from ``config.calendar_owner_id`` at the call
        site). It is used to strip self-aliased attendees so Google does
        not bounce an iCal invitation back to the organizer.
        """
        body = {
            "summary": odoo_event.name or "(Sans titre)",
            "description": odoo_event.description or "",
            "location": odoo_event.location or "",
        }

        if odoo_event.allday:
            body["start"] = {
                "date": (odoo_event.start_date or odoo_event.start.date()).isoformat()
            }
            # Google expects exclusive end; Odoo stop_date is inclusive
            stop_d = odoo_event.stop_date or odoo_event.stop.date()
            body["end"] = {"date": (stop_d + timedelta(days=1)).isoformat()}
        else:
            s = odoo_event.start.replace(tzinfo=timezone.utc)
            e = odoo_event.stop.replace(tzinfo=timezone.utc)
            body["start"] = {"dateTime": s.isoformat()}
            body["end"] = {"dateTime": e.isoformat()}

        if odoo_event.privacy == "private":
            body["visibility"] = "private"
        elif odoo_event.privacy == "public":
            body["visibility"] = "public"

        if odoo_event.show_as == "free":
            body["transparency"] = "transparent"
        else:
            body["transparency"] = "opaque"

        # Attendees — email addresses of partners linked to the event.
        # Strip the calendar owner's own aliases so Google does not send a
        # courtesy iCal invite back to the organizer when they appear as
        # both organizer and attendee under different but equivalent
        # identities (e.g. login=info@example.com, Google primary
        # =alias@example.com). See res.users._get_google_self_addresses.
        self_addresses = (
            owner._get_google_self_addresses() if owner else set()
        )
        attendees = []
        for att in odoo_event.attendee_ids:
            if not att.email:
                continue
            if att.email.strip().lower() in self_addresses:
                continue
            attendees.append({"email": att.email})
        if attendees:
            body["attendees"] = attendees

        return body

    # === Push (Odoo → Google) ===

    @api.model
    def push_create(self, config, odoo_event):
        """Insert an event into Google. Returns (google_id, etag)."""
        service = self._build_service(config.calendar_owner_id)
        body = self._odoo_to_google_vals(
            odoo_event, owner=config.calendar_owner_id
        )
        calendar_id = config.google_calendar_id or "primary"
        try:
            resp = service.events().insert(
                calendarId=calendar_id, body=body, sendUpdates="none"
            ).execute()
        except HttpError as e:
            _logger.error(
                "Google insert failed for event %s: %s", odoo_event.id, e
            )
            raise
        return resp.get("id"), resp.get("etag")

    @api.model
    def push_update(self, config, odoo_event):
        """Patch an existing event in Google. Returns (google_id, etag)."""
        service = self._build_service(config.calendar_owner_id)
        if not odoo_event.x_nc_uid:
            # Never pushed before — fall through to create
            return self.push_create(config, odoo_event)
        body = self._odoo_to_google_vals(
            odoo_event, owner=config.calendar_owner_id
        )
        calendar_id = config.google_calendar_id or "primary"
        try:
            resp = service.events().patch(
                calendarId=calendar_id,
                eventId=odoo_event.x_nc_uid,
                body=body,
                sendUpdates="none",
            ).execute()
        except HttpError as e:
            status = getattr(e, "resp", None) and e.resp.status or None
            if status == 404:
                # Event was deleted on Google side — recreate
                _logger.info(
                    "Google event %s not found, recreating",
                    odoo_event.x_nc_uid,
                )
                return self.push_create(config, odoo_event)
            raise
        return resp.get("id"), resp.get("etag")

    @api.model
    def push_delete(self, config, google_event_id):
        """Delete an event from Google. Silent on 404 (already gone)."""
        if not google_event_id:
            return False
        service = self._build_service(config.calendar_owner_id)
        calendar_id = config.google_calendar_id or "primary"
        try:
            service.events().delete(
                calendarId=calendar_id,
                eventId=google_event_id,
                sendUpdates="none",
            ).execute()
        except HttpError as e:
            status = getattr(e, "resp", None) and e.resp.status or None
            if status in (404, 410):
                return True
            raise
        return True
