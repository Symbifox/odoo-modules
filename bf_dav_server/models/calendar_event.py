# Part of bf_dav_server. See LICENSE file for full copyright and licensing details.
import hashlib
import re
import uuid

from odoo import api, fields, models
from odoo.tools.sql import create_index

SAFE_NAME = re.compile(r"^[A-Za-z0-9._@-]{1,200}$")
# Fields whose change can move an event in or out of a collection, or rename it.
MEMBERSHIP_FIELDS = {"partner_ids", "attendee_ids", "bf_dav_calendar_id", "recurrence_id",
                     "active", "user_id", "bf_dav_href"}


def new_uid():
    return str(uuid.uuid4())


def href_for_uid(uid, ext=".ics"):
    if uid and SAFE_NAME.match(uid):
        return uid + ext
    return hashlib.sha1((uid or new_uid()).encode("utf-8")).hexdigest() + ext


def _journal_rows(env, event_ids):
    """(collection_key, resource_key, href) for every collection showing these events."""
    if not event_ids:
        return []
    env["calendar.event"].flush_model()
    env["calendar.attendee"].flush_model()
    env["calendar.recurrence"].flush_model()
    env["bf.dav.calendar"].flush_model()
    env.cr.execute("""
        SELECT DISTINCT 'cal:' || c.id,
               CASE WHEN e.recurrence_id IS NULL THEN 'e' || e.id ELSE 'r' || e.recurrence_id END,
               COALESCE(r.bf_dav_href, e.bf_dav_href)
          FROM calendar_event e
          LEFT JOIN calendar_recurrence r ON r.id = e.recurrence_id
          JOIN bf_dav_calendar c ON (
                c.id = e.bf_dav_calendar_id
             OR (c.is_default AND c.owner_id IN (
                    SELECT u.id FROM calendar_attendee a
                      JOIN res_users u ON u.partner_id = a.partner_id
                     WHERE a.event_id = e.id))
             OR (e.bf_dav_calendar_id IS NULL AND c.is_default AND c.owner_id = e.user_id))
         WHERE e.id = ANY(%s)
    """, [list(event_ids)])
    return env.cr.fetchall()


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    bf_dav_calendar_id = fields.Many2one(
        "bf.dav.calendar", string="Calendars", index=True, ondelete="set null",
        help="Calendar this event lives in for CalDAV clients.")
    bf_dav_uid = fields.Char("iCalendar UID", copy=False, index=True)
    bf_dav_href = fields.Char("CalDAV resource name", copy=False)
    bf_dav_recurrence_id = fields.Datetime(
        "Original occurrence start (CalDAV)", copy=False,
        help="RECURRENCE-ID: the start this occurrence had in its series before any move.")

    # ------------------------------------------------------------------
    # Defaults and identity
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("bf_dav_uid"):
                vals["bf_dav_uid"] = new_uid()
            if not vals.get("bf_dav_href"):
                vals["bf_dav_href"] = href_for_uid(vals["bf_dav_uid"])
            if vals.get("recurrence_id") and not vals.get("bf_dav_recurrence_id") and vals.get("start"):
                vals["bf_dav_recurrence_id"] = vals["start"]
            if "bf_dav_calendar_id" not in vals:
                cal = self._bf_dav_home_for(vals.get("user_id"))
                if cal:
                    vals["bf_dav_calendar_id"] = cal.id
            self._bf_dav_color_vals(vals)
        events = super().create(vals_list)
        events._bf_dav_after_change()
        return events

    @api.model
    def _bf_dav_home_for(self, user_id):
        """Default collection of the organizer, for an internal active user only."""
        user = self.env["res.users"].sudo().browse(user_id) if user_id else self.env.user
        if not user or user.share or not user.active or user._is_superuser():
            return self.env["bf.dav.calendar"]
        if self.env.context.get("install_mode") and not self.env["bf.dav.calendar"].sudo().search_count([]):
            return self.env["bf.dav.calendar"]
        return self.env["bf.dav.calendar"]._bf_dav_default_for(user)

    @api.model
    def _bf_dav_color_vals(self, vals):
        """A new event, or one moved to another calendar, wears its calendar's color."""
        if vals.get("bf_dav_calendar_id") and "color" not in vals and "color" in self._fields:
            cal = self.env["bf.dav.calendar"].sudo().browse(vals["bf_dav_calendar_id"])
            if cal.exists() and cal.odoo_color:
                vals["color"] = cal.odoo_color

    def write(self, vals):
        if vals.get("bf_dav_calendar_id"):
            vals = dict(vals)
            self._bf_dav_color_vals(vals)
        if self.env.context.get("bf_dav_no_journal"):
            return super().write(vals)
        membership = bool(MEMBERSHIP_FIELDS & set(vals)) or "recurrence_update" in vals
        before = _journal_rows(self.env, self.ids) if membership else []
        # Series identity survives Odoo rebuilding a series ("all events" with a
        # time change archives every occurrence and creates a new recurrence).
        series = {ev.id: (ev.recurrence_id.id, ev.recurrence_id.bf_dav_uid, ev.recurrence_id.bf_dav_href)
                  for ev in self if ev.recurrence_id and ev.recurrence_id.bf_dav_uid}
        for ev in self:
            base = ev.recurrence_id.base_event_id
            if base and base.id not in series and ev.id in series:
                series[base.id] = series[ev.id]
        bases = {ev.recurrence_id.base_event_id.id: ev.id for ev in self
                 if ev.recurrence_id and ev.recurrence_id.base_event_id}
        # Odoo pops recurrence_update from the dict it is given: decide now.
        series_change = any(k in vals for k in ("recurrence_id", "recurrence_update", "rrule", "recurrency"))
        joins_series = bool(vals.get("recurrence_id"))
        res = super().write(vals)
        if joins_series:
            # Joining a series (new or rebuilt): the occurrence is where it starts now.
            for ev in self:
                super(CalendarEvent, ev).write({"bf_dav_recurrence_id": ev.start})
        if series_change:
            self._bf_dav_keep_series_identity(series, bases)
        self.env["bf.dav.change"]._record_many(before)
        self._bf_dav_after_change()
        return res

    def _bf_dav_keep_series_identity(self, series, bases):
        # Odoo rebuilds a series on its base event, which may not be the
        # occurrence that was edited ("all events" from any occurrence).
        base_events = self.browse([b for b in bases if b not in self.ids])
        for ev in (self | base_events).exists():
            rec = ev.recurrence_id
            old = series.get(ev.id) or series.get(bases.get(ev.id) or 0)
            replaced = old and rec and rec.id != old[0] and \
                not self.env["calendar.recurrence"].browse(old[0]).exists()
            if replaced and rec.bf_dav_uid != old[1]:
                rec.with_context(bf_dav_no_journal=True).write(
                    {"bf_dav_uid": old[1], "bf_dav_href": old[2]})
                # What apps keep on the object follows it to its new key.
                for model in ("bf.dav.user.data", "bf.dav.resource.extra"):
                    self.env[model].sudo().search([("resource_key", "=", "r%d" % old[0])]).write(
                        {"resource_key": "r%d" % rec.id})
            elif rec and not rec.bf_dav_uid:
                rec._bf_dav_ensure_identity()
            if rec and not ev.bf_dav_recurrence_id:
                super(CalendarEvent, ev).write({"bf_dav_recurrence_id": ev.start})
            if not rec and old and ev.bf_dav_uid == old[1]:
                # Left its series: it is a resource of its own now.
                uid = new_uid()
                super(CalendarEvent, ev).write({"bf_dav_uid": uid, "bf_dav_href": href_for_uid(uid)})

    def unlink(self):
        rows = _journal_rows(self.env, self.ids)
        keys = {"e%d" % ev.id for ev in self if not ev.recurrence_id}
        res = super().unlink()
        self.env["bf.dav.change"]._record_many(rows)
        if keys:
            self.env["bf.dav.user.data"].sudo().search([("resource_key", "in", list(keys))]).unlink()
            self.env["bf.dav.resource.extra"].sudo().search([("resource_key", "in", list(keys))]).unlink()
        return res

    def _bf_dav_after_change(self):
        if self.env.context.get("bf_dav_no_journal"):
            return
        self.env["bf.dav.change"]._defer_events(self.ids)

    # ------------------------------------------------------------------
    # Resource helpers
    # ------------------------------------------------------------------

    def _bf_dav_resource_key(self):
        self.ensure_one()
        return "r%d" % self.recurrence_id.id if self.recurrence_id else "e%d" % self.id


