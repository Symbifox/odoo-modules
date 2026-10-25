# Part of bf_dav_server. See LICENSE file for full copyright and licensing details.
import base64
import datetime
import uuid

from lxml import etree

from unittest.mock import patch

from odoo.tests import HttpCase, TransactionCase, tagged

from ..dav import ical, vcard
from ..models import res_users as dav_users
from ..models.dav_service import SYNC_PREFIX

NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav", "card": "urn:ietf:params:xml:ns:carddav"}

TZ_TORONTO = (
    "BEGIN:VTIMEZONE\r\nTZID:America/Toronto\r\nBEGIN:DAYLIGHT\r\nTZOFFSETFROM:-0500\r\nTZOFFSETTO:-0400\r\n"
    "TZNAME:EDT\r\nDTSTART:19700308T020000\r\nRRULE:FREQ=YEARLY;BYDAY=2SU;BYMONTH=3\r\nEND:DAYLIGHT\r\n"
    "BEGIN:STANDARD\r\nTZOFFSETFROM:-0400\r\nTZOFFSETTO:-0500\r\nTZNAME:EST\r\nDTSTART:19701101T020000\r\n"
    "RRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=11\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\n")


def f(dt):
    return dt.strftime("%Y%m%dT%H%M%SZ")


def vcal(*events, tz=False):
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n" + (TZ_TORONTO if tz else "")
            + "".join(events) + "END:VCALENDAR\r\n").encode()


def vevent(uid, start, stop, summary="Test", extra="", alarms=""):
    return ("BEGIN:VEVENT\r\nUID:%s\r\nDTSTAMP:20261024T000000Z\r\nSUMMARY:%s\r\nDTSTART:%s\r\nDTEND:%s\r\n%s%s"
            "END:VEVENT\r\n" % (uid, summary, f(start), f(stop), extra, alarms))


ALARM15 = "BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT15M\r\nDESCRIPTION:x\r\nEND:VALARM\r\n"


class TestIcal(TransactionCase):
    """The reading/writing layer, without the ORM."""

    def test_tzid_survives_vobject(self):
        # vobject removes TZID once it made the value aware: a series read in
        # the wrong zone moved every occurrence by a day for a user in Auckland.
        obj = ical.parse_calendar(vcal(
            "BEGIN:VEVENT\r\nUID:a\r\nDTSTART;TZID=America/Toronto:20261027T100000\r\n"
            "DTEND;TZID=America/Toronto:20261027T110000\r\nRRULE:FREQ=WEEKLY;BYDAY=TU\r\nEND:VEVENT\r\n",
            tz=True), "Pacific/Auckland")
        self.assertEqual(obj.master.tzid, "America/Toronto")
        self.assertEqual(obj.master.start, datetime.datetime(2026, 10, 27, 14, 0))

    def test_utc_start_reads_as_utc_zone(self):
        obj = ical.parse_calendar(vcal(vevent("b", datetime.datetime(2026, 10, 27, 14), datetime.datetime(
            2026, 10, 27, 15))), "Pacific/Auckland")
        self.assertEqual(obj.master.tzid, "UTC")

    def test_unknown_properties_and_reminder_state_kept(self):
        obj = ical.parse_calendar(vcal(vevent(
            "c", datetime.datetime(2026, 10, 27, 14), datetime.datetime(2026, 10, 27, 15),
            extra="X-MOZ-LASTACK:20261027T134500Z\r\nX-MOZ-GENERATION:4\r\nX-APPLE-STRUCTURED-LOCATION;VALUE=URI:geo:1,2\r\n",
            alarms="BEGIN:VALARM\r\nUID:U1\r\nACTION:DISPLAY\r\nTRIGGER:-PT10M\r\nACKNOWLEDGED:20261027T135000Z\r\nEND:VALARM\r\n")),
            "UTC")
        m = obj.master
        self.assertEqual(m.moz, {"X-MOZ-LASTACK": "20261027T134500Z"})
        self.assertIn("X-MOZ-GENERATION:4", m.extra)
        self.assertTrue(any(line.startswith("X-APPLE-STRUCTURED-LOCATION") for line in m.extra))
        self.assertEqual(m.alarms[0].uid, "U1")
        self.assertEqual(m.alarms[0].acknowledged, datetime.datetime(2026, 10, 27, 13, 50))
        self.assertTrue(m.alarms[0].mappable)

    def test_vtodo_refused(self):
        with self.assertRaises(ical.IcalError) as cm:
            ical.parse_calendar(b"BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nUID:x\r\nEND:VTODO\r\nEND:VCALENDAR\r\n")
        self.assertEqual(cm.exception.condition, "supported-calendar-component")

    def test_fold_keeps_utf8(self):
        line = "SUMMARY:" + "é" * 80
        folded = ical.fold(line)
        self.assertEqual(ical.unfold(folded), line)
        self.assertTrue(all(len(part.encode()) <= 75 for part in folded.split("\r\n")))

    def test_vcard_round_trip_keeps_extra(self):
        card = vcard.parse_card(
            "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:k\r\nFN:Ana Test\r\nEMAIL:a@example.test\r\nEMAIL:b@example.test\r\n"
            "NOTE:private\r\nBDAY:1980-01-02\r\nEND:VCARD\r\n")
        self.assertEqual(card.emails[0][0], "a@example.test")
        self.assertIn("NOTE:private", card.extra)
        with self.assertRaises(vcard.VcardError):
            vcard.parse_card("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:no uid\r\nEND:VCARD\r\n")


class DavCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, no_reset_password=True))
        cls.user = cls.env["res.users"].create({
            "name": "Dav Owner", "login": "dav.owner@example.com", "email": "dav.owner@example.com",
            "tz": "Pacific/Auckland", "groups_id": [(6, 0, [cls.env.ref("base.group_user").id,
                                                            cls.env.ref("base.group_partner_manager").id])]})
        cls.other = cls.env["res.users"].create({
            "name": "Dav Colleague", "login": "dav.colleague@example.com", "email": "dav.colleague@example.com",
            "tz": "America/Toronto", "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])]})
        cls.cal = cls.env["bf.dav.calendar"]._bf_dav_default_for(cls.user)
        cls.svc = cls.env["bf.dav.service"].with_user(cls.user)
        cls.start = (datetime.datetime.now() + datetime.timedelta(days=5)).replace(
            hour=14, minute=0, second=0, microsecond=0)

    def setUp(self):
        super().setUp()
        from ..models import dav_caldav
        dav_caldav._BURST.clear()

    def path(self, name=None, user=None, slug=None):
        user = user or self.user
        slug = slug or self.env["bf.dav.calendar"]._bf_dav_default_for(user).slug
        return "/dav/calendars/%d/%s/%s" % (user.id, slug, name or "")

    def call(self, method, path, body=b"", user=None, **headers):
        svc = self.env["bf.dav.service"].with_user(user or self.user)
        return svc._handle(method, path, headers, body)

    def put(self, uid, body, user=None, **headers):
        return self.call("PUT", self.path(uid + ".ics", user=user), body, user=user, **headers)

    def get(self, uid, user=None):
        return self.call("GET", self.path(uid + ".ics", user=user), user=user)

    def event(self, uid):
        return self.env["calendar.event"].search([("bf_dav_uid", "=", uid)])

    def series(self, uid):
        rec = self.env["calendar.recurrence"].search([("bf_dav_uid", "=", uid)])
        return rec, self.env["calendar.event"].search([("recurrence_id", "=", rec.id)])


@tagged("post_install", "-at_install")
class TestCalDav(DavCase):

    def test_create_read_etag_and_conflict(self):
        uid = "t-%s" % uuid.uuid4()
        body = vcal(vevent(uid, self.start, self.start + datetime.timedelta(hours=1), alarms=ALARM15))
        status, _h, _b = self.put(uid, body, **{"If-None-Match": "*"})
        self.assertEqual(status, 201)
        self.assertEqual(self.put(uid, body, **{"If-None-Match": "*"})[0], 412)
        status, headers, data = self.get(uid)
        self.assertEqual(status, 200)
        self.assertIn(b"UID:" + uid.encode(), data)
        self.assertEqual(self.put(uid, body, **{"If-Match": '"stale"'})[0], 412)
        self.assertEqual(self.put(uid, body, **{"If-Match": headers["ETag"]})[0], 204)
        ev = self.event(uid)
        self.assertEqual(ev.bf_dav_calendar_id, self.cal)
        self.assertEqual(sorted(ev.alarm_ids.mapped("duration_minutes")), [15])

    def test_reminder_only_put_writes_nothing_on_the_event(self):
        uid = "t-%s" % uuid.uuid4()
        stop = self.start + datetime.timedelta(hours=1)
        self.put(uid, vcal(vevent(uid, self.start, stop, alarms=ALARM15)))
        ev = self.event(uid)
        self.env.cr.execute("UPDATE calendar_event SET write_date = '2000-01-01' WHERE id = %s", [ev.id])
        ev.invalidate_recordset()
        mails = self.env["mail.mail"].search_count([])
        messages = self.env["mail.message"].search_count([("model", "=", "calendar.event"), ("res_id", "=", ev.id)])
        ack = f(self.start - datetime.timedelta(minutes=10))
        status, _h, _b = self.put(uid, vcal(vevent(uid, self.start, stop, extra="X-MOZ-LASTACK:%s\r\n" % ack,
                                                   alarms=ALARM15)))
        self.assertEqual(status, 204)
        ev.invalidate_recordset()
        self.assertEqual(ev.write_date, datetime.datetime(2000, 1, 1), "the event itself must not be written")
        self.assertEqual(self.env["mail.mail"].search_count([]), mails)
        self.assertEqual(self.env["mail.message"].search_count(
            [("model", "=", "calendar.event"), ("res_id", "=", ev.id)]), messages)
        if "bf_dismissed_at" in self.env["calendar.attendee"]._fields:
            att = ev.attendee_ids.filtered(lambda a: a.partner_id == self.user.partner_id)
            self.assertEqual(att.bf_dismissed_at, self.start - datetime.timedelta(minutes=10))
        self.assertIn(("X-MOZ-LASTACK:" + ack).encode(), self.get(uid)[2])

    def test_series_in_its_own_zone_for_a_user_elsewhere(self):
        uid = "s-%s" % uuid.uuid4()
        master = ("BEGIN:VEVENT\r\nUID:%s\r\nSUMMARY:Weekly\r\nDTSTART;TZID=America/Toronto:20261027T110000\r\n"
                  "DTEND;TZID=America/Toronto:20261027T120000\r\nRRULE:FREQ=WEEKLY;BYDAY=TU;COUNT=6\r\n"
                  "EXDATE;TZID=America/Toronto:20261110T110000\r\nEND:VEVENT\r\n" % uid)
        moved = ("BEGIN:VEVENT\r\nUID:%s\r\nRECURRENCE-ID;TZID=America/Toronto:20261103T110000\r\nSUMMARY:Weekly\r\n"
                 "DTSTART;TZID=America/Toronto:20261104T090000\r\nDTEND;TZID=America/Toronto:20261104T100000\r\n"
                 "END:VEVENT\r\n" % uid)
        self.assertEqual(self.put(uid, vcal(master, moved, tz=True))[0], 201)
        rec, events = self.series(uid)
        self.assertEqual(len(events), 5)
        self.assertTrue(all(e.start.weekday() == 1 for e in events if e.follow_recurrence))
        obj = ical.parse_calendar(self.get(uid)[2], "UTC")
        self.assertEqual(obj.uid, uid)
        self.assertEqual([d.strftime("%m%d") for d in obj.master.exdates], ["1110"])
        self.assertEqual([e.rid.strftime("%m%d") for e in obj.exceptions], ["1103"])
        # Change the time of the whole series: same UID, same address, exceptions re-applied.
        etag = self.get(uid)[1]["ETag"]
        later = master.replace("T110000", "T130000").replace("T120000", "T140000")
        moved2 = moved.replace("20261103T110000", "20261103T130000")
        self.assertEqual(self.put(uid, vcal(later, moved2, tz=True), **{"If-Match": etag})[0], 204)
        rec2, events2 = self.series(uid)
        self.assertTrue(rec2)
        self.assertEqual(len(events2), 5)
        obj = ical.parse_calendar(self.get(uid)[2], "UTC")
        self.assertEqual(obj.master.start, datetime.datetime(2026, 10, 27, 17, 0))
        self.assertEqual([e.rid for e in obj.exceptions], [datetime.datetime(2026, 11, 3, 18, 0)])

    def test_series_with_end_date_made_in_odoo_survives_a_dismissed_reminder(self):
        # Odoo spells UNTIL in local time, the server serves it in UTC;
        # comparing the two rebuilt the whole series (answers reset, notes posted).
        guest = self.env["res.partner"].create({"name": "Guest", "email": "guest.until@example.com"})
        start = datetime.datetime(2027, 3, 2, 15, 0)
        ev = self.env["calendar.event"].with_user(self.user).with_context(tz="America/Toronto").create({
            "name": "Until series", "start": start, "stop": start + datetime.timedelta(hours=1),
            "recurrency": True, "rrule_type": "weekly", "tue": True, "end_type": "end_date",
            "until": datetime.date(2027, 4, 27), "event_tz": "America/Toronto",
            "partner_ids": [(6, 0, [self.user.partner_id.id, guest.id])],
            "alarm_ids": [(6, 0, self.env["calendar.alarm"].search(
                [("alarm_type", "=", "notification")], limit=1).ids)]})
        rec = ev.recurrence_id
        self.assertTrue(rec)
        rec.calendar_event_ids.attendee_ids.filtered(lambda a: a.partner_id == guest).write({"state": "accepted"})
        events_before = rec.calendar_event_ids
        stamp = {(e.id, e.write_date) for e in events_before}
        msgs = self.env["mail.message"].search_count([("model", "=", "calendar.event"),
                                                       ("res_id", "in", events_before.ids)])
        href = rec.bf_dav_href
        status, headers, data = self.call("GET", self.path(href))
        self.assertEqual(status, 200)
        obj = ical.parse_calendar(data, "UTC")
        self.assertEqual(len(obj.master.exdates), 0)
        # UNTIL is the end of the last day in the meeting's zone, in UTC.
        self.assertIn("UNTIL=20270428T035959Z", obj.master.rrule)
        body = data.decode().replace("END:VEVENT", "X-MOZ-LASTACK:20270302T144000Z\r\nEND:VEVENT", 1)
        status, _h, _b = self.call("PUT", self.path(href), body.encode(), **{"If-Match": headers["ETag"]})
        self.assertEqual(status, 204)
        self.assertEqual(rec.exists(), rec, "the series must not be rebuilt")
        self.assertEqual({(e.id, e.write_date) for e in rec.calendar_event_ids}, stamp)
        self.assertEqual(set(rec.calendar_event_ids.attendee_ids.filtered(
            lambda a: a.partner_id == guest).mapped("state")), {"accepted"})
        self.assertEqual(self.env["mail.message"].search_count(
            [("model", "=", "calendar.event"), ("res_id", "in", events_before.ids)]), msgs)

    def test_until_inherited_from_nextcloud_is_rebuilt(self):
        # A rule synced from Nextcloud keeps a UTC UNTIL of 17:59:59 that hides
        # the last occurrence (19:00 UTC) on every device.
        start = datetime.datetime(2027, 3, 2, 19, 0)
        ev = self.env["calendar.event"].with_user(self.user).with_context(tz="America/Toronto").create({
            "name": "NC until", "start": start, "stop": start + datetime.timedelta(hours=1),
            "recurrency": True, "rrule_type": "weekly", "tue": True, "end_type": "end_date",
            "until": datetime.date(2027, 4, 27), "event_tz": "America/Toronto",
            "partner_ids": [(6, 0, [self.user.partner_id.id])]})
        rec = ev.recurrence_id
        self.env.flush_all()
        self.env.cr.execute("UPDATE calendar_recurrence SET rrule = %s WHERE id = %s",
                            ["FREQ=WEEKLY;UNTIL=20270427T175959Z;BYDAY=TU", rec.id])
        rec.invalidate_recordset()
        obj = ical.parse_calendar(self.call("GET", self.path(rec.bf_dav_href))[2], "UTC")
        self.assertIn("UNTIL=20270428T035959Z", obj.master.rrule)

    def test_deleted_last_occurrence_stays_deleted(self):
        start = datetime.datetime(2027, 3, 2, 15, 0)
        ev = self.env["calendar.event"].with_user(self.user).with_context(tz="America/Toronto").create({
            "name": "Count series", "start": start, "stop": start + datetime.timedelta(hours=1),
            "recurrency": True, "rrule_type": "weekly", "tue": True, "end_type": "count", "count": 4,
            "event_tz": "America/Toronto", "partner_ids": [(6, 0, [self.user.partner_id.id])]})
        rec = ev.recurrence_id
        rec.calendar_event_ids.sorted("start")[-1].unlink()
        obj = ical.parse_calendar(self.call("GET", self.path(rec.bf_dav_href))[2], "UTC")
        self.assertEqual([d.date() for d in obj.master.exdates], [datetime.date(2027, 3, 23)])

    def test_app_defaults_change_nothing(self):
        # Apple's own default reminder and a CLASS:PUBLIC added by an app are
        # not the user's decision: the event is not written.
        uid = "def-%s" % uuid.uuid4()
        stop = self.start + datetime.timedelta(hours=1)
        self.put(uid, vcal(vevent(uid, self.start, stop)))
        ev = self.event(uid)
        self.assertFalse(ev.privacy)
        self.env.cr.execute("UPDATE calendar_event SET write_date = '2000-01-01' WHERE id = %s", [ev.id])
        ev.invalidate_recordset()
        apple = ("BEGIN:VALARM\r\nX-WR-ALARMUID:D1\r\nUID:D1\r\nTRIGGER:-PT15M\r\nACTION:DISPLAY\r\n"
                 "X-APPLE-DEFAULT-ALARM:TRUE\r\nEND:VALARM\r\n")
        self.assertEqual(self.put(uid, vcal(vevent(uid, self.start, stop, extra="CLASS:PUBLIC\r\n",
                                                   alarms=apple)))[0], 204)
        ev.invalidate_recordset()
        self.assertEqual(ev.write_date, datetime.datetime(2000, 1, 1))
        self.assertFalse(ev.privacy)
        self.assertFalse(ev.alarm_ids)
        self.assertIn(b"X-APPLE-DEFAULT-ALARM:TRUE", self.get(uid)[2])

    def test_dav_writes_never_feed_nextcloud(self):
        # a pushed PUT cannot be rolled back, and Nextcloud mails every guest.
        seen = []
        Event = type(self.env["calendar.event"])
        original = Event.write

        def spy(records, vals):
            seen.append(bool(records.env.context.get("skip_nc_sync")))
            return original(records, vals)
        uid = "nc-%s" % uuid.uuid4()
        stop = self.start + datetime.timedelta(hours=1)
        self.put(uid, vcal(vevent(uid, self.start, stop)))
        with patch.object(Event, "write", spy):
            self.put(uid, vcal(vevent(uid, self.start, stop, summary="Renamed")))
        self.assertTrue(seen)
        self.assertTrue(all(seen))

    def test_reminder_only_put_never_announces(self):
        # the change notice of bf_calendar_invite left on a dismissed reminder.
        Event = type(self.env["calendar.event"])
        if not hasattr(Event, "_bf_announce_remote_change"):
            self.skipTest("bf_calendar_invite not installed")
        uid = "an-%s" % uuid.uuid4()
        stop = self.start + datetime.timedelta(hours=1)
        guests = "ORGANIZER:mailto:dav.owner@example.com\r\nATTENDEE;CN=G:mailto:guest.an@example.com\r\n"
        self.put(uid, vcal(vevent(uid, self.start, stop, extra=guests, alarms=ALARM15)))
        calls = []
        with patch.object(Event, "_bf_announce_remote_change", lambda recs: calls.append(recs.ids)):
            self.put(uid, vcal(vevent(uid, self.start, stop, alarms=ALARM15,
                                      extra=guests + "X-MOZ-LASTACK:%s\r\n" % f(self.start))))
            self.assertEqual(calls, [])
            self.put(uid, vcal(vevent(uid, self.start + datetime.timedelta(hours=1),
                                      stop + datetime.timedelta(hours=1), alarms=ALARM15, extra=guests)))
            self.assertEqual(len(calls), 1)

    def test_rule_odoo_cannot_hold_is_refused_and_nothing_stays(self):
        uid = "r-%s" % uuid.uuid4()
        body = vcal(vevent(uid, self.start, self.start + datetime.timedelta(hours=1),
                           extra="RRULE:FREQ=MONTHLY;BYDAY=TU;BYSETPOS=-1\r\n"))
        status, _h, data = self.put(uid, body)
        self.assertEqual(status, 403)
        self.assertIn(b"valid-calendar-object-resource", data)
        self.assertFalse(self.event(uid))
        self.assertFalse(self.env["calendar.recurrence"].search([("bf_dav_uid", "=", uid)]))

    def test_client_without_attendees_does_not_remove_guests(self):
        uid = "a-%s" % uuid.uuid4()
        guests = ("ORGANIZER:mailto:dav.owner@example.com\r\nATTENDEE:mailto:dav.owner@example.com\r\n"
                  "ATTENDEE;CN=Guest:mailto:guest.dav@example.com\r\n")
        stop = self.start + datetime.timedelta(hours=1)
        self.put(uid, vcal(vevent(uid, self.start, stop, extra=guests)))
        self.assertIn("guest.dav@example.com", self.event(uid).partner_ids.mapped("email"))
        self.put(uid, vcal(vevent(uid, self.start, stop, summary="Renamed")))
        ev = self.event(uid)
        self.assertEqual(ev.name, "Renamed")
        self.assertIn("guest.dav@example.com", ev.partner_ids.mapped("email"))

    def test_attendee_answers_but_cannot_rewrite(self):
        uid = "j-%s" % uuid.uuid4()
        stop = self.start + datetime.timedelta(hours=1)
        guests = ("ORGANIZER:mailto:dav.owner@example.com\r\nATTENDEE:mailto:dav.owner@example.com\r\n"
                  "ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:dav.colleague@example.com\r\n")
        self.put(uid, vcal(vevent(uid, self.start, stop, extra=guests)))
        status, _h, data = self.get(uid, user=self.other)
        self.assertEqual(status, 200)
        answer = vcal(vevent(uid, self.start, stop, summary="Hijacked", extra=guests.replace(
            "PARTSTAT=NEEDS-ACTION:mailto:dav.colleague", "PARTSTAT=ACCEPTED:mailto:dav.colleague")))
        self.assertEqual(self.put(uid, answer, user=self.other)[0], 204)
        ev = self.event(uid)
        self.assertEqual(ev.name, "Test")
        self.assertEqual(ev.attendee_ids.filtered(lambda a: a.partner_id == self.other.partner_id).state, "accepted")
        # Deleting it from their calendar declines and leaves.
        self.assertEqual(self.call("DELETE", self.path(uid + ".ics", user=self.other), user=self.other)[0], 204)
        self.assertTrue(self.event(uid))
        self.assertNotIn(self.other.partner_id, self.event(uid).partner_ids)

    def test_deleted_in_an_app_goes_to_the_trash(self):
        uid = "del-%s" % uuid.uuid4()
        stop = self.start + datetime.timedelta(hours=1)
        self.assertEqual(self.put(uid, vcal(vevent(uid, self.start, stop)))[0], 201)
        ev = self.event(uid)
        self.assertEqual(self.call("DELETE", self.path(uid + ".ics"))[0], 204)
        self.assertEqual(self.get(uid)[0], 404)
        if "bf_trashed_at" not in ev._fields:
            self.assertFalse(ev.exists())
            return
        self.assertTrue(ev.exists(), "bf_calendar_trash: the meeting is in the trash, not destroyed")
        self.assertFalse(ev.active)
        self.assertTrue(ev.bf_trashed_at)
        ev._bf_restore(nextcloud=False)
        self.assertEqual(self.get(uid)[0], 200, "restored from the trash, the app sees it again")

    def test_private_event_of_someone_else_is_masked(self):
        ev = self.env["calendar.event"].with_user(self.other).create({
            "name": "Secret", "description": "<p>secret</p>", "privacy": "private",
            "start": self.start, "stop": self.start + datetime.timedelta(hours=1),
            "partner_ids": [(6, 0, [self.other.partner_id.id])]})
        ev.sudo().bf_dav_calendar_id = self.cal  # lives in the owner's calendar
        status, _h, data = self.call("GET", self.path(ev.bf_dav_href))
        self.assertEqual(status, 200)
        self.assertNotIn(b"Secret", data)
        self.assertNotIn(b"secret", data)

    def test_sync_collection_reports_changes_and_deletions(self):
        node_path = self.path()
        body = (b'<d:sync-collection xmlns:d="DAV:"><d:sync-token>%s</d:sync-token><d:sync-level>1</d:sync-level>'
                b'<d:prop><d:getetag/></d:prop></d:sync-collection>')
        status, _h, data = self.call("REPORT", node_path, body % b"", Depth="1")
        token = etree.fromstring(data).findtext("d:sync-token", namespaces=NS)
        self.assertTrue(token.startswith(SYNC_PREFIX))
        uid = "y-%s" % uuid.uuid4()
        self.put(uid, vcal(vevent(uid, self.start, self.start + datetime.timedelta(hours=1))))
        status, _h, data = self.call("REPORT", node_path, body % token.encode(), Depth="1")
        root = etree.fromstring(data)
        hrefs = [r.findtext("d:href", namespaces=NS) for r in root.findall("d:response", NS)]
        self.assertTrue(any(h.endswith(uid + ".ics") for h in hrefs))
        token2 = root.findtext("d:sync-token", namespaces=NS)
        self.event(uid).unlink()
        status, _h, data = self.call("REPORT", node_path, body % token2.encode(), Depth="1")
        gone = [r for r in etree.fromstring(data).findall("d:response", NS)
                if (r.findtext("d:status", namespaces=NS) or "").startswith("HTTP/1.1 404")]
        self.assertEqual(len(gone), 1)
        # An invented token is refused, which makes the client start over.
        status, _h, data = self.call("REPORT", node_path, body % b"https://evil/1", Depth="1")
        self.assertEqual(status, 403)

    def test_refused_request_rolls_back_its_writes(self):
        uid = "z-%s" % uuid.uuid4()
        good = vevent(uid, self.start, self.start + datetime.timedelta(hours=1))
        # Two VEVENTs with different UIDs: refused after parsing, nothing stays.
        status, _h, _b = self.put(uid, vcal(good, vevent("other", self.start, self.start)))
        self.assertEqual(status, 403)
        self.assertFalse(self.event(uid))

    def test_refusal_after_writes_leaves_nothing(self):
        # Odoo creates the event, then detaches it from a rule it does not
        # match (a Monday with BYDAY=TU): refused after writing, all rolled back.
        uid = "rb-%s" % uuid.uuid4()
        monday = datetime.datetime(2027, 3, 1, 15, 0)
        body = vcal(vevent(uid, monday, monday + datetime.timedelta(hours=1),
                           extra="RRULE:FREQ=WEEKLY;BYDAY=TU;COUNT=3\r\n"))
        status, _h, _b = self.put(uid, body)
        self.assertEqual(status, 403)
        self.env.invalidate_all()
        self.assertFalse(self.env["calendar.event"].with_context(active_test=False).search(
            [("bf_dav_uid", "=", uid)]))
        self.assertFalse(self.env["calendar.event"].with_context(active_test=False).search(
            [("bf_dav_href", "=", uid + ".ics")]))

    def test_series_master_is_the_common_shape(self):
        start = datetime.datetime(2027, 3, 2, 15, 0)
        ev = self.env["calendar.event"].with_user(self.user).with_context(tz="America/Toronto").create({
            "name": "Shape series", "start": start, "stop": start + datetime.timedelta(hours=1),
            "recurrency": True, "rrule_type": "weekly", "tue": True, "end_type": "count", "count": 6,
            "event_tz": "America/Toronto", "partner_ids": [(6, 0, [self.user.partner_id.id])]})
        rec = ev.recurrence_id
        occs = rec.calendar_event_ids.sorted("start")
        occs.attendee_ids.write({"state": "accepted"})
        occs[0].attendee_ids.write({"state": "declined"})
        status, headers, data = self.call("GET", self.path(rec.bf_dav_href))
        obj = ical.parse_calendar(data, "UTC")
        self.assertEqual(len(obj.exceptions), 1)
        self.assertEqual(obj.master.attendees[0].partstat, "ACCEPTED")
        states = {a.id: a.state for a in occs.attendee_ids}
        self.assertEqual(self.call("PUT", self.path(rec.bf_dav_href), data, **{"If-Match": headers["ETag"]})[0], 204)
        self.assertEqual({a.id: a.state for a in occs.attendee_ids}, states)

    def sync(self, token=""):
        body = (b'<d:sync-collection xmlns:d="DAV:"><d:sync-token>%s</d:sync-token><d:sync-level>1</d:sync-level>'
                b'<d:prop><d:getetag/></d:prop></d:sync-collection>') % token.encode()
        root = etree.fromstring(self.call("REPORT", self.path(), body, Depth="1")[2])
        out = {}
        for r in root.findall("d:response", NS):
            st = r.findtext("d:status", namespaces=NS)
            out[r.findtext("d:href", namespaces=NS).rsplit("/", 1)[-1]] = "404" if st and " 404" in st else "200"
        return out, root.findtext("d:sync-token", namespaces=NS)

    def test_rebuilt_series_is_not_reported_gone(self):
        # old key and new key share the address.
        uid = "rb2-%s" % uuid.uuid4()
        master = ("BEGIN:VEVENT\r\nUID:%s\r\nSUMMARY:S\r\nDTSTART;TZID=America/Toronto:20270302T100000\r\n"
                  "DTEND;TZID=America/Toronto:20270302T110000\r\nRRULE:FREQ=WEEKLY;BYDAY=TU;COUNT=4\r\n"
                  "END:VEVENT\r\n" % uid)
        self.put(uid, vcal(master, tz=True))
        _seen, token = self.sync()
        self.put(uid, vcal(master.replace("T100000", "T120000").replace("T110000", "T130000"), tz=True))
        changes, _t = self.sync(token)
        self.assertEqual(changes.get(uid + ".ics"), "200")
        single = "one-%s" % uuid.uuid4()
        stop = self.start + datetime.timedelta(hours=1)
        self.put(single, vcal(vevent(single, self.start, stop)))
        _seen, token = self.sync()
        self.put(single, vcal(vevent(single, self.start, stop, extra="RRULE:FREQ=WEEKLY;COUNT=3\r\n")))
        changes, _t = self.sync(token)
        self.assertEqual(changes.get(single + ".ics"), "200")

    def test_weekly_without_weekday_in_new_zealand_morning(self):
        # Odoo reads the weekday in UTC (the day before).
        for rule in ("FREQ=WEEKLY;COUNT=4", "FREQ=MONTHLY;COUNT=3", "FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR;COUNT=5"):
            uid = "nz-%s" % uuid.uuid4()
            ev = ("BEGIN:VEVENT\r\nUID:%s\r\nSUMMARY:NZ\r\nDTSTART;TZID=Pacific/Auckland:20270303T090000\r\n"
                  "DTEND;TZID=Pacific/Auckland:20270303T100000\r\nRRULE:%s\r\nEND:VEVENT\r\n" % (uid, rule))
            status, _h, data = self.put(uid, vcal(ev))
            self.assertEqual(status, 201, rule)
            _rec, events = self.series(uid)
            import pytz
            local = [pytz.utc.localize(e.start).astimezone(pytz.timezone("Pacific/Auckland")) for e in events]
            if "WEEKLY" in rule:
                self.assertEqual({d.weekday() for d in local}, {2}, rule)
            elif "MONTHLY" in rule:
                self.assertEqual({d.day for d in local}, {3}, rule)
            else:
                self.assertEqual(len(events), 5)
                self.assertTrue(all(d.weekday() < 5 for d in local))

    def test_series_edited_in_odoo_keeps_its_identity(self):
        start = datetime.datetime(2027, 3, 2, 15, 0)
        ev = self.env["calendar.event"].with_user(self.user).with_context(tz="America/Toronto").create({
            "name": "Id series", "start": start, "stop": start + datetime.timedelta(hours=1),
            "recurrency": True, "rrule_type": "weekly", "tue": True, "end_type": "count", "count": 4,
            "event_tz": "America/Toronto", "partner_ids": [(6, 0, [self.user.partner_id.id])]})
        rec = ev.recurrence_id
        uid, href = rec.bf_dav_uid, rec.bf_dav_href
        self.env["bf.dav.resource.extra"]._store("r%d" % rec.id, {"": ["ATTACH:https://x.example/f"]})
        third = rec.calendar_event_ids.sorted("start")[2]
        third.with_context(tz="America/Toronto").write({
            "start": third.start + datetime.timedelta(hours=1), "stop": third.stop + datetime.timedelta(hours=1),
            "recurrence_update": "all_events"})
        new_rec = self.env["calendar.recurrence"].search([("bf_dav_uid", "=", uid)])
        self.assertTrue(new_rec)
        self.assertEqual(new_rec.bf_dav_href, href)
        self.assertIn(b"ATTACH:https://x.example/f", self.call("GET", self.path(href))[2])

    def test_attendee_cannot_rewrite_a_system_event_in_someone_elses_calendar(self):
        root = self.env.ref("base.user_root")
        ev = self.env["calendar.event"].sudo().create({
            "name": "System meeting", "user_id": root.id, "start": self.start,
            "stop": self.start + datetime.timedelta(hours=1), "bf_dav_calendar_id": self.cal.id,
            "partner_ids": [(6, 0, [self.user.partner_id.id, self.other.partner_id.id])]})
        path_other = "/dav/calendars/%d/%s/%s" % (
            self.other.id, self.env["bf.dav.calendar"]._bf_dav_default_for(self.other).slug, ev.bf_dav_href)
        status, headers, data = self.call("GET", path_other, user=self.other)
        self.assertEqual(status, 200)
        self.call("PUT", path_other, data.replace(b"SUMMARY:System meeting", b"SUMMARY:Hijack"), user=self.other)
        self.assertEqual(ev.name, "System meeting")
        # Its owner can, and becomes its organizer.
        status, headers, data = self.call("GET", self.path(ev.bf_dav_href))
        self.assertIn(b"ORGANIZER", data)
        self.assertIn(b"mailto:dav.owner@example.com", data.split(b"ORGANIZER", 1)[1][:120])
        self.call("PUT", self.path(ev.bf_dav_href), data.replace(b"SUMMARY:System meeting", b"SUMMARY:Mine"))
        self.assertEqual(ev.name, "Mine")
        self.assertEqual(ev.user_id, self.user)

    def test_yearly_series_made_in_odoo_survives_an_unchanged_put(self):
        start = datetime.datetime(2027, 3, 18, 13, 0)
        ev = self.env["calendar.event"].with_user(self.user).with_context(tz="America/Toronto").create({
            "name": "Yearly", "start": start, "stop": start + datetime.timedelta(hours=1),
            "recurrency": True, "rrule_type": "yearly", "end_type": "count", "count": 3,
            "event_tz": "America/Toronto", "partner_ids": [(6, 0, [self.user.partner_id.id])]})
        rec = ev.recurrence_id
        stamp = {(e.id, e.write_date) for e in rec.calendar_event_ids}
        status, headers, data = self.call("GET", self.path(rec.bf_dav_href))
        self.assertEqual(self.call("PUT", self.path(rec.bf_dav_href), data, **{"If-Match": headers["ETag"]})[0], 204)
        self.assertTrue(rec.exists())
        self.assertEqual({(e.id, e.write_date) for e in rec.calendar_event_ids}, stamp)

    def test_concurrent_creation_is_told_to_retry(self):
        import odoo
        uid = "lk-%s" % uuid.uuid4()
        name = uid + ".ics"
        other = odoo.sql_db.db_connect(self.env.cr.dbname).cursor()
        try:
            other.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ["bf_dav:%d:%s" % (self.cal.id, name)])
            status, headers, _b = self.put(uid, vcal(vevent(uid, self.start, self.start + datetime.timedelta(hours=1))))
            self.assertEqual(status, 503)
            self.assertEqual(headers.get("Retry-After"), "2")
            self.assertFalse(self.event(uid))
        finally:
            other.rollback()
            other.close()

    def test_events_wear_their_calendar_color_in_odoo(self):
        if "color" not in self.env["calendar.event"]._fields:
            self.skipTest("no color field on events")
        perso = self.env["bf.dav.calendar"].sudo().create({"name": "Perso", "owner_id": self.user.id, "odoo_color": 1})
        self.cal.sudo().odoo_color = 4
        ev = self.env["calendar.event"].with_user(self.user).create({
            "name": "Colored", "start": self.start, "stop": self.start + datetime.timedelta(hours=1)})
        self.assertEqual(ev.bf_dav_calendar_id, self.cal)
        self.assertEqual(ev.color, 4)
        ev.write({"bf_dav_calendar_id": perso.id})
        self.assertEqual(ev.color, 1)
        perso.odoo_color = 9
        ev.invalidate_recordset()
        self.assertEqual(ev.color, 9)
        views = self.env["calendar.event"].with_user(self.user).get_views([(False, "calendar")])
        arch = views["views"]["calendar"]["arch"]
        self.assertIn('name="bf_dav_calendar_id"', arch)
        self.assertIn('filters="1"', arch)

    def test_other_users_home_is_closed(self):
        status, _h, _b = self.call("PROPFIND", "/dav/calendars/%d/" % self.other.id, Depth="1")
        self.assertEqual(status, 403)
        status, _h, _b = self.call("GET", "/dav/calendars/%d/%s/x.ics" % (
            self.user.id, "s-%d" % self.env["bf.dav.calendar"]._bf_dav_default_for(self.other).id))
        self.assertEqual(status, 404)

    def test_discovery_properties(self):
        body = (b'<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop>'
                b'<c:calendar-home-set/><c:calendar-user-address-set/><d:current-user-principal/>'
                b'</d:prop></d:propfind>')
        status, _h, data = self.call("PROPFIND", "/dav/principals/%d/" % self.user.id, body, Depth="0")
        self.assertEqual(status, 207)
        root = etree.fromstring(data)
        self.assertEqual(root.findtext(".//c:calendar-home-set/d:href", namespaces=NS),
                         "/dav/calendars/%d/" % self.user.id)
        self.assertIn("mailto:dav.owner@example.com",
                      [h.text for h in root.findall(".//c:calendar-user-address-set/d:href", NS)])


@tagged("post_install", "-at_install")
class TestCardDav(DavCase):

    def test_user_without_contact_rights_cannot_create(self):
        uid = "n-%s" % uuid.uuid4()
        card = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:%s\r\nFN:Nope\r\nEND:VCARD\r\n" % uid
        status, _h, _b = self.call("PUT", "/dav/addressbooks/%d/company/%s.vcf" % (self.other.id, uid),
                                   card.encode(), user=self.other)
        self.assertEqual(status, 403)
        self.assertFalse(self.env["res.partner"].search([("bf_dav_uid", "=", uid)]))

    def book_path(self, name=""):
        return "/dav/addressbooks/%d/company/%s" % (self.user.id, name)

    def test_company_book_serves_contacts_without_user(self):
        partner = self.env["res.partner"].create({"name": "Plain Contact", "email": "plain@example.com"})
        status, _h, data = self.call("GET", self.book_path(partner.bf_dav_href))
        self.assertEqual(status, 200)
        self.assertIn(b"FN:Plain Contact", data)

    def test_card_round_trip_keeps_what_odoo_does_not_hold(self):
        uid = "c-%s" % uuid.uuid4()
        card = ("BEGIN:VCARD\r\nVERSION:3.0\r\nUID:%s\r\nFN:Card Person\r\nEMAIL:one@example.com\r\n"
                "EMAIL:two@example.com\r\nTEL;TYPE=CELL:+1 555 0101\r\nNOTE:from the phone\r\nEND:VCARD\r\n" % uid)
        status, _h, _b = self.call("PUT", self.book_path(uid + ".vcf"), card.encode())
        self.assertEqual(status, 201)
        partner = self.env["res.partner"].search([("bf_dav_uid", "=", uid)])
        self.assertEqual(partner.email, "one@example.com")
        self.assertEqual(partner.mobile, "+1 555 0101")
        self.assertFalse(partner.comment, "notes from a phone stay out of Odoo")
        data = self.call("GET", self.book_path(uid + ".vcf"))[2]
        self.assertIn(b"two@example.com", data)
        self.assertIn(b"NOTE:from the phone", data)
        self.assertEqual(self.call("DELETE", self.book_path(uid + ".vcf"))[0], 204)
        self.assertFalse(partner.active, "a contact deleted on a phone is archived, never destroyed")
        self.assertTrue(partner.exists())

    def test_unchanged_card_keeps_its_country(self):
        # 'Allemagne' searched as a 2-letter code found Albania.
        for code in ("DE", "NZ", "GB", "VN", "NE"):
            country = self.env["res.country"].search([("code", "=", code)])
            partner = self.env["res.partner"].create({"name": "Card %s" % code, "city": "X",
                                                      "country_id": country.id})
            path = self.book_path(partner.bf_dav_href)
            for lang in ("fr_CA", "en_US"):
                if not self.env["res.lang"]._lang_get(lang):
                    continue
                svc = self.env["bf.dav.service"].with_user(self.user).with_context(lang=lang)
                status, headers, data = svc._handle("GET", path, {}, b"")
                status, _h, _b = svc._handle("PUT", path, {"If-Match": headers["ETag"]}, data)
                self.assertEqual(status, 204)
                self.assertEqual(partner.country_id, country, "%s in %s" % (code, lang))

    def test_country_name_odoo_does_not_know_keeps_the_country(self):
        germany = self.env["res.country"].search([("code", "=", "DE")])
        partner = self.env["res.partner"].create({"name": "Card Deutschland", "city": "Berlin",
                                                  "country_id": germany.id})
        path = self.book_path(partner.bf_dav_href)
        status, headers, data = self.call("GET", path)
        # "Alemania" cut to a 2-letter code would read "AL": Albania.
        data = data.decode().replace("Allemagne", "Alemania").replace("Germany", "Alemania")
        self.assertIn("Alemania", data)
        self.assertEqual(self.call("PUT", path, data.encode(), **{"If-Match": headers["ETag"]})[0], 204)
        self.assertEqual(partner.country_id, germany)

    def test_card_notes_are_per_user(self):
        partner = self.env["res.partner"].create({"name": "Shared Contact", "email": "shared.c@example.com"})
        path = self.book_path(partner.bf_dav_href)
        data = self.call("GET", path)[2].decode()
        self.call("PUT", path, data.replace("END:VCARD", "NOTE:mine only\r\nEND:VCARD").encode())
        other = self.env["bf.dav.service"].with_user(self.other)._handle(
            "GET", "/dav/addressbooks/%d/company/%s" % (self.other.id, partner.bf_dav_href), {}, b"")[2]
        self.assertNotIn(b"mine only", other)
        self.assertIn(b"mine only", self.call("GET", path)[2])

    def test_personal_card_is_kept_byte_for_byte(self):
        uid = "p-%s" % uuid.uuid4()
        card = "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:%s\r\nFN:Private\r\nEND:VCARD\r\n" % uid
        path = "/dav/addressbooks/%d/personal/%s.vcf" % (self.user.id, uid)
        status, headers, _b = self.call("PUT", path, card.encode())
        self.assertEqual(status, 201)
        status, headers2, data = self.call("GET", path)
        self.assertEqual(data.decode(), card)
        self.assertEqual(headers["ETag"], headers2["ETag"])
        self.assertFalse(self.env["res.partner"].search([("bf_dav_uid", "=", uid)]))


@tagged("post_install", "-at_install")
class TestDavHttp(HttpCase):

    def setUp(self):
        super().setUp()
        dav_users.clear_auth_cache()
        self.user = self.env["res.users"].create({
            "name": "Http Dav", "login": "http.dav@example.com", "email": "http.dav@example.com",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        self.key = self.env["res.users.apikeys"].with_user(self.user).sudo()._generate("dav", "test", None)
        self.rpc_key = self.env["res.users.apikeys"].with_user(self.user).sudo()._generate(None, "rpc", None)

    def auth(self, login, key):
        return {"Authorization": "Basic " + base64.b64encode(("%s:%s" % (login, key)).encode()).decode()}

    def test_wizard_gives_the_public_address(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_dav_server.public_url", "https://dav.example.com/")
        wiz = self.env["bf.dav.password.wizard"].with_user(self.user).create({"name": "x"})
        self.assertEqual(wiz.server_url, "https://dav.example.com")
        self.assertEqual(wiz.caldav_url, "https://dav.example.com/dav/calendars/%d/" % self.user.id)

    def test_wizard_windows_have_a_title(self):
        self.assertTrue(self.user.with_user(self.user).action_bf_dav_new_password().get("name"))
        wiz = self.env["bf.dav.password.wizard"].with_user(self.user).create({"name": "x"})
        # Past the identity check (it needs a browser session): the window that
        # shows the password keeps its title.
        shown = type(wiz).action_generate.__wrapped__(wiz)
        self.assertTrue(shown.get("name"))
        self.assertTrue(wiz.key)

    def test_well_known_redirects(self):
        res = self.url_open("/.well-known/caldav", allow_redirects=False)
        self.assertEqual(res.status_code, 301)
        self.assertTrue(res.headers["Location"].endswith("/dav/"))

    def test_app_password_scope(self):
        body = '<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>'
        ok = self.opener.request("PROPFIND", self.base_url() + "/dav/", data=body,
                                 headers=dict(self.auth("http.dav@example.com", self.key), Depth="0"))
        self.assertEqual(ok.status_code, 207)
        self.assertIn(b"/dav/principals/%d/" % self.user.id, ok.content)
        # An RPC key opens the whole API: it must not open the calendars.
        refused = self.opener.request("PROPFIND", self.base_url() + "/dav/", data=body,
                                      headers=dict(self.auth("http.dav@example.com", self.rpc_key), Depth="0"))
        self.assertEqual(refused.status_code, 401)
        wrong_login = self.opener.request("PROPFIND", self.base_url() + "/dav/", data=body,
                                          headers=dict(self.auth("admin", self.key), Depth="0"))
        self.assertEqual(wrong_login.status_code, 401)
        self.assertIn("Basic", wrong_login.headers.get("WWW-Authenticate", ""))
