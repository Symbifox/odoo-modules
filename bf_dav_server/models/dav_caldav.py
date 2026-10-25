# Part of bf_dav_server. See LICENSE file for full copyright and licensing details.
"""CalDAV: calendar collections, objects, reminder state, scheduling outbox."""

import collections
import datetime
import itertools
import logging
import re
import threading
import time

import pytz
from dateutil import rrule as du_rrule

from urllib.parse import quote

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import email_normalize, html2plaintext, is_html_empty, plaintext2html

from ..dav import ical
from ..dav import xmlutil as X
from ..dav.xmlutil import C, D
from .dav_service import WRITE_LEVELS, DavError, Node

_logger = logging.getLogger(__name__)

PARTSTAT_TO_ODOO = {"ACCEPTED": "accepted", "DECLINED": "declined", "TENTATIVE": "tentative",
                    "NEEDS-ACTION": "needsAction"}
ODOO_TO_PARTSTAT = {v: k for k, v in PARTSTAT_TO_ODOO.items()}
KLASS_TO_ODOO = {"PUBLIC": "public", "PRIVATE": "private", "CONFIDENTIAL": "confidential"}
ALLOWED_RRULE_PARTS = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY", "BYMONTH", "WKST"}
TIME_KEYS = ("start", "stop", "start_date", "stop_date", "allday")
# A client that creates more objects than this in the window is uploading a
# local calendar (first sync of a new device, migration): invitations stop.
BURST_MAX, BURST_WINDOW = 5, 120
_BURST = collections.defaultdict(collections.deque)
_BURST_LOCK = threading.Lock()


def _rid_key(value, allday=False):
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y%m%d") if allday else value.strftime("%Y%m%dT%H%M")
    return value.strftime("%Y%m%d")


def _max_dt(*values):
    values = [v for v in values if v]
    return max(values) if values else None