class CalendarRecurrence(models.Model):
    _inherit = "calendar.recurrence"

    bf_dav_uid = fields.Char("iCalendar UID", copy=False, index=True)
    bf_dav_href = fields.Char("CalDAV resource name", copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        # The UID is set after the base event is attached (see calendar.event write),
        # so that a rebuilt series keeps the UID of the one it replaces.
        return recs

    def _bf_dav_ensure_identity(self):
        for rec in self.filtered(lambda r: not r.bf_dav_uid):
            uid = new_uid()
            rec.with_context(bf_dav_no_journal=True).write({"bf_dav_uid": uid, "bf_dav_href": href_for_uid(uid)})

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get("bf_dav_no_journal") and self.calendar_event_ids:
            self.env["bf.dav.change"]._defer_events(self.calendar_event_ids.ids)
        return res


class CalendarAttendee(models.Model):
    _inherit = "calendar.attendee"

    def init(self):
        # Odoo does not index attendees by event: listing a calendar of 12 000
        # events otherwise reads the whole attendee table once per event.
        create_index(self.env.cr, "calendar_attendee_bf_dav_event_partner_idx", self._table,
                     ["event_id", "partner_id"])

    @api.model_create_multi
    def create(self, vals_list):
        atts = super().create(vals_list)
        if not self.env.context.get("bf_dav_no_journal"):
            self.env["bf.dav.change"]._defer_events(atts.event_id.ids)
        return atts

    @api.model
    def _bf_dav_color_vals(self, vals):
        """A new event, or one moved to another calendar, wears its calendar's color."""
        if vals.get("bf_dav_calendar_id") and "color" not in vals and "color" in self._fields:
            cal = self.env["bf.dav.calendar"].sudo().browse(vals["bf_dav_calendar_id"])
            if cal.exists() and cal.odoo_color:
                vals["color"] = cal.odoo_color

    def write(self, vals):
        if vals.get("bf_dav_calendar_id"):
            vals = dict(vals)
            self._bf_dav_color_vals(vals)
        if self.env.context.get("bf_dav_no_journal"):
            return super().write(vals)
        before = _journal_rows(self.env, self.event_id.ids) if "partner_id" in vals else []
        res = super().write(vals)
        self.env["bf.dav.change"]._record_many(before)
        self.env["bf.dav.change"]._defer_events(self.event_id.ids)
        return res

    def unlink(self):
        events = self.event_id
        rows = _journal_rows(self.env, events.ids) if not self.env.context.get("bf_dav_no_journal") else []
        res = super().unlink()
        self.env["bf.dav.change"]._record_many(rows)
        if rows:
            self.env["bf.dav.change"]._defer_events(events.ids)
        return res
