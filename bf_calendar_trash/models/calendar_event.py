"""The calendar's trash.

A meeting in the trash is an archived meeting with a date, an author and a
batch: the batch is the gesture that put it there, one per meeting or per
series, and restoring brings that gesture back (a series cut short comes back
whole, not one date).

What sends a meeting to the trash, and why each door is here:

* `action_archive` / `action_mass_archive`, when the web client calls them on
  meetings. Core's calendar popover does on its "Delete" button when a Google
  or Microsoft module is installed, and the form's "Archive" always does.
  Until now they archived in silence, and Nextcloud kept the meeting; the next
  pull that touched it brought it back (measured in production: back a
  few seconds after being archived).
* `unlink` called by the web client on meetings: the form's and the list's
  "Delete", which erased the meeting and its thread for good.
* the popover's delete dialog (`calendar.popover.delete.wizard`), which is
  what "Delete" opens on a series when neither Google nor Microsoft is
  installed: wizard/calendar_popover_delete.py.
* "Delete" on an activity and its meeting (`mail.activity.unlink_w_meeting`).
* the cancellation dialog's "Move to trash" (wizard/calendar_event_cancel.py).
* `delete_from_nextcloud` and a date excluded in Nextcloud: deleted there.

What does NOT: every other `unlink` or archive. Core archives a whole series
through `action_mass_archive` before rebuilding it (`_rewrite_recurrence`);
the Nextcloud pull razes and recreates series (on a real database, many
times more ids gone than meetings kept); other modules clean up after themselves. A
trash that caught those would fill with copies nobody deleted, and would
delete from Nextcloud what nobody asked to delete.

Deleting a meeting that is already in the trash deletes it for good, after
removing it from Nextcloud if it is still there.

A meeting that comes from an appointment, or from a subscribed external
calendar, is not moved to the trash: the appointment is cancelled from the
appointment, the external calendar is changed at its
source.
"""

import json
import logging
import uuid
from datetime import timedelta

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.http import request

from odoo.addons.calendar_nextcloud_sync.models import caldav_backend as cb

_logger = logging.getLogger(__name__)

# Beyond this, removing single dates from a series is not "a few dates" any
# more, and Nextcloud is not written to (same bound as the sync's own EXDATE).
EXDATE_MAX = 50