class BfDavServiceCaldav(models.AbstractModel):
    _inherit = "bf.dav.service"

    # ------------------------------------------------------------------
    # Members
    # ------------------------------------------------------------------

    def _cal_members(self, node, keys=None, hrefs=None, time_range=None, uids=None):
        cal, viewer = node.cal, self.env.user
        owner = cal.owner_id
        self.env["calendar.event"].flush_model()
        self.env["calendar.attendee"].flush_model()
        self.env["calendar.recurrence"].flush_model()
        filters, params = [], {
            "cal": cal.id, "owner": owner.id, "partner": owner.partner_id.id, "is_default": bool(cal.is_default)}
        if keys is not None:
            eids = [int(k[1:]) for k in keys if k[:1] == "e" and k[1:].isdigit()]
            rids = [int(k[1:]) for k in keys if k[:1] == "r" and k[1:].isdigit()]
            filters.append("(e.id = ANY(%(eids)s) OR e.recurrence_id = ANY(%(rids)s))")
            params.update(eids=eids, rids=rids)
        if hrefs is not None:
            filters.append("(e.bf_dav_href = ANY(%(hrefs)s) OR e.recurrence_id IN "
                           "(SELECT id FROM calendar_recurrence WHERE bf_dav_href = ANY(%(hrefs)s)))")
            params["hrefs"] = list(hrefs)
        if uids is not None:
            filters.append("((e.recurrence_id IS NULL AND e.bf_dav_uid = ANY(%(uids)s)) OR e.recurrence_id IN "
                           "(SELECT id FROM calendar_recurrence WHERE bf_dav_uid = ANY(%(uids)s)))")
            params["uids"] = list(uids)
        extra = (" AND " + " AND ".join(filters)) if filters else ""
        having = ""
        if time_range:
            start, end = time_range
            if start:
                having += " AND max(ev.stop) > %(tr_start)s"
                params["tr_start"] = start
            if end:
                having += " AND min(ev.start) < %(tr_end)s"
                params["tr_end"] = end
        self.env.cr.execute("""
            WITH ev AS (
                SELECT e.id, e.recurrence_id, e.write_date, e.bf_dav_href, e.start, e.stop,
                       (SELECT max(a.write_date) FROM calendar_attendee a WHERE a.event_id = e.id) AS att_wd
                  FROM calendar_event e
                 WHERE e.active AND (
                       e.bf_dav_calendar_id = %(cal)s
                    OR (%(is_default)s
                        AND (e.bf_dav_calendar_id IS NULL OR e.bf_dav_calendar_id NOT IN
                             (SELECT id FROM bf_dav_calendar WHERE owner_id = %(owner)s))
                        AND (EXISTS (SELECT 1 FROM calendar_attendee a
                                      WHERE a.event_id = e.id AND a.partner_id = %(partner)s)
                             OR (e.bf_dav_calendar_id IS NULL AND e.user_id = %(owner)s))))
                 """ + extra + """
            )
            SELECT CASE WHEN ev.recurrence_id IS NULL THEN 'e' || ev.id ELSE 'r' || ev.recurrence_id END,
                   COALESCE(max(r.bf_dav_href), max(ev.bf_dav_href)),
                   max(ev.write_date), max(ev.att_wd), count(*), sum(ev.id), max(r.write_date)
              FROM ev LEFT JOIN calendar_recurrence r ON r.id = ev.recurrence_id
             GROUP BY 1
            HAVING true """ + having, params)
        rows = self.env.cr.fetchall()
        keys_found = [r[0] for r in rows]
        ud = {}
        if keys_found:
            self.env.cr.execute(
                "SELECT resource_key, write_date FROM bf_dav_user_data WHERE user_id = %s AND resource_key = ANY(%s)",
                [viewer.id, keys_found])
            ud = dict(self.env.cr.fetchall())
        ex = self.env["bf.dav.resource.extra"]._stamps(keys_found)
        members = []
        for key, href, wd, att_wd, count, sumid, r_wd in rows:
            if not href:
                continue
            members.append({
                "key": key, "href": href,
                "etag": self._etag(viewer.id, node.level, cal.id, key, href, wd, att_wd, count, sumid, r_wd,
                                   ud.get(key), ex.get(key)),
                "modified": _max_dt(wd, att_wd, r_wd),
            })
        return members

    # ------------------------------------------------------------------
    # Reading a resource
    # ------------------------------------------------------------------

    def _resource_events(self, key):
        Event = self.env["calendar.event"]
        if key[:1] == "e":
            return Event.browse(int(key[1:])).exists(), self.env["calendar.recurrence"]
        rec = self.env["calendar.recurrence"].browse(int(key[1:])).exists()
        events = Event.search([("recurrence_id", "=", rec.id)]) if rec else Event
        return events, rec

    def _tz_for(self, event):
        viewer = self.env.user
        candidates = []
        if event.recurrence_id and event.recurrence_id.event_tz:
            candidates.append(event.recurrence_id.event_tz)
        candidates += [event.user_id.tz, viewer.tz]
        for tz in candidates:
            if tz and tz in pytz.all_timezones_set:
                return tz
        return "UTC"

    def _alarm_state(self, events):
        """event id -> (dismissed_at, snoozed_until) for the viewer's own attendee rows."""
        Att = self.env["calendar.attendee"]
        if "bf_dismissed_at" not in Att._fields or not events:
            return {}
        atts = Att.sudo().search([("event_id", "in", events.ids),
                                  ("partner_id", "=", self.env.user.partner_id.id)])
        return {a.event_id.id: (a.bf_dismissed_at, a.bf_snoozed_until) for a in atts}

    def _notification_alarms(self, event):
        return event.alarm_ids.filtered(lambda a: a.alarm_type == "notification")

    def _trigger_time(self, event):
        alarms = self._notification_alarms(event)
        minutes = max(alarms.mapped("duration_minutes") or [0])
        return event.start - datetime.timedelta(minutes=minutes)

    def _rid_of(self, event):
        return event.bf_dav_recurrence_id or event.start

    def _is_organizer_view(self, node, event):
        """May the viewer change the content of this event (not only answer it)?"""
        viewer = self.env.user
        if node.level == "edit":
            return True
        if node.level != "owner":
            return False
        ev = event.sudo()
        # The organizer, or the owner of the calendar it lives in (events
        # synced from Nextcloud are organized by the system user). An attendee
        # never rewrites them for everyone.
        return ev.user_id == viewer or ev.bf_dav_calendar_id.owner_id == viewer

    def _effective_organizer(self, node, ev):
        """The person the meeting belongs to. Events synced from Nextcloud are
        organized by the system user: they belong to whoever owns the
        calendar they live in."""
        ev = ev.sudo()
        user = ev.user_id
        if user and user.active and not user.share and not user._is_superuser():
            return user.partner_id
        owner = ev.bf_dav_calendar_id.owner_id or (node.cal.owner_id if node and node.cal else self.env.user)
        return owner.partner_id

    def _out_event(self, ev, uid, level, hidden=False, organizer=None):
        o = ical.OutEvent(uid=uid, start=ev.start, stop=ev.stop, allday=ev.allday)
        if ev.allday:
            o.start = ev.start_date or ev.start.date()
            o.stop = (ev.stop_date or ev.stop.date()) + datetime.timedelta(days=1)
        if hidden:
            # Someone else's private event: a busy block, as Odoo shows it.
            o.summary = self.env._("Busy")
            o.klass = "PRIVATE"
            o.transp = "TRANSPARENT" if ev.show_as == "free" else "OPAQUE"
            o.created, o.modified = ev.create_date, ev.write_date
            return o
        o.summary = ev.name or ""
        if level != "titles" and ev.description and not is_html_empty(ev.description):
            o.description = html2plaintext(ev.description).strip()
        o.location = ev.location or ""
        if not ev.location and ev.videocall_location:
            o.location = ev.videocall_location
            o.own.append("X-BF-LOCATION-IS-VIDEOCALL:TRUE")
        elif ev.videocall_location:
            o.own.append("CONFERENCE;VALUE=URI;FEATURE=VIDEO:%s" % ev.videocall_location)
        o.klass = (ev.privacy or "").upper() if ev.privacy in KLASS_TO_ODOO.values() else ""
        o.transp = "TRANSPARENT" if ev.show_as == "free" else "OPAQUE"
        o.categories = ev.categ_ids.mapped("name")
        if "bf_event_status" in ev._fields and hasattr(ev, "_bf_ics_status"):
            try:
                o.status = ev._bf_ics_status() or ""
            except Exception:  # noqa: BLE001 - optional module, never break a read
                o.status = ""
        if "bf_ics_sequence" in ev._fields:
            o.sequence = ev.bf_ics_sequence or 0
        org_email = ""
        if "x_imip_organizer" in ev._fields and ev.x_imip_organizer:
            org_email = ical._mailto(ev.x_imip_organizer)
        organizer = organizer or ev.user_id.partner_id
        if org_email:
            o.organizer = (org_email, "")
        elif email_normalize(organizer.email or ""):
            o.organizer = (email_normalize(organizer.email), organizer.name or "")
        if level != "titles":
            for att in ev.attendee_ids:
                email = email_normalize(att.email or att.partner_id.email or "")
                if email:
                    o.attendees.append((email, att.partner_id.name or att.common_name or "",
                                        ODOO_TO_PARTSTAT.get(att.state, "NEEDS-ACTION")))
        o.created = ev.create_date
        o.modified = ev.write_date
        if "bf_is_hold" in ev._fields and ev.bf_is_hold:
            o.own.append("X-BF-HOLD:TRUE")
        if "bf_block_type_id" in ev._fields and ev.bf_block_type_id:
            o.own.append("X-BF-BLOCK-TYPE:%s" % ical.escape_text(ev.bf_block_type_id.display_name))
        return o

    def _valarms_out(self, ev, uid_base, stored, ack, use_odoo, level):
        """VALARM blocks of one component for the viewer."""
        if level == "titles":
            return []
        parsed = [ical.parse_alarm(r) for r in stored]
        out, used = [], set()
        if use_odoo or not parsed:
            for alarm in self._notification_alarms(ev).sorted("duration_minutes"):
                minutes = int(alarm.duration_minutes or 0)
                match = next((i for i, p in enumerate(parsed)
                              if i not in used and p.mappable and p.minutes_before == minutes), None)
                trig = ev.start - datetime.timedelta(minutes=minutes)
                this_ack = ack if ack and ack >= trig else None
                if match is not None:
                    used.add(match)
                    out.append(ical.set_acknowledged(parsed[match].raw,
                                                     _max_dt(parsed[match].acknowledged, this_ack)))
                else:
                    out.append(ical.valarm_block("%s-a%d" % (uid_base, alarm.id), minutes,
                                                 ev.name or alarm.name, this_ack))
            for i, p in enumerate(parsed):
                if i not in used and not p.mappable:
                    out.append(p.raw)
        else:
            for p in parsed:
                out.append(ical.set_acknowledged(p.raw, _max_dt(p.acknowledged, ack)) if p.mappable else p.raw)
        return out

    def _rrule_out(self, rec, tz, allday):
        """RRULE as served: Odoo's rule without its own UNTIL, and an UNTIL
        rebuilt from ``rec.until`` (a date in the meeting's zone): the end of
        that day, in UTC for a timed series (RFC 5545 §3.3.10)."""
        lines = [ln for ln in (rec.rrule or "").splitlines() if ln and not ln.upper().startswith("DTSTART")]
        rule = (lines[-1] if lines else "").removeprefix("RRULE:")
        parts = [p for p in rule.split(";") if p and not p.upper().startswith("UNTIL=")]
        if rec.end_type == "end_date" and rec.until:
            if allday:
                parts.append("UNTIL=%s" % rec.until.strftime("%Y%m%d"))
            else:
                zone = pytz.timezone(tz if tz in pytz.all_timezones_set else "UTC")
                end = zone.localize(datetime.datetime.combine(rec.until, datetime.time(23, 59, 59)))
                parts.append("UNTIL=%s" % ical.fmt_utc(end))
        return ";".join(parts)

    @staticmethod
    def _rule_key(rule, tz):
        """Compare two rules by meaning, not by spelling."""
        parts = {}
        for item in (rule or "").removeprefix("RRULE:").split(";"):
            if "=" not in item:
                continue
            k, v = item.split("=", 1)
            k, v = k.strip().upper(), v.strip().upper()
            if k == "WKST" or (k == "INTERVAL" and v == "1"):
                continue
            if k == "BYDAY":
                v = ",".join(sorted(v.split(",")))
            if k == "UNTIL":
                if len(v) == 8:
                    v = v
                else:
                    dt = ical.parse_utc_stamp(v)
                    if dt and not v.endswith("Z") and tz in pytz.all_timezones_set:
                        dt = pytz.timezone(tz).localize(dt).astimezone(pytz.utc).replace(tzinfo=None)
                    v = dt.strftime("%Y%m%dT%H%M") if dt else v
            parts[k] = v
        return sorted(parts.items())

    def _shape(self, occ):
        return (occ.name or "", occ.description or "", occ.location or "", occ.privacy or "", occ.show_as or "",
                bool(occ.allday), occ.videocall_location or "", occ.stop - occ.start,
                tuple(sorted(occ.categ_ids.ids)), tuple(sorted(occ.alarm_ids.ids)),
                tuple(sorted((a.partner_id.id, a.state) for a in occ.attendee_ids)))

    def _differs(self, occ, template):
        if not occ.follow_recurrence:
            return True
        for fname in ("name", "description", "location", "privacy", "show_as", "allday", "videocall_location"):
            if (occ[fname] or False) != (template[fname] or False):
                return True
        if (occ.stop - occ.start) != (template.stop - template.start):
            return True
        if set(occ.categ_ids.ids) != set(template.categ_ids.ids) or set(occ.alarm_ids.ids) != set(template.alarm_ids.ids):
            return True
        if {(a.partner_id.id, a.state) for a in occ.attendee_ids} != {(a.partner_id.id, a.state) for a in template.attendee_ids}:
            return True
        rid = self._rid_of(occ)
        return _rid_key(rid, occ.allday) != _rid_key(occ.start, occ.allday)

    def _expected_rids(self, rec, anchor, last, allday):
        if not rec.rrule_type:
            return []
        if rec.end_type in ("count", "end_date"):
            # A bounded series is unrolled to its end: an occurrence deleted at
            # the end would otherwise come back on every device.
            last = datetime.datetime(9999, 1, 1)
        try:
            occ = rec._get_occurrences(anchor)
        except (UserError, ValidationError, ValueError, TypeError):
            return []
        out = []
        for value in itertools.islice(occ, 3000):
            if isinstance(value, datetime.datetime) and value.tzinfo is not None:
                value = value.astimezone(pytz.utc).replace(tzinfo=None)
            if _rid_key(value, allday) > _rid_key(last, allday):
                break
            out.append(value)
        return out

    def _series_view(self, rec, events):
        occs = events.sorted(lambda e: (self._rid_of(e), e.id))
        # The master is the most common shape of the series, not its first
        # occurrence: one declined first meeting would otherwise turn every
        # other occurrence into an exception.
        following = occs.filtered("follow_recurrence") or occs
        counts = collections.Counter(self._shape(o) for o in following)
        best = counts.most_common(1)[0][0]
        template = next(o for o in following if self._shape(o) == best)
        allday = template.allday
        anchor = self._rid_of(occs[0])
        last = self._rid_of(occs[-1])
        expected = self._expected_rids(rec, anchor, last, allday)
        present = {_rid_key(self._rid_of(o), allday) for o in occs}
        exdates = [x for x in expected if _rid_key(x, allday) not in present]
        exceptions = occs.filtered(lambda o: self._differs(o, template))
        return {"occs": occs, "template": template, "anchor": anchor, "allday": allday,
                "exdates": exdates, "exceptions": exceptions}

    def _cal_body(self, node, member):
        return self._build_resource(node, member["key"])

    def _cal_bodies(self, node, members):
        """Build the calendar data of many members with a handful of queries."""
        todo = [m for m in members if "body" not in m]
        for i in range(0, len(todo), 250):
            chunk = todo[i:i + 250]
            batch = self._prepare_batch(node, [m["key"] for m in chunk])
            for m in chunk:
                try:
                    m["body"] = self._build_resource(node, m["key"], batch)
                except DavError:
                    m["body"] = ""

    def _prepare_batch(self, node, keys):
        """Everything the resources ``keys`` need, read in bulk.

        Reads are done as superuser after checking the viewer's access to the
        events, and other people's private events are masked here exactly as
        Odoo masks them (calendar.event._check_private_event_conditions):
        reading each field as the viewer costs a group check per field and
        per record, which made a 6 000-object calendar take minutes.
        """
        viewer = self.env.user
        cr = self.env.cr
        Event = self.env["calendar.event"].sudo()
        eids = [int(k[1:]) for k in keys if k[:1] == "e" and k[1:].isdigit()]
        rids = [int(k[1:]) for k in keys if k[:1] == "r" and k[1:].isdigit()]
        occ_rows = []
        if rids:
            cr.execute("SELECT id, recurrence_id FROM calendar_event WHERE active AND recurrence_id = ANY(%s)",
                       [rids])
            occ_rows = cr.fetchall()
        all_ids = list(dict.fromkeys(eids + [r[0] for r in occ_rows]))
        readable = set(self.env["calendar.event"].browse(all_ids)._filtered_access("read").ids) if all_ids else set()
        by_key, recs = {}, {}
        for eid in eids:
            if eid in readable:
                by_key["e%d" % eid] = Event.browse([eid]).with_prefetch(all_ids)
        grouped = collections.defaultdict(list)
        for eid, rid in occ_rows:
            if eid in readable:
                grouped[rid].append(eid)
        Rec = self.env["calendar.recurrence"].sudo()
        for rid, ids in grouped.items():
            by_key["r%d" % rid] = Event.browse(ids).with_prefetch(all_ids)
            recs["r%d" % rid] = Rec.browse(rid).with_prefetch(list(grouped))
        state = {}
        if "bf_dismissed_at" in self.env["calendar.attendee"]._fields and all_ids:
            cr.execute("SELECT event_id, bf_dismissed_at, bf_snoozed_until FROM calendar_attendee "
                       "WHERE event_id = ANY(%s) AND partner_id = %s", [all_ids, viewer.partner_id.id])
            state = {r[0]: (r[1], r[2]) for r in cr.fetchall()}
        masked = set()
        if all_ids:
            cr.execute("""
                SELECT e.id, e.privacy, e.user_id FROM calendar_event e
                 WHERE e.id = ANY(%s) AND e.user_id IS DISTINCT FROM %s
                   AND (e.privacy = 'private' OR e.privacy IS NULL)
                   AND NOT EXISTS (SELECT 1 FROM calendar_event_res_partner_rel r
                                    WHERE r.calendar_event_id = e.id AND r.res_partner_id = %s)
            """, [all_ids, viewer.id, viewer.partner_id.id])
            default_private = {}
            has_default = "calendar_default_privacy" in self.env["res.users"]._fields
            for eid, privacy, uid in cr.fetchall():
                if privacy == "private":
                    masked.add(eid)
                    continue
                if uid not in default_private:
                    default_private[uid] = bool(uid and has_default and self.env["res.users"].sudo().browse(
                        uid).calendar_default_privacy == "private")
                if default_private[uid]:
                    masked.add(eid)
        ud, extra = {}, {}
        if keys:
            for row in self.env["bf.dav.user.data"].sudo().search_read(
                    [("user_id", "=", viewer.id), ("resource_key", "in", list(keys))],
                    ["resource_key", "valarms", "moz"]):
                from .dav_store import _loads
                ud[row["resource_key"]] = (_loads(row["valarms"]), _loads(row["moz"]))
            for row in self.env["bf.dav.resource.extra"].sudo().search_read(
                    [("resource_key", "in", list(keys))], ["resource_key", "props"]):
                from .dav_store import _loads
                extra[row["resource_key"]] = _loads(row["props"])
        return {"by_key": by_key, "recs": recs, "state": state, "masked": masked, "ud": ud, "extra": extra}

    def _build_resource(self, node, key, batch=None):
        if batch is None or key not in batch["by_key"]:
            batch = self._prepare_batch(node, [key])
        events = batch["by_key"].get(key)
        if not events:
            raise DavError(404)
        rec = batch["recs"].get(key, self.env["calendar.recurrence"])
        level = node.level
        state = batch["state"]
        masked = batch["masked"]
        stored_valarms, stored_moz = batch["ud"].get(key, ({}, {}))
        extra = batch["extra"].get(key, {})
        first = events[:1]
        tzname = self._tz_for(first)
        use_odoo = self._is_organizer_view(node, first) or not stored_valarms
        if not rec:
            ev = first
            uid = ev.bf_dav_uid
            hidden = ev.id in masked
            o = self._out_event(ev, uid, level, hidden, organizer=self._effective_organizer(node, ev))
            dismissed, _snoozed = state.get(ev.id, (None, None))
            o.valarms = [] if hidden else self._valarms_out(ev, uid, stored_valarms.get("", []), dismissed,
                                                            use_odoo, level)
            o.moz = {} if hidden else self._moz_out(stored_moz, [(ev, None)], state)
            o.extra = [] if hidden or level == "titles" else list(extra.get("", []))
            outs = [o]
        else:
            uid = rec.bf_dav_uid
            view = self._series_view(rec, events)
            template, anchor, allday = view["template"], view["anchor"], view["allday"]
            hidden = template.id in masked
            org = self._effective_organizer(node, template)
            master = self._out_event(template, uid, level, hidden, organizer=org)
            duration = template.stop - template.start
            if allday:
                start_d = anchor.date() if isinstance(anchor, datetime.datetime) else anchor
                days = ((template.stop_date or template.stop.date())
                        - (template.start_date or template.start.date())).days
                master.start, master.stop = start_d, start_d + datetime.timedelta(days=days + 1)
            else:
                master.start, master.stop = anchor, anchor + duration
            master.rrule = self._rrule_out(rec, tzname, allday)
            master.exdates = view["exdates"]
            if not hidden:
                master_ack = _max_dt(*[state.get(o.id, (None, None))[0] for o in view["occs"]
                                       if o not in view["exceptions"]])
                master.valarms = self._valarms_out(template, uid, stored_valarms.get("", []), master_ack,
                                                   use_odoo, level)
                master.moz = self._moz_out(stored_moz, [(o, self._rid_of(o)) for o in view["occs"]], state)
                master.extra = [] if level == "titles" else list(extra.get("", []))
            outs = [master]
            for occ in view["exceptions"]:
                occ_hidden = occ.id in masked
                o = self._out_event(occ, uid, level, occ_hidden, organizer=org)
                rid = self._rid_of(occ)
                o.rid = rid.date() if allday and isinstance(rid, datetime.datetime) else rid
                ck = _rid_key(rid, allday)
                if not occ_hidden:
                    o.valarms = self._valarms_out(occ, "%s-%s" % (uid, ck), stored_valarms.get(ck, []),
                                                  state.get(occ.id, (None, None))[0], use_odoo, level)
                    o.extra = [] if level == "titles" else list(extra.get(ck, []))
                o.sequence = master.sequence if o.sequence is None else o.sequence
                outs.append(o)
        stamp = max(e.write_date for e in events)
        return ical.build_calendar(outs, tzname=tzname, stamp=stamp)

    def _moz_out(self, stored_moz, occs, state):
        """Thunderbird's reminder state for the viewer, from Odoo first."""
        moz = {}
        stored_ack = ical.parse_utc_stamp(stored_moz.get("X-MOZ-LASTACK"))
        acks = [stored_ack] + [state.get(ev.id, (None, None))[0] for ev, _rid in occs]
        last = _max_dt(*acks)
        if last:
            moz["X-MOZ-LASTACK"] = ical.fmt_utc(last)
        for ev, rid in occs:
            dismissed, snoozed = state.get(ev.id, (None, None))
            if not snoozed or (dismissed and dismissed >= snoozed):
                continue
            if rid is None:
                moz["X-MOZ-SNOOZE-TIME"] = ical.fmt_utc(snoozed)
            elif isinstance(rid, datetime.datetime) and not ev.allday:
                moz["X-MOZ-SNOOZE-TIME-%d" % ical.prtime(rid)] = ical.fmt_utc(snoozed)
        return moz

    # ------------------------------------------------------------------
    # Writing a resource
    # ------------------------------------------------------------------

    def _ical_error(self, err):
        cond = err.condition
        if cond in ("max-resource-size",):
            return DavError(403, C(cond))
        if cond:
            return DavError(403, C(cond))
        return DavError(400)

    def _put_calendar_object(self, node, headers, body):
        viewer = self.env.user
        if node.level not in WRITE_LEVELS:
            raise DavError(403, D("need-privileges"))
        member = self._member_for_node(node)
        if member:
            events, rec = self._resource_events(member["key"])
            self._lock_rows("calendar_event", events.ids)
            self.env.invalidate_all()
            member = self._member_for_node(node)
        self._check_preconditions(member, headers)
        try:
            obj = ical.parse_calendar(body, viewer.tz or "UTC")
        except ical.IcalError as err:
            raise self._ical_error(err) from err
        if obj.master and obj.master.rrule:
            m = obj.master
            m.rrule = self._complete_rule(m.rrule, m.start, m.tzid if m.tzid in pytz.all_timezones_set
                                          else (viewer.tz or "UTC"), m.allday)
        coll = self._member_node(node)
        if not member:
            clash = self._cal_members(coll, uids=[obj.uid])
            if clash:
                raise DavError(403, C("no-uid-conflict"))
            self._create_from_object(node, obj)
            status = 201
        else:
            events, rec = self._resource_events(member["key"])
            current_uid = rec.bf_dav_uid if rec else events[:1].bf_dav_uid
            if current_uid and current_uid != obj.uid:
                raise DavError(403, C("no-uid-conflict"))
            self._update_from_object(node, member["key"], obj)
            status = 204
        hdrs = self._base_headers()
        return status, hdrs, b""

    # -- helpers ---------------------------------------------------------

    def _mail_context(self, obj, external_organizer):
        ctx = {}
        param = self.env["ir.config_parameter"].sudo().get_param("bf_dav_server.send_invitations", "1")
        client_agent = any(a.schedule_agent == "CLIENT" for ev in obj.events for a in ev.attendees)
        if self.env.context.get("no_mail_to_attendees") or external_organizer or client_agent \
                or param in ("0", "False", "false"):
            ctx["no_mail_to_attendees"] = True
        elif self._burst_locked():
            ctx["no_mail_to_attendees"] = True
            ctx["bf_dav_burst"] = True
        return ctx

    def _burst_locked(self, record=False):
        key = (self.env.cr.dbname, self.env.uid)
        now = time.monotonic()
        with _BURST_LOCK:
            dq = _BURST[key]
            while dq and dq[0] < now - BURST_WINDOW:
                dq.popleft()
            if record:
                dq.append(now)
            return len(dq) > BURST_MAX

    def _internal_emails(self):
        self.env.cr.execute("""
            SELECT lower(p.email) FROM res_users u JOIN res_partner p ON p.id = u.partner_id
             WHERE u.active AND u.share IS NOT TRUE AND p.email IS NOT NULL
            UNION SELECT lower(login) FROM res_users WHERE active AND share IS NOT TRUE
        """)
        return {r[0].strip() for r in self.env.cr.fetchall() if r[0]}

    def _partner_for_email(self, email, cn=""):
        Partner = self.env["res.partner"].sudo()
        self.env.cr.execute("""
            SELECT p.id FROM res_partner p LEFT JOIN res_users u ON u.partner_id = p.id
             WHERE lower(trim(p.email)) = %s AND p.active
             ORDER BY (u.id IS NOT NULL AND u.share IS NOT TRUE) DESC, p.is_company, p.id LIMIT 1
        """, [email])
        row = self.env.cr.fetchone()
        if row:
            return Partner.browse(row[0])
        return Partner.create({"name": cn or email, "email": email})

    WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")

    def _complete_rule(self, rule, start, tz, allday):
        """Spell out what a rule leaves implicit, in the meeting's own zone.

        Odoo reads the missing weekday or day of the month from the start in
        UTC: an iOS "every week" made at 9:00 in New Zealand (the evening
        before in UTC) would land on the wrong day and be refused.
        """
        if not rule:
            return rule
        items = [i for i in rule.strip().removeprefix("RRULE:").split(";") if i]
        parts = {}
        for item in items:
            if "=" in item:
                k, v = item.split("=", 1)
                parts[k.strip().upper()] = v.strip()
        if isinstance(start, datetime.datetime) and not allday:
            zone = pytz.timezone(tz if tz in pytz.all_timezones_set else "UTC")
            local = pytz.utc.localize(start).astimezone(zone)
        else:
            local = start
        freq = parts.get("FREQ", "").upper()
        if freq == "DAILY" and parts.get("BYDAY") and parts.get("INTERVAL", "1") == "1" and \
                not any(k in parts for k in ("BYMONTH", "BYMONTHDAY", "BYSETPOS")):
            # "Every weekday": Odoo holds it as a weekly rule.
            parts["FREQ"] = "WEEKLY"
            freq = "WEEKLY"
        if freq == "WEEKLY" and not parts.get("BYDAY"):
            parts["BYDAY"] = self.WEEKDAYS[local.weekday()]
        elif freq == "MONTHLY" and not parts.get("BYDAY") and not parts.get("BYMONTHDAY"):
            parts["BYMONTHDAY"] = str(local.day)
        elif freq == "YEARLY" and not parts.get("BYDAY"):
            parts.setdefault("BYMONTH", str(local.month))
            parts.setdefault("BYMONTHDAY", str(local.day))
        order = ["FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY", "BYMONTH", "WKST"]
        keys = [k for k in order if k in parts] + [k for k in parts if k not in order]
        return ";".join("%s=%s" % (k, parts[k]) for k in keys)

    def _validate_rrule(self, rule):
        parts = {}
        for item in rule.strip().removeprefix("RRULE:").split(";"):
            if not item:
                continue
            if "=" not in item:
                raise DavError(403, C("valid-calendar-object-resource"))
            k, v = item.split("=", 1)
            parts[k.strip().upper()] = v.strip().upper()
        unsupported = set(parts) - ALLOWED_RRULE_PARTS
        freq = parts.get("FREQ")
        ok = not unsupported and freq in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
        byday = parts.get("BYDAY", "")
        if ok and freq == "DAILY" and (byday or "BYMONTHDAY" in parts or "BYMONTH" in parts):
            ok = False
        if ok and freq == "WEEKLY" and (any(re.match(r"^[+-]?\d", d) for d in byday.split(",") if d)
                                        or "BYMONTHDAY" in parts or "BYMONTH" in parts):
            ok = False
        if ok and freq == "MONTHLY":
            if "BYMONTH" in parts or (byday and "BYMONTHDAY" in parts):
                ok = False
            elif byday and not re.match(r"^([+-]?[1-4]|-1)[A-Z]{2}$", byday):
                ok = False
            elif "," in parts.get("BYMONTHDAY", ""):
                ok = False
        if ok and freq == "YEARLY" and (byday or "," in parts.get("BYMONTH", "") or "," in parts.get("BYMONTHDAY", "")):
            ok = False
        if not ok:
            _logger.info("bf_dav_server: recurrence rule refused (Odoo cannot hold it): %s", rule)
            raise DavError(403, C("valid-calendar-object-resource"),
                           message="Odoo cannot store this repeat rule")
        return parts

    def _check_rule_expansion(self, rule, comp, tz, rec, events):
        """Odoo's occurrences must be the client's: anything else would lose dates in silence."""
        try:
            local = pytz.utc.localize(comp.start).astimezone(pytz.timezone(tz)).replace(tzinfo=None) \
                if not comp.allday else datetime.datetime.combine(comp.start, datetime.time())
            dr = du_rrule.rrulestr(re.sub(r"UNTIL=(\d{8}T\d{6})Z", r"UNTIL=\1", rule.removeprefix("RRULE:")),
                                   dtstart=local, ignoretz=True)
            client = []
            for value in itertools.islice(dr, 12):
                if comp.allday:
                    client.append(value.strftime("%Y%m%d"))
                else:
                    utc = pytz.timezone(tz).localize(value).astimezone(pytz.utc).replace(tzinfo=None)
                    client.append(utc.strftime("%Y%m%dT%H%M"))
        except (ValueError, TypeError, OverflowError):
            return  # the rule passed Odoo's own parser; nothing better to compare with
        odoo = sorted(_rid_key(self._rid_of(e), comp.allday) for e in events)
        n = min(len(client), len(odoo))
        if odoo[:n] != client[:n] or len(odoo) < len(client):
            _logger.info("bf_dav_server: rule %s expands differently in Odoo (%s vs %s)", rule, odoo[:3], client[:3])
            raise DavError(403, C("valid-calendar-object-resource"),
                           message="Odoo cannot store this repeat rule")

    def _time_vals(self, comp, current=None):
        vals = {}
        if comp.allday:
            start = comp.start
            stop = comp.stop - datetime.timedelta(days=1) if comp.stop and comp.stop > comp.start else comp.start
            if current is None or not current.allday or current.start_date != start or current.stop_date != stop:
                vals.update(allday=True, start_date=start, stop_date=stop)
        else:
            start, stop = comp.start, comp.stop if comp.stop and comp.stop >= comp.start else comp.start
            if current is None or current.allday or current.start != start or current.stop != stop:
                vals.update(allday=False, start=start, stop=stop)
        return vals

    def _content_vals(self, comp, current, is_org, with_time=True):
        """Odoo values for what the client changed (all values when ``current`` is None)."""
        vals = {}
        if with_time:
            vals.update(self._time_vals(comp, current))
        if current is None or (current.name or "") != comp.summary:
            vals["name"] = comp.summary or "(no title)"
        cur_desc = "" if current is None or not current.description or is_html_empty(current.description) \
            else html2plaintext(current.description).strip()
        if cur_desc.strip() != (comp.description or "").strip():
            vals["description"] = plaintext2html(comp.description) if comp.description else False
        cur_loc = (current.location or "") if current is not None else ""
        if comp.location != cur_loc:
            is_video = current is not None and not current.location and \
                comp.location == (current.videocall_location or "")
            if not is_video:
                vals["location"] = comp.location or False
        if comp.klass is not None:
            privacy = KLASS_TO_ODOO.get(comp.klass, "private")
            # Apps write CLASS:PUBLIC on everything: on an event left at the
            # owner's default it says nothing, and must not make it public.
            default_kept = current is not None and not current.privacy and privacy == "public"
            if not default_kept and (current is None or current.privacy != privacy):
                vals["privacy"] = privacy
        if comp.transp is not None:
            show_as = "free" if comp.transp == "TRANSPARENT" else "busy"
            if current is None or current.show_as != show_as:
                vals["show_as"] = show_as
        if comp.categories is not None:
            ids = self._category_ids(comp.categories)
            if current is None or set(ids) != set(current.categ_ids.ids):
                vals["categ_ids"] = [(6, 0, ids)]
        if is_org:
            minutes = sorted({a.minutes_before for a in comp.alarms if a.mappable})
            cur_notif = self._notification_alarms(current) if current is not None else self.env["calendar.alarm"]
            if current is None or sorted(cur_notif.mapped("duration_minutes")) != minutes:
                keep = (current.alarm_ids - cur_notif).ids if current is not None else []
                vals["alarm_ids"] = [(6, 0, keep + self._alarm_ids(minutes))]
        return vals

    def _category_ids(self, names):
        Type = self.env["calendar.event.type"].sudo()
        ids = []
        for name in names:
            name = name.strip()[:128]
            if not name:
                continue
            cat = Type.search([("name", "=", name)], limit=1) or next(
                (t for t in Type.search([("name", "ilike", name)]) if t.name.casefold() == name.casefold()),
                None) or Type.create({"name": name})
            ids.append(cat.id)
        return ids

    def _alarm_ids(self, minutes_list):
        Alarm = self.env["calendar.alarm"].sudo()
        ids = []
        for minutes in minutes_list:
            alarm = Alarm.search([("alarm_type", "=", "notification"), ("duration_minutes", "=", minutes)], limit=1)
            if not alarm:
                if minutes and minutes % 1440 == 0:
                    interval, duration = "days", minutes // 1440
                elif minutes and minutes % 60 == 0:
                    interval, duration = "hours", minutes // 60
                else:
                    interval, duration = "minutes", minutes
                alarm = Alarm.create({"name": "%d min" % minutes, "alarm_type": "notification",
                                      "interval": interval, "duration": duration})
            ids.append(alarm.id)
        return ids

    def _partner_cmds(self, comp, current, organizer_partner, internal):
        """Commands for partner_ids.

        Attendees without an email are never removed. A client that sends no
        ATTENDEE at all is read as "does not handle attendees", never as
        "remove everyone": losing a guest list in silence is worse than
        keeping one.
        """
        wanted = {}
        for att in comp.attendees:
            if att.email and att.email not in wanted:
                wanted[att.email] = att
        if current is None:
            ids = {self._partner_for_email(e, a.cn).id for e, a in wanted.items()}
            if organizer_partner:
                ids.add(organizer_partner.id)
            return [(6, 0, sorted(ids))]
        cur = {}
        for att in current.attendee_ids:
            email = email_normalize(att.email or att.partner_id.email or "")
            if email:
                cur[email] = att.partner_id
        add = [self._partner_for_email(e, a.cn).id for e, a in wanted.items() if e not in cur]
        remove = []
        if wanted:
            remove = [p.id for e, p in cur.items() if e not in wanted and p != organizer_partner]
        return [(4, pid) for pid in add] + [(3, pid) for pid in remove]

    # -- create ------------------------------------------------------------

    def _create_from_object(self, node, obj):
        viewer = self.env.user
        cal = node.cal
        # Two clients creating the same address at once. Waiting would not do:
        # in REPEATABLE READ the second would not see what the first commits.
        # The second is told to come back, and then finds the event.
        self.env.cr.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))", ["bf_dav:%d:%s" % (cal.id, node.name)])
        if not self.env.cr.fetchone()[0]:
            raise DavError(503, headers={"Retry-After": "2"})
        if self._member_for_node(node):
            raise DavError(412)
        if obj.master and obj.master.rrule:
            m = obj.master
            m.rrule = self._complete_rule(m.rrule, m.start, m.tzid if m.tzid in pytz.all_timezones_set
                                          else (viewer.tz or "UTC"), m.allday)
        owner = cal.owner_id
        master = obj.master or obj.events[0]
        internal = self._internal_emails()
        external_org = bool(master.organizer) and master.organizer not in internal
        ctx = self._mail_context(obj, external_org)
        self._burst_locked(record=True)
        tz = master.tzid if master.tzid in pytz.all_timezones_set else (viewer.tz or "UTC")
        org_user = owner if owner.active else viewer
        vals = self._content_vals(master, None, is_org=True)
        vals.update({
            "user_id": org_user.id,
            "bf_dav_calendar_id": cal.id,
            "bf_dav_uid": obj.uid,
            "bf_dav_href": node.name,
            "partner_ids": self._partner_cmds(master, None, org_user.partner_id, internal),
        })
        if external_org and "x_imip_organizer" in self.env["calendar.event"]._fields:
            vals["x_imip_organizer"] = master.organizer
        # Odoo reads weekdays and DST in the context zone: it must be the
        # meeting's, never the zone of whoever is syncing.
        ctx = dict(ctx, tz=tz)
        Event = self.env["calendar.event"].with_context(**ctx)
        if master.rrule and obj.master:
            self._validate_rrule(master.rrule)
            vals.update(recurrency=True, rrule=master.rrule.removeprefix("RRULE:"), event_tz=tz)
            try:
                ev = Event.create(vals)
            except (UserError, ValidationError) as err:
                _logger.info("bf_dav_server: series refused: %s", err)
                raise DavError(403, C("valid-calendar-object-resource")) from err
            rec = ev.recurrence_id
            if not rec:
                raise DavError(403, C("valid-calendar-object-resource"))
            rec.with_context(bf_dav_no_journal=True).write({"bf_dav_uid": obj.uid, "bf_dav_href": node.name})
            events = self.env["calendar.event"].search([("recurrence_id", "=", rec.id)])
            self._check_rule_expansion(master.rrule, master, tz, rec, events)
            self._apply_exceptions(rec, obj, ctx, is_org=True, internal=internal)
            key = "r%d" % rec.id
        else:
            try:
                ev = Event.create(vals)
            except (UserError, ValidationError) as err:
                raise DavError(403, C("valid-calendar-object-resource")) from err
            key = "e%d" % ev.id
        events, rec = self._resource_events(key)
        if ctx.get("bf_dav_burst") and (obj.master or obj.events[0]).attendees:
            _logger.warning("bf_dav_server: %s uploads many events at once; invitations held for %s",
                            viewer.login, key)
            events[:1].sudo().message_post(
                body=self.env._("Created from a calendar app while it was uploading many events at once: "
                                "no invitation was sent. Send it from Odoo if the guests should get one."),
                subtype_xmlid="mail.mt_note")
        self._apply_partstat(events, rec, obj, ctx)
        self._apply_alarm_state(key, events, rec, obj, before=None)
        self._store_raw(key, obj, is_org=True)
        self.env["bf.dav.change"]._record([cal._bf_dav_key()], key, node.name)
        return key

    # -- update ------------------------------------------------------------

    def _update_from_object(self, node, key, obj):
        events, rec = self._resource_events(key)
        first = events[:1]
        is_org = self._is_organizer_view(node, first)
        internal = self._internal_emails()
        master = obj.master or obj.events[0]
        external_org = bool(master.organizer) and master.organizer not in internal
        ctx = self._mail_context(obj, external_org)
        tz0 = master.tzid if master.tzid in pytz.all_timezones_set else (
            (rec.event_tz if rec else "") or self._tz_for(first))
        ctx = dict(ctx, tz=tz0)
        before = self._state_view(node, key)
        organizer_partner = self._effective_organizer(node, first)
        self.env.cr.precommit.data["bf_dav.content_written"] = False
        if is_org:
            try:
                if not rec:
                    key = self._update_single(node, key, first, obj, ctx, internal, organizer_partner)
                else:
                    key = self._update_series(node, key, rec, events, obj, ctx, internal, organizer_partner,
                                              before=before)
            except AccessError as err:
                raise DavError(403, D("need-privileges")) from err
            except (UserError, ValidationError) as err:
                _logger.info("bf_dav_server: update refused: %s", err)
                raise DavError(403, C("valid-calendar-object-resource")) from err
        events, rec = self._resource_events(key)
        if not events:
            return key
        content_written = self.env.cr.precommit.data.get("bf_dav.content_written")
        self._apply_partstat(events, rec, obj, ctx)
        self._apply_alarm_state(key, events, rec, obj, before)
        self._store_raw(key, obj, is_org=is_org)
        if is_org and content_written and not ctx.get("no_mail_to_attendees") \
                and hasattr(events, "_bf_announce_remote_change"):
            # A change made in a calendar app is a change "from outside Odoo":
            # bf_calendar_invite decides whether the guests hear about it
            # (bf_calendar_invite.auto_change_notice), exactly as for the
            # Nextcloud pull it replaces.
            upcoming = events.filtered(lambda e: e.start and e.start >= fields.Datetime.now())
            upcoming.sorted("start")[:1]._bf_announce_remote_change()
        return key

    def _mark_written(self):
        self.env.cr.precommit.data["bf_dav.content_written"] = True

    def _content_stamp(self, events):
        """What a content write changes: the events' own rows (not attendees)."""
        if not events:
            return None
        self.env["calendar.event"].flush_model()
        self.env.cr.execute("SELECT count(*), max(write_date), sum(id) FROM calendar_event "
                            "WHERE id = ANY(%s) OR recurrence_id = ANY(%s)",
                            [events.ids, events.recurrence_id.ids or [0]])
        return self.env.cr.fetchone()

    def _owner_takes_over(self, ev, vals):
        """A meeting organized by the system user (synced from Nextcloud) that
        its calendar's owner changes becomes theirs: invitations then leave
        under their name, not under the system's."""
        user = ev.sudo().user_id
        if vals and (not user or not user.active or user.share or user._is_superuser()):
            owner = ev.sudo().bf_dav_calendar_id.owner_id
            if owner == self.env.user:
                vals["user_id"] = owner.id

    def _update_single(self, node, key, ev, obj, ctx, internal, organizer_partner):
        master = obj.master or obj.events[0]
        vals = self._content_vals(master, ev, is_org=True)
        cmds = self._partner_cmds(master, ev, organizer_partner, internal)
        if cmds:
            vals["partner_ids"] = cmds
        if master.rrule and obj.master:
            self._validate_rrule(master.rrule)
            tz = master.tzid if master.tzid in pytz.all_timezones_set else (self.env.user.tz or "UTC")
            href, uid = ev.bf_dav_href, ev.bf_dav_uid
            vals.update(recurrency=True, rrule=master.rrule.removeprefix("RRULE:"), event_tz=tz)
            ev.with_context(**ctx).write(vals)
            self._mark_written()
            rec = ev.recurrence_id
            if not rec:
                raise DavError(403, C("valid-calendar-object-resource"))
            rec.with_context(bf_dav_no_journal=True).write({"bf_dav_uid": uid, "bf_dav_href": href})
            events = self.env["calendar.event"].search([("recurrence_id", "=", rec.id)])
            self._check_rule_expansion(master.rrule, master, tz, rec, events)
            self._apply_exceptions(rec, obj, ctx, is_org=True, internal=internal)
            return "r%d" % rec.id
        self._owner_takes_over(ev, vals)
        if vals:
            ev.with_context(**ctx).write(vals)
            self._mark_written()
        return key

    def _update_series(self, node, key, rec, events, obj, ctx, internal, organizer_partner, before=None):
        master = obj.master
        view = self._series_view(rec, events)
        template = view["template"]
        if master is None:
            self._apply_exceptions(rec, obj, ctx, is_org=True, internal=internal)
            return key
        uid, href = rec.bf_dav_uid, rec.bf_dav_href
        if not master.rrule:
            # The client turned the series into a single event: keep the
            # occurrence that carries the series' shape, drop the others.
            keep = template
            (view["occs"] - keep).with_context(**ctx).unlink()
            self._mark_written()
            keep.with_context(**dict(ctx, bf_dav_no_journal=True)).write(
                {"recurrence_id": False, "recurrency": False, "follow_recurrence": False,
                 "bf_dav_uid": uid, "bf_dav_href": href})
            rec.with_context(bf_dav_no_journal=True).unlink()
            self.env["bf.dav.change"]._record([node.cal._bf_dav_key()], "e%d" % keep.id, href)
            return self._update_single(node, "e%d" % keep.id, keep, obj, ctx, internal, organizer_partner)
        tz = master.tzid if master.tzid in pytz.all_timezones_set else (rec.event_tz or self.env.user.tz or "UTC")
        anchor = view["anchor"]
        served = (before or {}).get("master")
        if served:
            # Compared with what the client was given, not with Odoo's own
            # spelling of the rule: a client that sends back what it received
            # has changed nothing.
            time_changed = (served["start"], served["stop"], served["allday"]) != \
                (master.start, master.stop, master.allday)
            served_rule = self._complete_rule(served["rrule"], served["start"], tz, served["allday"])
            rule_changed = self._rule_key(served_rule, tz) != self._rule_key(master.rrule, tz)
        else:
            duration = template.stop - template.start
            time_changed = template.allday != master.allday or anchor != master.start or \
                duration != (master.stop - master.start)
            rule_changed = self._rule_key(self._complete_rule(
                self._rrule_out(rec, tz, master.allday), anchor, tz, master.allday), tz) != \
                self._rule_key(master.rrule, tz)
        if time_changed or rule_changed:
            self._validate_rrule(master.rrule)
            base = rec.base_event_id if rec.base_event_id in events else events.sorted(
                lambda e: self._rid_of(e))[:1]
            vals = self._content_vals(master, base, is_org=True, with_time=False)
            vals.update(self._time_vals(master, None))
            cmds = self._partner_cmds(master, base, organizer_partner, internal)
            if cmds:
                vals["partner_ids"] = cmds
            vals.update(self._rrule_vals(master.rrule, master.start, tz))
            vals.update(recurrency=True, recurrence_update="all_events")
            base.with_context(**dict(ctx, tz=tz)).write(vals)
            self._mark_written()
            new_rec = base.recurrence_id
            if not new_rec:
                raise DavError(403, C("valid-calendar-object-resource"))
            if new_rec.bf_dav_uid != uid:
                new_rec.with_context(bf_dav_no_journal=True).write({"bf_dav_uid": uid, "bf_dav_href": href})
            new_events = self.env["calendar.event"].search([("recurrence_id", "=", new_rec.id)])
            self._check_rule_expansion(master.rrule, master, tz, new_rec, new_events)
            self._apply_exceptions(new_rec, obj, ctx, is_org=True, internal=internal)
            return "r%d" % new_rec.id
        vals = self._content_vals(master, template, is_org=True, with_time=False)
        cmds = self._partner_cmds(master, template, organizer_partner, internal)
        added = [c[1] for c in cmds if c[0] == 4]
        if cmds:
            vals["partner_ids"] = cmds
        self._owner_takes_over(template, vals)
        if vals:
            rec.with_context(tz=tz)._write_events(vals)
            self._mark_written()
            if "alarm_ids" in vals and not ctx.get("no_mail_to_attendees"):
                rec._setup_alarms(recurrence_update=True)
            if added and not ctx.get("no_mail_to_attendees"):
                upcoming = self.env["calendar.event"].search(
                    [("recurrence_id", "=", rec.id), ("start", ">=", fields.Datetime.now())], order="start", limit=1)
                atts = upcoming.attendee_ids.filtered(lambda a: a.partner_id.id in added)
                template_inv = self.env.ref("calendar.calendar_template_meeting_invitation", raise_if_not_found=False)
                if atts and template_inv:
                    atts._send_mail_to_attendees(template_inv, force_send=False)
        self._apply_exceptions(rec, obj, ctx, is_org=True, internal=internal)
        return key

    def _rrule_vals(self, rule, start, tz):
        """Recurrence field values for ``rule``, every weekday explicit.

        Odoo rebuilding a series ("all events") drops an ``rrule`` string and
        keeps the old rule's fields: a changed rule must be given as fields.
        """
        Rec = self.env["calendar.recurrence"].with_context(tz=tz)
        data = Rec._rrule_parse(rule.removeprefix("RRULE:"), start)
        vals = {k: data[k] for k in ("rrule_type", "interval", "count", "until", "end_type", "month_by",
                                      "day", "byday", "weekday") if k in data}
        for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
            vals[day] = bool(data.get(day))
        if vals.get("end_type") != "count":
            vals["count"] = vals.get("count") or 1
        vals["event_tz"] = tz
        return vals

    def _rule_signature(self, rule, start):
        if not rule:
            return None
        try:
            data = self.env["calendar.recurrence"]._rrule_parse(rule.removeprefix("RRULE:"), start)
        except Exception:  # noqa: BLE001 - unreadable rule compares as different
            return rule
        data.pop("rrule", None)
        return sorted((k, str(v)) for k, v in data.items())

    def _apply_exceptions(self, rec, obj, ctx, is_org, internal):
        events = self.env["calendar.event"].search([("recurrence_id", "=", rec.id)])
        if not events:
            return
        allday = events[:1].allday
        by_rid = {}
        for occ in events:
            by_rid.setdefault(_rid_key(self._rid_of(occ), allday), occ)
        organizer_partner = events[:1].user_id.partner_id
        for exc in obj.exceptions:
            occ = by_rid.get(_rid_key(exc.rid, allday))
            if not occ:
                continue
            vals = self._content_vals(exc, occ, is_org=is_org)
            cmds = self._partner_cmds(exc, occ, organizer_partner, internal)
            if cmds:
                vals["partner_ids"] = cmds
            if not vals:
                continue
            if set(vals) & set(TIME_KEYS):
                vals["recurrence_update"] = "self_only"
            occ.with_context(**dict(ctx, tz=rec.event_tz or ctx.get("tz") or "UTC")).write(vals)
            self._mark_written()
        master = obj.master
        if master:
            gone = self.env["calendar.event"]
            for ex in master.exdates:
                occ = by_rid.get(_rid_key(ex, allday))
                if occ:
                    gone |= occ
            if gone:
                gone.with_context(**ctx).unlink()
                self._mark_written()

    def _apply_partstat(self, events, rec, obj, ctx):
        """The viewer's own answer, for each occurrence it covers."""
        email_set = set(self._viewer_emails())
        partner = self.env.user.partner_id
        master = obj.master
        allday = events[:1].allday
        comp_for = {}
        for exc in obj.exceptions:
            comp_for[_rid_key(exc.rid, allday)] = exc
        now = fields.Datetime.now()
        todo = collections.defaultdict(lambda: self.env["calendar.attendee"])
        for ev in events:
            comp = comp_for.get(_rid_key(self._rid_of(ev), allday)) if rec else None
            if comp is None:
                comp = master or (obj.events[0] if not rec else None)
                if rec and ev.stop and ev.stop < now:
                    continue  # past occurrences keep their answer
            if comp is None:
                continue
            mine = next((a for a in comp.attendees if a.email in email_set), None)
            if not mine:
                continue
            state = PARTSTAT_TO_ODOO.get(mine.partstat)
            att = ev.attendee_ids.filtered(lambda a: a.partner_id == partner)[:1]
            if att and state and att.state != state:
                todo[state] |= att
        for state, atts in todo.items():
            atts = atts.with_context(**ctx)
            if state == "accepted":
                atts.do_accept()
            elif state == "declined":
                atts.do_decline()
            elif state == "tentative":
                atts.do_tentative()
            else:
                atts.write({"state": "needsAction"})

    # -- reminder state ----------------------------------------------------

    def _state_view(self, node, key):
        """What the viewer's client was shown: Thunderbird properties and, per
        component, the latest ACKNOWLEDGED and the snooze VALARMs."""
        try:
            text = self._build_resource(node, key)
        except DavError:
            return None
        try:
            obj = ical.parse_calendar(text, "UTC")
        except ical.IcalError:
            return None
        return self._state_of(obj, allday=False)

    def _state_of(self, obj, allday):
        view = {"moz": dict(obj.master.moz) if obj.master else {}, "acks": {}, "snoozes": {}}
        if obj.master:
            m = obj.master
            view["master"] = {"start": m.start, "stop": m.stop, "allday": m.allday, "rrule": m.rrule,
                              "tzid": m.tzid}
        for comp in obj.events:
            ck = "" if comp.rid is None else _rid_key(comp.rid, isinstance(comp.rid, datetime.date)
                                                      and not isinstance(comp.rid, datetime.datetime))
            view["acks"][ck] = _max_dt(*[a.acknowledged for a in comp.alarms if a.mappable])
            view["snoozes"][ck] = {(a.snooze_of, a.absolute) for a in comp.alarms if a.snooze_of and a.absolute}
        return view

    def _apply_alarm_state(self, key, events, rec, obj, before):
        """Translate the reminder state the client wrote into Odoo's
        (bf_dismissed_at / bf_snoozed_until of the viewer's attendee rows),
        only where the client changed it compared to what it was shown."""
        Att = self.env["calendar.attendee"]
        if "bf_dismissed_at" not in Att._fields or not events:
            return
        partner = self.env.user.partner_id
        allday = events[:1].allday
        atts = Att.sudo().search([("event_id", "in", events.ids), ("partner_id", "=", partner.id)])
        by_event = {a.event_id.id: a for a in atts}
        by_rid = {_rid_key(self._rid_of(ev), allday): ev for ev in events}
        before = before or {"moz": {}, "acks": {}, "snoozes": {}}
        writes = collections.defaultdict(dict)
        master = obj.master or obj.events[0]
        moz_now, moz_before = master.moz, before.get("moz", {})

        def dismiss(evs, when, since):
            for ev in evs:
                att = by_event.get(ev.id)
                if not att:
                    continue
                trig = self._trigger_time(ev)
                if trig > when or (since and trig <= since):
                    continue
                if not att.bf_dismissed_at or att.bf_dismissed_at < when:
                    writes[att.id]["bf_dismissed_at"] = when
                if att.bf_snoozed_until and att.bf_snoozed_until <= when:
                    writes[att.id]["bf_snoozed_until"] = False

        # Thunderbird: X-MOZ-LASTACK on the master.
        new_ack = ical.parse_utc_stamp(moz_now.get("X-MOZ-LASTACK"))
        old_ack = ical.parse_utc_stamp(moz_before.get("X-MOZ-LASTACK"))
        if new_ack and (not old_ack or new_ack > old_ack):
            dismiss(events, new_ack, old_ack or (new_ack - datetime.timedelta(days=2)))
        # Thunderbird: snoozes, one per occurrence for a series.
        snooze_keys = {k for k in set(moz_now) | set(moz_before) if k.startswith("X-MOZ-SNOOZE-TIME")}
        for k in snooze_keys:
            if moz_now.get(k) == moz_before.get(k):
                continue
            if k == "X-MOZ-SNOOZE-TIME":
                targets = events if not rec else self.env["calendar.event"]
            else:
                rid = ical.from_prtime(k.rsplit("-", 1)[-1])
                ev = by_rid.get(_rid_key(rid, allday)) if rid else None
                targets = ev or self.env["calendar.event"]
            until = ical.parse_utc_stamp(moz_now.get(k)) if moz_now.get(k) else False
            for ev in targets:
                att = by_event.get(ev.id)
                if att:
                    writes[att.id]["bf_snoozed_until"] = until or False
        # Apple / RFC 9074: ACKNOWLEDGED and snooze VALARMs, per component.
        now_view = self._state_of(obj, allday)
        for ck, ack in now_view["acks"].items():
            prev = before.get("acks", {}).get(ck)
            if ack and (not prev or ack > prev):
                if ck == "" and rec:
                    dismiss(events, ack, prev or (ack - datetime.timedelta(days=2)))
                else:
                    ev = events if not rec else by_rid.get(ck)
                    if ev:
                        dismiss(ev, ack, None)
        for ck, snoozes in now_view["snoozes"].items():
            fresh = snoozes - before.get("snoozes", {}).get(ck, set())
            if not fresh:
                continue
            until = max(s[1] for s in fresh)
            if ck == "" and rec:
                # The occurrence whose reminder last went off.
                past = events.filtered(lambda e: self._trigger_time(e) <= until)
                targets = past.sorted(lambda e: self._trigger_time(e))[-1:]
            else:
                targets = events if not rec else (by_rid.get(ck) or self.env["calendar.event"])
            for ev in targets:
                att = by_event.get(ev.id)
                if att:
                    writes[att.id]["bf_snoozed_until"] = until
        for att_id, vals in writes.items():
            Att.sudo().browse(att_id).write(vals)

    def _store_raw(self, key, obj, is_org):
        valarms = {}
        extra = {}
        for comp in obj.events:
            allday = isinstance(comp.rid, datetime.date) and not isinstance(comp.rid, datetime.datetime)
            ck = "" if comp.rid is None else _rid_key(comp.rid, allday)
            if comp.alarms:
                valarms[ck] = [a.raw for a in comp.alarms]
            if comp.extra:
                extra[ck] = list(comp.extra)
        moz = dict(obj.master.moz) if obj.master else {}
        self.env["bf.dav.user.data"]._store(self.env.user, key, valarms, moz)
        if is_org:
            self.env["bf.dav.resource.extra"]._store(key, extra)

    # ------------------------------------------------------------------
    # Delete, move
    # ------------------------------------------------------------------

    def _delete_calendar_object(self, node, headers):
        member = self._member_for_node(node)
        if not member:
            raise DavError(404)
        self._check_preconditions(member, headers)
        events, rec = self._resource_events(member["key"])
        if node.level not in WRITE_LEVELS:
            raise DavError(403, D("need-privileges"))
        self._lock_rows("calendar_event", events.ids)
        if self._is_organizer_view(node, events[:1]):
            try:
                if hasattr(events, "_bf_trash"):
                    # bf_calendar_trash: a meeting deleted in an app goes to
                    # the trash, as when it is deleted in Odoo. Its own door
                    # stays shut to requests that never feed Nextcloud.
                    events._bf_trash("all_events", nextcloud=False, origin="odoo")
                else:
                    events.unlink()
                    if rec.exists():
                        rec.unlink()
            except AccessError as err:
                raise DavError(403, D("need-privileges")) from err
            except UserError as err:
                raise DavError(403) from err
        else:
            # An attendee removing someone else's event from their calendar:
            # decline it, then leave it.
            partner = self.env.user.partner_id
            mine = events.attendee_ids.filtered(lambda a: a.partner_id == partner)
            if not mine:
                raise DavError(403, D("need-privileges"))
            future = mine.filtered(lambda a: a.event_id.stop >= fields.Datetime.now())
            if future:
                future.do_decline()
            events.sudo().with_context(no_mail_to_attendees=True).write({"partner_ids": [(3, partner.id)]})
        self.env["bf.dav.change"]._record([node.cal._bf_dav_key()], member["key"], member["href"])
        return 204, self._base_headers(), b""

    def _move_calendar_object(self, node, target, headers):
        member = self._member_for_node(node)
        if not member:
            raise DavError(404)
        self._check_preconditions(member, headers)
        events, rec = self._resource_events(member["key"])
        if not self._is_organizer_view(node, events[:1]) or target.cal.owner_id != self.env.user \
                and target.level != "edit":
            raise DavError(403, D("need-privileges"))
        existing = self._member_for_node(target)
        overwrite = (headers.get("overwrite") or "T").strip().upper() != "F"
        if existing and existing["key"] != member["key"]:
            if not overwrite:
                raise DavError(412)
            raise DavError(403)
        events.sudo().write({"bf_dav_calendar_id": target.cal.id})
        holder = rec or events[:1]
        if target.name != holder.bf_dav_href:
            holder.sudo().write({"bf_dav_href": target.name})
        self.env["bf.dav.change"]._record([node.cal._bf_dav_key(), target.cal._bf_dav_key()],
                                          member["key"], member["href"])
        return (204 if existing else 201), self._base_headers(), b""

    # ------------------------------------------------------------------
    # Scheduling outbox: free/busy lookups (Apple asks before inviting)
    # ------------------------------------------------------------------

    def _outbox_post(self, node, headers, body):
        text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
        lines = ical.unfold(text).split("\n")
        start = end = None
        recipients = []
        organizer = ""
        for line in lines:
            name = ical._prop_name(line)
            value = ical._value(line).strip()
            if name == "DTSTART":
                start = ical.parse_utc_stamp(value)
            elif name == "DTEND":
                end = ical.parse_utc_stamp(value)
            elif name == "ATTENDEE":
                recipients.append(ical._mailto(value))
            elif name == "ORGANIZER":
                organizer = value
        if "VFREEBUSY" not in text.upper() or not start or not end or end <= start:
            raise DavError(400)
        if end - start > datetime.timedelta(days=62):
            end = start + datetime.timedelta(days=62)
        from lxml import etree
        root = etree.Element(C("schedule-response"), nsmap={"d": X.NS_DAV, "cal": X.NS_CALDAV})
        for email in recipients:
            resp = etree.SubElement(root, C("response"))
            rcp = etree.SubElement(resp, C("recipient"))
            rcp.append(X.href_el("mailto:%s" % email))
            user = self.env["res.users"].sudo().search(
                ["|", ("login", "=ilike", email), ("partner_id.email", "=ilike", email),
                 ("share", "=", False), ("active", "=", True)], limit=1)
            if not user:
                etree.SubElement(resp, C("request-status")).text = "3.7;Invalid calendar user"
                continue
            self.env.cr.execute("""
                SELECT e.start, e.stop FROM calendar_event e
                  JOIN calendar_attendee a ON a.event_id = e.id
                 WHERE a.partner_id = %s AND e.active AND e.show_as = 'busy' AND a.state != 'declined'
                   AND e.start < %s AND e.stop > %s ORDER BY e.start
            """, [user.partner_id.id, end, start])
            fb = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Symbifox//bf_dav_server//EN",
                  "METHOD:REPLY", "BEGIN:VFREEBUSY", "UID:%s" % ical.escape_text(organizer or email),
                  "DTSTAMP:%s" % ical.fmt_utc(fields.Datetime.now()),
                  "DTSTART:%s" % ical.fmt_utc(start), "DTEND:%s" % ical.fmt_utc(end),
                  "ATTENDEE:mailto:%s" % email]
            for s, e in self.env.cr.fetchall():
                fb.append("FREEBUSY;FBTYPE=BUSY:%s/%s" % (ical.fmt_utc(max(s, start)), ical.fmt_utc(min(e, end))))
            fb += ["END:VFREEBUSY", "END:VCALENDAR"]
            etree.SubElement(resp, C("request-status")).text = "2.0;Success"
            etree.SubElement(resp, C("calendar-data")).text = ical.CRLF.join(fb) + ical.CRLF
        hdrs = self._base_headers()
        hdrs["Content-Type"] = "application/xml; charset=utf-8"
        return 200, hdrs, etree.tostring(root, xml_declaration=True, encoding="utf-8")

    # ------------------------------------------------------------------
    # calendar-query
    # ------------------------------------------------------------------

    def _report_calendar_query(self, node, root):
        node = self._member_node(node)
        if node.kind != "calendar":
            raise DavError(403)
        tags = self._report_tags(root)
        time_range, uid_match = None, None
        flt = root.find(C("filter"))
        if flt is not None:
            for comp in flt.iter(C("comp-filter")):
                name = (comp.get("name") or "").upper()
                if name in ("VTODO", "VJOURNAL", "VFREEBUSY"):
                    return self._multistatus(X.Multistatus())
                tr = comp.find(C("time-range"))
                if tr is not None and name == "VEVENT":
                    time_range = (ical.parse_utc_stamp(tr.get("start")), ical.parse_utc_stamp(tr.get("end")))
                for pf in comp.findall(C("prop-filter")):
                    if (pf.get("name") or "").upper() == "UID":
                        tm = pf.find(C("text-match"))
                        if tm is not None and tm.text:
                            uid_match = tm.text.strip()
        members = self._cal_members(node, time_range=time_range,
                                    uids=[uid_match] if uid_match else None)
        self._ensure_bodies(node, members, tags)
        ms = X.Multistatus()
        for member in members:
            found, missing = self._member_props(node, member, tags)
            ms.add_props(node.href + quote(member["href"]), found, missing)
        return self._multistatus(ms)