# Public stamps, cleared whenever a meeting becomes active again.
STAMPS = {
    "bf_trashed_at": False, "bf_trashed_by": False, "bf_trash_batch": False,
    "bf_trash_origin": False, "bf_trash_nc_state": False,
}
# System-only bookkeeping, cleared alongside (in sudo).
BOOKKEEPING = {
    "bf_trash_nc_ics": False, "bf_trash_nc_url": False, "bf_trash_nc_etag": False,
    "bf_trash_nc_mode": False, "bf_trash_nc_rrule": False,
    "bf_trash_recurrence_id": False, "bf_trash_recurrence_end": False,
}


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    bf_trashed_at = fields.Datetime(
        string="Moved to trash on", readonly=True, copy=False, index=True,
    )
    bf_trashed_by = fields.Many2one(
        "res.users", string="Moved to trash by", readonly=True, copy=False,
    )
    bf_trash_batch = fields.Char(string="Trash gesture", readonly=True, copy=False, index=True)
    bf_trash_origin = fields.Selection(
        [("odoo", "Odoo"), ("nextcloud", "Nextcloud")],
        string="Deleted from", readonly=True, copy=False,
    )
    bf_trash_nc_state = fields.Selection(
        [
            ("deleted", "Removed from Nextcloud"),
            ("edited", "Dates removed from the series in Nextcloud"),
            ("failed", "Still in Nextcloud"),
        ],
        string="Nextcloud", readonly=True, copy=False,
        help="What became of the meeting in Nextcloud (or Google) when it was "
             "moved to the trash. Restoring puts back what was removed.",
    )
    # The resource as it was before the trash touched it, where it lives, what
    # was done to it and what we wrote. System-only: an .ics carries every
    # guest's address, the URL is the one the sync's credentials are sent to,
    # and the series fields rewrite a recurrence on restore.
    bf_trash_nc_ics = fields.Text(
        string="Nextcloud copy", readonly=True, copy=False, groups="base.group_system")
    bf_trash_nc_url = fields.Char(
        string="Nextcloud address", readonly=True, copy=False, groups="base.group_system")
    bf_trash_nc_etag = fields.Char(
        string="Nextcloud version after the trash", readonly=True, copy=False,
        groups="base.group_system")
    bf_trash_nc_mode = fields.Selection(
        [("delete", "Deleted"), ("exdate", "Dates excluded"), ("until", "Series ended"),
         ("google", "Deleted from Google")],
        string="Removed from the calendar by", readonly=True, copy=False,
        groups="base.group_system")
    bf_trash_nc_rrule = fields.Char(
        string="Rule written by the trash", readonly=True, copy=False, groups="base.group_system")
    # "This one and the following ones" goes through core, which detaches the
    # dates from their series and ends the rule before them. Restoring puts
    # both back: the series each date belonged to, and how that series ended.
    bf_trash_recurrence_id = fields.Many2one(
        "calendar.recurrence", string="Series before the trash", readonly=True, copy=False,
        ondelete="set null", groups="base.group_system")
    bf_trash_recurrence_end = fields.Char(
        string="End of the series before the trash", readonly=True, copy=False,
        groups="base.group_system")

    def _get_public_fields(self):
        """When and by whom a meeting went to the trash is not the meeting.

        A private meeting of someone else reads as « Busy » with every
        non-public field emptied: in the trash, its date, author and origin
        came out blank. They say nothing of what the meeting is about.
        """
        return super()._get_public_fields() | {
            "bf_trashed_at", "bf_trashed_by", "bf_trash_batch", "bf_trash_origin",
            "bf_trash_nc_state",
        }

    # ------------------------------------------------------------------
    # The doors
    # ------------------------------------------------------------------

    def action_archive(self):
        if not self._bf_trash_door("action_archive"):
            return super().action_archive()
        self._bf_trash("self_only")
        return True

    def action_unarchive(self):
        trashed = self.filtered(lambda e: not e.active and e.bf_trashed_at)
        if trashed:
            trashed._bf_restore()
        rest = self - trashed
        if rest:
            return super(CalendarEvent, rest).action_unarchive()
        return True

    def action_mass_archive(self, recurrence_update_setting):
        self.ensure_one()
        if not self._bf_trash_door("action_mass_archive"):
            return super().action_mass_archive(recurrence_update_setting)
        if not self.recurrence_id or recurrence_update_setting == "self_only":
            self._bf_trash("self_only")
            return True
        recurrence = self.recurrence_id
        occurrences = recurrence.calendar_event_ids
        if recurrence_update_setting == "future_events":
            chosen = occurrences.filtered(lambda e: e.start >= self.start)
            if chosen == occurrences:
                # From the first date on is the whole series. Core would stop
                # the series before its first date: detach everything and
                # delete the recurrence, leaving nothing to restore into.
                recurrence_update_setting = "all_events"
            occurrences = chosen
        extra = {}
        if recurrence_update_setting == "future_events":
            extra = {
                "bf_trash_recurrence_id": recurrence.id,
                "bf_trash_recurrence_end": json.dumps({
                    "end_type": recurrence.end_type,
                    "count": recurrence.count,
                    "until": fields.Date.to_string(recurrence.until) if recurrence.until else False,
                }),
            }
        # Core's own archiving does the Odoo half: it stops the recurrence at
        # this date for "future_events", so a later edit of the series does not
        # regenerate the dates just thrown away.
        core = super(CalendarEvent, self.with_context(bf_trash_bypass=True, skip_nc_sync=True))
        setting = recurrence_update_setting
        occurrences._bf_trash(
            setting, archive=lambda: core.action_mass_archive(setting), extra=extra)
        return True

    def action_mass_deletion(self, recurrence_update_setting):
        if not self._bf_trash_door("action_mass_deletion"):
            return super().action_mass_deletion(recurrence_update_setting)
        return self.with_context(bf_trash_door=True).action_mass_archive(recurrence_update_setting)

    def unlink(self):
        if not self._bf_trash_door("unlink"):
            return super().unlink()
        in_trash = self.filtered(lambda e: not e.active and e.bf_trashed_at)
        live = (self - in_trash).filtered("active")
        if live:
            live._bf_trash("self_only")
        rest = self - live
        if rest:
            return rest._bf_purge()
        return True

    def _bf_trash_door(self, method):
        """Is this call a person throwing meetings away?

        Read off the request itself: the web client's JSON-RPC call is
        ``method`` on `calendar.event`, and this is that call. Core calls the
        same methods for its own housekeeping (`_rewrite_recurrence` archives
        a whole series through `action_mass_archive` before rebuilding it),
        and those must stay what they were. ``bf_trash_door`` in the context
        opens the door from our own code (the dialogs).

        Not a guard: a caller who sets or clears these keys over RPC gets the
        trash instead of a deletion, or a deletion his rights already allow.
        """
        ctx = self.env.context
        if not self or ctx.get("bf_trash_purge") or ctx.get("bf_trash_bypass") \
                or ctx.get("skip_nc_sync"):
            return False
        if ctx.get("bf_trash_door"):
            return True
        try:
            params = request.params if request else None
        except RuntimeError:
            return False
        if not params:
            return False
        return params.get("model") == self._name and params.get("method") == method

    def action_bf_restore(self):
        trashed = self.filtered(lambda e: not e.active and e.bf_trashed_at)
        if not trashed:
            raise UserError(_("These meetings are not in the trash."))
        trashed._bf_restore()
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    def action_bf_purge(self):
        trashed = self.filtered(lambda e: not e.active and e.bf_trashed_at)
        if trashed != self:
            raise UserError(_("Only meetings in the trash can be deleted for good."))
        trashed._bf_purge()
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    def write(self, vals):
        if vals.get("active") and not self.env.context.get("bf_trash_restoring"):
            # Brought back by anything but a restore (a subscription read
            # again, core reactivating a series base): not in the trash any
            # more, and its stale stamps would say otherwise.
            stale = self.filtered("bf_trashed_at")
            res = super().write(vals)
            if stale:
                super(CalendarEvent, stale).write(dict(STAMPS))
                super(CalendarEvent, stale.sudo()).write(dict(BOOKKEEPING))
            return res
        return super().write(vals)

    # ------------------------------------------------------------------
    # Moving to the trash
    # ------------------------------------------------------------------

    def _bf_trash_refusal(self):
        """Meetings a person may not throw away from the agenda, and why."""
        if "resource_booking_ids" in self._fields:
            booked = self.sudo().filtered(lambda e: e.resource_booking_ids.filtered(
                lambda b: b.active and b.state != "canceled"))
            if booked:
                return _(
                    "“%s” comes from an appointment: cancel the appointment instead.",
                    booked[:1].name or "",
                )
        if hasattr(self, "_bf_abonnement_verrouille") and self._bf_abonnement_verrouille():
            return _("This event comes from a subscribed external calendar: "
                     "it cannot be deleted here.")
        return False

    def _bf_trash(self, scope="self_only", nextcloud=True, origin="odoo", archive=None, extra=None):
        """Move these meetings to the trash.

        ``scope`` is the question core asks of anyone touching a series
        (`self_only`, `future_events`, `all_events`): it decides what
        Nextcloud is told. Removing one date is an EXDATE; "this one and the
        following ones" ends the series there; the whole series, or every
        date of it, deletes the resource.

        ``archive`` does the Odoo half when core must do it (its series
        logic); otherwise the meetings are archived here.
        """
        events = self.filtered("active")
        if not events:
            return events
        events.check_access("write")
        if origin == "odoo":
            refusal = events._bf_trash_refusal()
            if refusal:
                raise UserError(refusal)
        # One batch per meeting or per series, read before core detaches the
        # dates of "this one and the following ones" from their series.
        batches = {}
        for event in events:
            key = ("r", event.recurrence_id.id) if event.recurrence_id else ("e", event.id)
            batches.setdefault(key, uuid.uuid4().hex)
        batch_of = {e.id: batches[("r", e.recurrence_id.id) if e.recurrence_id else ("e", e.id)]
                    for e in events}
        plan = events._bf_trash_nc_plan(scope) if nextcloud else []
        if "bf_hold_ids" in self._fields:
            # bf_calendar_hold: the slots held to move this meeting are free
            # again, as when the meeting is deleted. Released, not trashed.
            holds = events.bf_hold_ids - events
            if holds:
                holds.with_context(bf_trash_bypass=True, bf_trash_door=False)._bf_hold_release()
        quiet = events.with_context(
            skip_nc_sync=True, bf_trash_bypass=True, no_mail_to_attendees=True)
        if archive:
            archive()
        else:
            vals = {"active": False}
            if len(events) == 1 and events.recurrence_id:
                vals["recurrence_update"] = "self_only"
            quiet.write(vals)
        now = fields.Datetime.now()
        archived = quiet.with_context(active_test=False).exists()
        for batch in set(batch_of.values()):
            archived.filtered(lambda e: batch_of.get(e.id) == batch).write({
                "bf_trashed_at": now,
                "bf_trashed_by": self.env.uid,
                "bf_trash_batch": batch,
                "bf_trash_origin": origin,
            })
        if extra:
            archived.sudo().write(extra)
        self._bf_trash_nc_run(plan)
        return events

    def _bf_trash_nc_plan(self, scope):
        """What to remove from Nextcloud (or Google), read BEFORE archiving.

        ``[{mode, config, url, anchor, ...}]``. ``anchor`` is the meeting the
        copy of the resource is kept on. When the series carrier is among the
        dates removed and some dates remain, the carrier hands the series'
        address to the first date that remains (as the sync does when a date
        is deleted), whatever the push transport: an archived carrier would
        leave the series unreachable.
        """
        backend = self.env["calendar.caldav.backend"]
        caldav = self._push_via_caldav_enabled()
        plan = []
        for event in self.filtered(lambda e: not e.recurrence_id and e.x_nc_uid):
            google = event.x_nc_calendar_id.sudo()
            if google.backend_type == "google":
                if google.sync_direction != "nc_to_odoo":
                    plan.append({"mode": "google", "config": google, "anchor": event,
                                 "title": event.name or "", "gid": event.x_nc_uid})
                continue
            config = event._bf_nc_config_pousse() if caldav else None
            url = backend._resource_url(config, event)[0] if config else None
            if url:
                plan.append({"mode": "delete", "config": config, "url": url,
                             "anchor": event, "title": event.name or ""})
        series = {}
        for event in self.filtered("recurrence_id"):
            series.setdefault(event.recurrence_id, self.browse())
            series[event.recurrence_id] |= event
        for recurrence, chosen in series.items():
            carrier = chosen[:1]._bf_nc_porteur()
            remaining = recurrence.calendar_event_ids - chosen
            # Address and uid read before the hand-over, which clears them.
            carrier_uid = carrier.x_nc_uid
            config = carrier._bf_nc_config_pousse() if carrier and caldav else None
            url = backend._resource_url(config, carrier)[0] if config else None
            if carrier and carrier in chosen and remaining:
                heir = remaining.filtered("active").sorted("start")[:1]
                if heir:
                    self._bf_nc_transferer_porteur(carrier, heir)
            if not url:
                continue
            step = {"config": config, "url": url, "anchor": chosen.sorted("start")[:1],
                    "title": chosen[:1].name or "", "allday": chosen[:1].allday,
                    "carrier_uid": carrier_uid}
            origins = [o for o in (e._bf_nc_origine() for e in chosen) if o]
            if not remaining:
                step["mode"] = "delete"
            elif scope == "self_only":
                step["mode"] = "exdate"
                step["origins"] = origins
            else:
                step["mode"] = "until"
                step["cut"] = min(origins) if origins else min(chosen.mapped("start"))
            plan.append(step)
        return plan

    def _bf_trash_nc_run(self, plan):
        """Carry the plan out. A failure is said, kept on the meeting, never raised.

        ⚠️ The calls leave before the transaction commits, as every push of
        the sync does: a transaction rolled back afterwards leaves Nextcloud
        ahead of Odoo, and the next pull reconciles.
        """
        backend = self.env["calendar.caldav.backend"]
        for step in plan:
            config, anchor = step["config"], step["anchor"]
            keep = {"bf_trash_nc_mode": step["mode"]}
            try:
                if step["mode"] == "google":
                    self.env["calendar.google.backend"].push_delete(config, step["gid"])
                    anchor.sudo().write(dict(keep, bf_trash_nc_state="deleted"))
                    config.update_sync_status("success", "Google trash: %s" % step["title"])
                    continue
                url = step["url"]
                keep["bf_trash_nc_url"] = url
                text, _etag = backend._fetch(config, url)
                if text is None:
                    # Already gone (deleted there, not read yet): nothing to
                    # keep, and a restore writes it back from Odoo.
                    anchor.sudo().write(dict(keep, bf_trash_nc_state="deleted"))
                    continue
                keep["bf_trash_nc_ics"] = text
                if step["mode"] == "delete":
                    backend.push_delete(config, url=url)
                    keep["bf_trash_nc_state"] = "deleted"
                else:
                    written = {}
                    if step["mode"] == "exdate":
                        if not step["origins"] or len(step["origins"]) > EXDATE_MAX:
                            raise cb.RessourceInattendue(
                                "%d dates to remove one by one" % len(step["origins"]))
                        etag = backend._exclure_occurrence(
                            config, url, step["origins"], step["allday"])
                    else:
                        def until(t, step=step):
                            written["text"] = self._bf_trash_until(config, t, step["cut"])
                            return written["text"]
                        etag = backend._retoucher(config, url, until)
                        keep["bf_trash_nc_rrule"] = self._bf_trash_master_rrule(written["text"])
                    keep["bf_trash_nc_state"] = "edited"
                    keep["bf_trash_nc_etag"] = self._bf_trash_settle_series(
                        config, url, step["carrier_uid"], etag, written.get("text"))
                anchor.sudo().write(keep)
                config.update_sync_status("success", "CalDAV trash: %s" % step["title"])
            except Exception as e:  # noqa: BLE001 (said to the user, kept on the meeting)
                _logger.error("Trash: calendar not updated for %s: %s", step["title"], e)
                anchor.sudo().write(dict(keep, bf_trash_nc_state="failed"))
                config.update_sync_status("error", "Trash: %s" % e)
                self._bf_nc_avertir(step["title"])

    @api.model
    def _bf_trash_master_rrule(self, text):
        backend = self.env["calendar.caldav.backend"]
        lines, master, _exceptions = backend._decouper(text or "")
        if master is None:
            return False
        block = lines[master[0]:master[1] + 1]
        return next((line for _i, line in cb.props_de_tete(block)
                     if cb.nom_prop(line) == "RRULE"), False)

    @api.model
    def _bf_trash_until(self, config, text, cut):
        """The resource with its series ending just before ``cut`` (naive UTC).

        UNTIL is written in UTC for a series in date-time (RFC 5545 §3.3.10:
        required when DTSTART carries a TZID) and as a DATE for an all-day
        series; COUNT goes, the two cannot coexist. Exceptions dated at or
        after the cut go with the dates they changed.
        """
        backend = self.env["calendar.caldav.backend"]
        sync = self.env["nextcloud.calendar.sync.config"]
        lines, master, exceptions = backend._decouper(text)
        if master is None:
            raise cb.RessourceInattendue("resource without a master VEVENT")
        vtz = sync._ics_vtimezones(text)
        start, end = master
        block = lines[start:end + 1]
        head = cb.props_de_tete(block)
        rrule = next(((i, line) for i, line in head if cb.nom_prop(line) == "RRULE"), None)
        if rrule is None:
            raise cb.RessourceInattendue("no series where Odoo sees one")
        params = next((cb.params_prop(line) for _i, line in head
                       if cb.nom_prop(line) == "DTSTART"), "")
        dtstart = next((cb.valeur_prop(line) for _i, line in head
                        if cb.nom_prop(line) == "DTSTART"), "")
        allday = cb.journee_de(params)
        cut = fields.Datetime.to_datetime(cut)
        first, _allday = sync._parse_ics_datetime(dtstart.strip(), params, vtz)
        first = fields.Datetime.to_datetime(first)
        if first and ((cut.date() <= first.date()) if allday else (cut <= first)):
            raise cb.RessourceInattendue("cut before the first date: the whole series goes")
        if allday:
            until = (cut.date() - timedelta(days=1)).strftime("%Y%m%d")
        else:
            until = (cut - timedelta(seconds=1)).strftime("%Y%m%dT%H%M%SZ")
        parts = [p for p in cb.valeur_prop(rrule[1]).split(";")
                 if p and p.split("=", 1)[0].strip().upper() not in ("UNTIL", "COUNT")]
        block[rrule[0]] = "RRULE%s:%s" % (cb.params_prop(rrule[1]), ";".join(parts + ["UNTIL=" + until]))
        dropped = set()
        for d, f, rid in exceptions:
            when, _a = sync._parse_ics_datetime(
                cb.valeur_prop(rid).strip(), cb.params_prop(rid), vtz)
            when = fields.Datetime.to_datetime(when)
            if when and ((when.date() >= cut.date()) if allday else (when >= cut)):
                dropped.update(range(d, f + 1))
        out = []
        for i, line in enumerate(lines):
            if start <= i <= end:
                if i == start:
                    out += block
                continue
            if i not in dropped:
                out.append(line)
        return backend._assembler(out)

    def _bf_trash_settle_series(self, config, url, carrier_uid, etag=None, text=None):
        """Tell the sync the series it is about to read again is ours.

        Our own write changed the resource's ETag (the pull would read it
        again) and, for an UNTIL, its rule (the pull would raze the series
        and recreate it, losing what is attached to the dates that remain).
        The carrier takes the ETag our PUT returned and the fingerprint of the
        master we wrote, computed by the sync's own reader. A GET only when
        the server returned no ETag. Returns the ETag.
        """
        backend = self.env["calendar.caldav.backend"]
        sync = self.env["nextcloud.calendar.sync.config"]
        if not etag:
            text, etag = backend._fetch(config, url)
            if text is None:
                return False
        carrier = self.sudo().search([
            ("x_nc_uid", "=", carrier_uid), ("x_nc_calendar_id", "=", config.id),
            ("x_caldav_href", "!=", False),
        ], limit=1) if carrier_uid else self.browse()
        if carrier:
            vals = {
                "x_caldav_etag": self._normalize_caldav_etag(etag) if etag else False,
                "x_last_sync": fields.Datetime.now(),
            }
            if text:
                vals["x_nc_serie_sig"] = self._bf_nc_signature(sync._parse_ics_vevent(text) or {})
            carrier.with_context(skip_nc_sync=True, no_mail_to_attendees=True).write(vals)
        return etag or False

    # ------------------------------------------------------------------
    # Deleting for good
    # ------------------------------------------------------------------

    def _bf_purge(self):
        """Delete these trashed meetings for good, Nextcloud and Google first.

        A meeting still in Nextcloud (removal failed, push transport off,
        archived before the trash existed) would otherwise be brought back as
        new by the next pull. If it cannot be removed there, it is not deleted
        here either: said, and left in the trash.
        """
        # The right to delete first: the remote removal below happens before
        # `unlink`, which is where the record rules would otherwise speak. A
        # private meeting of someone else must not leave Nextcloud and then
        # fail to leave Odoo.
        self.check_access("unlink")
        caldav = self._push_via_caldav_enabled()
        backend = self.env["calendar.caldav.backend"]
        gone = self.sudo().filtered(lambda e: e.bf_trash_nc_state in ("deleted", "edited"))
        gone = self.browse(gone.ids)
        for event in self - gone:
            try:
                calendar = event.x_nc_calendar_id.sudo()
                if not calendar or not event.x_nc_uid and not event.x_caldav_href:
                    continue
                if event._bf_trash_shares_resource():
                    # An archived duplicate (entered the trash at install):
                    # the resource belongs to another meeting too, often the
                    # live one. Deleted here only.
                    continue
                if calendar.backend_type == "google":
                    if not event.recurrence_id and calendar.sync_direction != "nc_to_odoo":
                        self.env["calendar.google.backend"].push_delete(calendar, event.x_nc_uid)
                    continue
                config = event._bf_nc_config_pousse()
                if not config or not caldav:
                    continue
                if not event.recurrence_id:
                    url = backend._resource_url(config, event)[0]
                    if url and backend._fetch(config, url)[0] is not None:
                        backend.push_delete(config, url=url)
                    continue
                carrier = event._bf_nc_porteur()
                origin = event._bf_nc_origine()
                url = backend._resource_url(config, carrier)[0] if carrier else None
                if url and origin and backend._fetch(config, url)[0] is not None:
                    etag = backend._exclure_occurrence(config, url, [origin], event.allday)
                    self._bf_trash_settle_series(config, url, carrier.x_nc_uid, etag)
            except Exception as e:  # noqa: BLE001
                _logger.error("Purge: %s still in the calendar: %s", event.id, e)
                raise UserError(_(
                    "“%s” could not be removed from Nextcloud, so it was not deleted "
                    "for good: it would come back with the next synchronisation. "
                    "It is still in the trash; try again later.", event.name or "",
                )) from e
        return super(CalendarEvent, self.with_context(
            bf_trash_purge=True, skip_nc_sync=True, skip_google_sync=True,
            active_test=True)).unlink()

    def _bf_trash_shares_resource(self):
        """Does another meeting, live or not, hold the same remote resource?

        The same uid in any calendar (a Google id is global, and the Google
        pull looks it up across calendars), or the same Nextcloud address.
        """
        self.ensure_one()
        domain = [("id", "!=", self.id)]
        keys = []
        if self.x_nc_uid:
            keys.append(("x_nc_uid", "=", self.x_nc_uid))
        if self.x_caldav_href:
            keys.append(("x_caldav_href", "=", self.x_caldav_href))
        if not keys:
            return False
        if len(keys) == 2:
            domain += ["|"] + keys
        else:
            domain += keys
        return bool(self.sudo().with_context(active_test=False).search_count(domain, limit=1))

    # ------------------------------------------------------------------
    # Restoring
    # ------------------------------------------------------------------

    def _bf_restore(self, nextcloud=True):
        """Bring back the whole gesture these meetings were trashed by.

        ``nextcloud=False``: the meeting is coming back FROM Nextcloud (it was
        restored there); nothing is pushed.
        """
        batches = [b for b in set(self.mapped("bf_trash_batch")) if b]
        events = self.with_context(active_test=False).search([
            ("bf_trash_batch", "in", batches), ("active", "=", False),
        ]) | self
        events.check_access("write")
        book = {e.id: {
            "batch": e.bf_trash_batch, "state": e.bf_trash_nc_state, "mode": e.bf_trash_nc_mode,
            "ics": e.bf_trash_nc_ics, "url": e.bf_trash_nc_url, "etag": e.bf_trash_nc_etag,
            "rrule": e.bf_trash_nc_rrule, "recurrence": e.bf_trash_recurrence_id,
            "end": e.bf_trash_recurrence_end,
        } for e in events.sudo()}
        quiet = events.with_context(skip_nc_sync=True, bf_trash_bypass=True,
                                    bf_trash_restoring=True)
        quiet.write({"active": True})
        for recurrence in {b["recurrence"] for b in book.values() if b["recurrence"]}:
            # Detached by core's "this one and the following ones": back into
            # their series, which ends where it ended before.
            back = events.filtered(lambda e: book[e.id]["recurrence"] == recurrence
                                   and not e.recurrence_id)
            if not back or not recurrence.exists():
                continue
            end = json.loads(book[back[:1].id]["end"] or "{}")
            if end.get("end_type"):
                recurrence.write({k: end[k] for k in ("end_type", "count", "until") if k in end})
            quiet.browse(back.ids).write({"recurrence_id": recurrence.id, "recurrency": True})
        quiet.write(dict(STAMPS))
        events.sudo().with_context(bf_trash_restoring=True).write(dict(BOOKKEEPING))
        if not nextcloud:
            return events
        # Already back in Odoo under the same address (archived twin from
        # before the trash): two meetings must not share one resource.
        detached = set()
        for event in events.filtered(lambda e: not e.recurrence_id and e.x_nc_uid):
            twin = self.sudo().with_context(active_test=True).search([
                ("id", "!=", event.id), ("x_nc_uid", "=", event.x_nc_uid),
                ("x_nc_calendar_id", "=", event.x_nc_calendar_id.id),
                ("active", "=", True),
            ], limit=1)
            if twin:
                event.with_context(skip_nc_sync=True).write(
                    {"x_nc_uid": False, "x_caldav_href": False, "x_caldav_etag": False})
                self._bf_trash_warn_restored_here(event.name or "", "twin")
                detached.add(event.id)
        done = set(detached)
        for event in events:
            b = book[event.id]
            if b["mode"] == "google" and b["state"] == "deleted":
                event._bf_restore_google()
                done.add(event.id)
            elif event.id in detached:
                continue
            elif b["mode"] in ("delete", "until") and b["state"] in ("deleted", "edited") and b["ics"]:
                batch_events = events.filtered(lambda e: book[e.id]["batch"] == b["batch"])
                self._bf_restore_nc(event, b, batch_events)
                done.update(batch_events.ids)
        rest = events.filtered(lambda e: e.id not in done)
        # A meeting of its own with no copy (archived before the trash, gone
        # there already): written back from Odoo, or the next full pull would
        # sweep it as an orphan.
        for event in rest.filtered(lambda e: not e.recurrence_id and e.x_caldav_href):
            event._bf_restore_nc_from_odoo()
        # Dates of a series (excluded by the trash, or deleted in Nextcloud):
        # their EXDATE goes, or the next pull would send them straight back.
        dates = rest.filtered("recurrence_id")
        if dates:
            copies = [b["ics"] for b in book.values() if b["ics"] and b["mode"] == "exdate"]
            dates._bf_restore_nc_exdates(copies)
        return events

    def _bf_restore_nc(self, anchor, b, batch_events):
        """Put back what the trash removed: the whole resource, or the series' end."""
        backend = self.env["calendar.caldav.backend"]
        sync = self.env["nextcloud.calendar.sync.config"]
        title = anchor.name or ""
        config = anchor._bf_nc_config_pousse() if anchor.x_nc_calendar_id else None
        if not config:
            carrier = batch_events.filtered("recurrence_id")[:1]._bf_nc_porteur()
            config = carrier._bf_nc_config_pousse() if carrier else None
        url, ics = b["url"], b["ics"]
        if not config or not url or not backend._url_de_la_collection(config, url):
            self._bf_trash_warn_restored_here(title, "unreachable")
            return
        uid = (sync._parse_ics_vevent(ics) or {}).get("uid")
        try:
            if b["mode"] == "delete":
                # Put back only where nothing has taken its place.
                response = requests.put(
                    url, data=ics.encode("utf-8"),
                    headers={"Content-Type": "text/calendar; charset=utf-8",
                             "If-None-Match": "*", **cb.NO_SCHEDULING_HEADERS},
                    auth=backend._auth(config), timeout=cb.CALDAV_TIMEOUT)
                if response.status_code == 412:
                    self._bf_trash_warn_restored_here(title, "changed")
                    return
                response.raise_for_status()
                etag = (response.headers.get("ETag") or "").strip() or None
                self._bf_trash_settle_series(config, url, uid, etag, ics)
            else:
                # The series' end only, on the resource as it is now: dates
                # trashed since and changes made elsewhere all stay. Refused if
                # the rule is no longer the one the trash wrote.
                snap_lines, _snap_master, snap_exc = backend._decouper(ics)
                snap_rrule = self._bf_trash_master_rrule(ics)
                snap_exceptions = {rid: snap_lines[d:f + 1] for d, f, rid in snap_exc}
                written, refused = {}, {}

                def transformer(text):
                    lines, master, exceptions = backend._decouper(text)
                    if master is None or self._bf_trash_master_rrule(text) != b["rrule"]:
                        refused["rule"] = True
                        return None
                    s, f = master
                    block = [snap_rrule if cb.nom_prop(line) == "RRULE" else line
                             for line in lines[s:f + 1]]
                    present = {rid for _d, _f, rid in exceptions}
                    out = lines[:s] + block
                    for rid, blk in snap_exceptions.items():
                        if rid not in present:
                            out += blk
                    written["text"] = backend._assembler(out + lines[f + 1:])
                    return written["text"]

                etag = backend._retoucher(config, url, transformer)
                if refused:
                    self._bf_trash_warn_restored_here(title, "changed")
                    return
                self._bf_trash_settle_series(config, url, uid, etag, written.get("text"))
            config.update_sync_status("success", "CalDAV restore: %s" % title)
        except Exception as e:  # noqa: BLE001
            _logger.error("Restore: Nextcloud not updated for %s (%s): %s", title, url, e)
            config.update_sync_status("error", "CalDAV restore: %s" % e)
            self._bf_nc_avertir(title)

    def _bf_restore_nc_exdates(self, copies=()):
        """Remove the EXDATE of these dates; put back the exception a moved date had.

        ``copies``: the resource as the trash found it. A date that had been
        moved had its own VEVENT (RECURRENCE-ID) there; without it, Nextcloud
        would show the date back at the rule's time while Odoo shows it moved.
        """
        if not self._push_via_caldav_enabled():
            return
        backend = self.env["calendar.caldav.backend"]
        sync = self.env["nextcloud.calendar.sync.config"]
        series = {}
        for event in self:
            carrier = event._bf_nc_porteur()
            config = carrier._bf_nc_config_pousse() if carrier else None
            url = backend._resource_url(config, carrier)[0] if config else None
            origin = event._bf_nc_origine()
            if url and origin:
                series.setdefault((config, url, carrier.x_nc_uid, event.name or ""), []).append(origin)
        for (config, url, uid, title), origins in series.items():
            def transformer(text, origins=origins):
                lines, master, exceptions = backend._decouper(text)
                if master is None:
                    return None
                vtz = sync._ics_vtimezones(text)
                start, end = master
                block = lines[start:end + 1]
                changed = False
                present = [rid for _d, _f, rid in exceptions]
                extra = []
                for copy in copies:
                    c_lines, _c_master, c_exc = backend._decouper(copy)
                    for d, f, rid in c_exc:
                        journee = cb.journee_de(cb.params_prop(rid))
                        if rid not in present and any(backend._meme_instant(
                                rid, o, journee, sync._ics_vtimezones(copy)) for o in origins):
                            extra.append(c_lines[d:f + 1])
                            present.append(rid)
                for i, line in cb.props_de_tete(block):
                    if cb.nom_prop(line) != "EXDATE":
                        continue
                    params = cb.params_prop(line)
                    allday = cb.journee_de(params)
                    values = [v.strip() for v in cb.valeur_prop(line).split(",") if v.strip()]
                    kept = [v for v in values if not any(backend._meme_instant(
                        "EXDATE%s:%s" % (params, v), o, allday, vtz) for o in origins)]
                    if kept != values:
                        changed = True
                        block[i] = "EXDATE%s:%s" % (params, ",".join(kept)) if kept else None
                if not changed and not extra:
                    return None
                block = [line for line in block if line is not None]
                for blk in extra:
                    block = block + blk
                return backend._assembler(lines[:start] + block + lines[end + 1:])
            try:
                text, _etag = backend._fetch(config, url)
                if text is None:
                    # The whole series was deleted there: Odoo cannot write a
                    # series back to Nextcloud. Restored here, said.
                    self._bf_trash_warn_restored_here(title, "series_gone")
                    continue
                etag = backend._retoucher(config, url, transformer)
                if etag:
                    self._bf_trash_settle_series(config, url, uid, etag)
            except Exception as e:  # noqa: BLE001
                _logger.error("Restore: EXDATE not removed for %s (%s): %s", title, url, e)
                self._bf_nc_avertir(title)

    def _bf_restore_nc_from_odoo(self):
        self.ensure_one()
        config = self._bf_nc_config_pousse()
        if not config or not self._push_via_caldav_enabled():
            return
        backend = self.env["calendar.caldav.backend"]
        try:
            url = backend._resource_url(config, self)[0]
            if not url:
                return
            text, _etag = backend._fetch(config, url)
            if text is not None:
                return
            self._push_caldav("create", self, config)
        except Exception as e:  # noqa: BLE001
            _logger.error("Restore: %s not written back to Nextcloud: %s", self.id, e)
            self._bf_nc_avertir_echec()

    def _bf_restore_google(self):
        """Deleted from Google by the trash: created there again (a new id)."""
        self.ensure_one()
        config = self.x_nc_calendar_id.sudo()
        try:
            gid, etag = self.env["calendar.google.backend"].push_create(config, self)
            self.with_context(skip_nc_sync=True, skip_google_sync=True,
                              no_mail_to_attendees=True).write({
                "x_nc_uid": gid, "x_caldav_etag": etag, "x_last_sync": fields.Datetime.now()})
        except Exception as e:  # noqa: BLE001
            _logger.error("Restore: %s not written back to Google: %s", self.id, e)
            self._bf_trash_warn_restored_here(self.name or "", "unreachable")

    @api.model
    def _bf_trash_warn_restored_here(self, title, why):
        user = self.env.user
        if not hasattr(user, "_bus_send"):
            return
        messages = {
            "changed": _("“%s” is back in Odoo, but it was not put back in your other "
                         "calendars: Nextcloud has changed since it was moved to the "
                         "trash, and was left as it is.", title),
            "series_gone": _("“%s” is back in Odoo, but its series no longer exists in "
                             "Nextcloud: your other calendars will not show it.", title),
            "unreachable": _("“%s” is back in Odoo, but could not be put back in your "
                             "other calendars.", title),
            "twin": _("“%s” is back in Odoo only: another meeting already holds its "
                      "place in your other calendars.", title),
        }
        user._bus_send("simple_notification", {
            "type": "warning",
            "sticky": True,
            "title": _("Restored in Odoo only"),
            "message": messages[why],
        })

    # ------------------------------------------------------------------
    # Deleted in Nextcloud, and brought back there
    # ------------------------------------------------------------------

    def _bf_nc_retirer_exclues(self, occurrences, ctx):
        """A date deleted in Nextcloud goes to the trash (series unchanged)."""
        if not self.env.context.get("bf_nc_exclusion_neuve") \
                or self.env.context.get("bf_trash_bypass"):
            return super()._bf_nc_retirer_exclues(occurrences, ctx)
        occurrences.with_context(**ctx)._bf_trash(
            "self_only", nextcloud=False, origin="nextcloud")

    def _bf_nc_archivee_revient(self, data):
        """Deleted in Nextcloud, then restored there: it comes back here too."""
        self.ensure_one()
        if self.bf_trashed_at and self.bf_trash_origin == "nextcloud":
            self.sudo()._bf_restore(nextcloud=False)
            return True
        return super()._bf_nc_archivee_revient(data)

    @api.model
    def delete_from_nextcloud(self, nc_uid, config_id):
        """A meeting deleted in Nextcloud lands in the trash, its series with it."""
        if self.env.context.get("bf_trash_bypass"):
            return super().delete_from_nextcloud(nc_uid, config_id)
        config = self.env["nextcloud.calendar.sync.config"].browse(config_id)
        if not config.exists() or config.sync_direction == "odoo_to_nc":
            return super().delete_from_nextcloud(nc_uid, config_id)
        event = self.search([
            ("x_nc_uid", "=", nc_uid), ("x_nc_calendar_id", "=", config_id),
        ], limit=1)
        if not event:
            return super().delete_from_nextcloud(nc_uid, config_id)
        name = event.name
        targets = event.recurrence_id.calendar_event_ids if event.recurrence_id else event
        targets.with_context(tracking_disable=True, dont_notify=True)._bf_trash(
            "all_events", nextcloud=False, origin="nextcloud")
        config.update_sync_status("success", f"Moved to trash: {name}")
        return {"deleted": True, "name": name, "trashed": True}
