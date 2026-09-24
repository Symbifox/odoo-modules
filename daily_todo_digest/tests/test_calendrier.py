# -*- coding: utf-8 -*-
"""The day's calendar events in the digest.

🔴 An event belongs to someone through their ATTENDANCE: the events pulled from
Nextcloud are organized by the system user, so reading `user_id` would miss
most of them. The day is the recipient's local day: a recipient may read the
digest in Pacific/Auckland while the meetings are set in Montréal.

Frozen at 2026-09-15 16:00 UTC = Wednesday 2026-09-16 04:00 in Auckland
(NZST, UTC+12), the sending hour. The Auckland day runs from
2026-09-15 12:00 UTC to 2026-09-16 12:00 UTC.
"""

from datetime import date
from unittest import SkipTest
from unittest.mock import patch

from freezegun import freeze_time

from odoo.fields import Command
from odoo.tests import TransactionCase, tagged

from odoo.addons.daily_todo_digest.models.daily_digest import contrast_ratio, on_white

NOW = "2026-09-15 16:00:00"
DAY = date(2026, 9, 16)


@tagged("post_install", "-at_install")
class TestDigestCalendar(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "calendar.event" not in cls.env:
            raise SkipTest("calendar is not installed: the digest has no calendar section")
        cls.env = cls.env(context=dict(cls.env.context, no_mail_to_attendees=True, tracking_disable=True))
        cls.env["res.lang"]._activate_lang("fr_CA")
        # A user reads their language only when it is active: dates fall back
        # to the default language otherwise (en_US is often inactive).
        cls.env["res.lang"]._activate_lang("en_US")
        cls.env["ir.module.module"]._load_module_terms(["daily_todo_digest"], ["fr_CA"], overwrite=True)
        cls.olive = cls.env["res.users"].create({
            "name": "Olive Calendrier", "login": "olive.calendrier@example.com",
            "email": "olive.calendrier@example.com", "lang": "fr_CA", "tz": "Pacific/Auckland",
        })
        cls.has_secondary_tz = "secondary_tz" in cls.env["res.users"]._fields
        if cls.has_secondary_tz:
            cls.olive.secondary_tz = "America/Montreal"
        cls.jane = cls.env["res.users"].create({
            "name": "Jane Calendar", "login": "jane.calendar@example.com",
            "email": "jane.calendar@example.com", "lang": "en_US", "tz": "Pacific/Auckland",
        })
        cls.other = cls.env["res.users"].create({
            "name": "Autre Personne", "login": "autre.calendrier@example.com",
            "email": "autre.calendrier@example.com", "tz": "America/Toronto",
        })
        cls.system = cls.env.ref("base.user_root")
        cls.config = cls.env["daily.digest.config"].create({
            "name": "Calendar digest",
            "user_ids": [Command.set([cls.olive.id, cls.jane.id])],
            "include_weather": False, "include_quote": False, "include_meetings": False,
        })

        def event(name, start, stop, partners, **vals):
            return cls.env["calendar.event"].create({
                "name": name, "start": start, "stop": stop, "user_id": cls.system.id,
                "partner_ids": [Command.set(partners.ids)], **vals,
            })

        both = cls.olive.partner_id | cls.jane.partner_id
        cls.expected = [
            # Organized by __system__, as Nextcloud does: read through attendance.
            event("Rencontre statutaire", "2026-09-15 18:00:00", "2026-09-15 18:30:00", both),
            event("Juste après minuit", "2026-09-15 12:30:00", "2026-09-15 13:00:00", both),
            event("Fin de journée", "2026-09-16 11:30:00", "2026-09-16 12:30:00", both),
            event("Commencée la veille", "2026-09-15 10:00:00", "2026-09-15 14:00:00", both),
            event("Rappel à minuit pile", "2026-09-15 12:00:00", "2026-09-15 12:00:00", both),
            event("Christchurch", "2026-09-16 08:00:00", "2026-09-18 18:00:00", both,
                  allday=True, start_date="2026-09-16", stop_date="2026-09-18"),
            # Odoo's all-day end is inclusive: the last day of a trip is still in it.
            event("Dernier jour du voyage", "2026-09-14 08:00:00", "2026-09-16 18:00:00", both,
                  allday=True, start_date="2026-09-14", stop_date="2026-09-16"),
            event("<b>Injection</b>", "2026-09-15 20:00:00", "2026-09-15 20:30:00", both,
                  location="javascript:alert(1)"),
            event("Appel vidéo", "2026-09-15 21:00:00", "2026-09-15 21:30:00", both,
                  location="Salle 2", videocall_location="https://meet.example.com/abc"),
            event("Appel au téléphone", "2026-09-15 22:30:00", "2026-09-15 23:00:00", both,
                  videocall_location="Jean appelle au 514 555-0100"),
        ]
        declined = event("Refusée", "2026-09-15 19:00:00", "2026-09-15 19:30:00", both)
        declined.attendee_ids.filtered(lambda a: a.partner_id == cls.olive.partner_id).state = "declined"
        cls.not_expected = [
            declined,
            event("Pour quelqu'un d'autre", "2026-09-15 18:00:00", "2026-09-15 19:00:00", cls.other.partner_id),
            event("Veille au soir", "2026-09-15 11:30:00", "2026-09-15 11:59:00", both),
            event("Finie à minuit", "2026-09-15 11:00:00", "2026-09-15 12:00:00", both),
            event("Lendemain", "2026-09-16 12:00:00", "2026-09-16 12:30:00", both),
            event("Journée d'hier", "2026-09-15 08:00:00", "2026-09-15 18:00:00", both,
                  allday=True, start_date="2026-09-15", stop_date="2026-09-15"),
        ]
        cls.has_event_status = "bf_event_status" in cls.env["calendar.event"]._fields
        if cls.has_event_status:
            # bf_calendar_invite keeps a cancelled meeting active, struck through.
            cls.not_expected.append(event(
                "Annulée", "2026-09-15 23:00:00", "2026-09-15 23:30:00", both, bf_event_status="cancelled",
            ))
            cls.expected.append(event(
                "Confirmée", "2026-09-15 23:30:00", "2026-09-15 23:45:00", both, bf_event_status="confirmed",
            ))
        archived = event("Archivée", "2026-09-15 22:00:00", "2026-09-15 22:30:00", both)
        archived.active = False
        cls.not_expected.append(archived)

    def _names(self, user):
        config = self.config.with_context(lang=user.lang, tz=user.tz)
        with freeze_time(NOW):
            data = config._gather_digest_data(user, tz_name=user.tz)
        return [e["name"] for e in data["events_by_user"][user.id]]

    def _body(self, user):
        # Kept in the queue: sent mail is auto-deleted, and there would be nothing to read.
        with freeze_time(NOW), patch.object(type(self.env["mail.mail"]), "send", lambda self, *a, **k: True):
            self.config.with_context({})._send_digest(test_user=user)
        mail = self.env["mail.mail"].search([("email_to", "=", user.email)], order="id desc", limit=1)
        self.assertTrue(mail)
        return mail

    def test_the_recipients_day_by_attendance(self):
        names = self._names(self.olive)
        self.assertCountEqual(names, [e.name for e in self.expected])
        for ev in self.not_expected:
            self.assertNotIn(ev.name, names)

    def test_all_day_first_then_by_start(self):
        names = self._names(self.olive)
        self.assertEqual(names[0:2], ["Dernier jour du voyage", "Christchurch"])
        self.assertEqual(names[2:4], ["Commencée la veille", "Rappel à minuit pile"])
        self.assertEqual(names[-1], "Fin de journée")

    def test_a_declined_event_stays_for_the_one_who_accepted(self):
        self.assertIn("Refusée", self._names(self.jane))
        self.assertNotIn("Refusée", self._names(self.olive))

    def test_times_read_in_the_recipients_time_zone(self):
        corps = self._body(self.olive).body_html
        self.assertIn("Événements du jour", corps)
        self.assertIn("06:00 – 06:30", corps)
        # Crossing midnight names the days; an end names its day only when it differs from the start's.
        self.assertIn("mar. 22:00 – mer. 02:00", corps)
        self.assertIn("23:30 – jeu. 00:30", corps)
        self.assertIn("Toute la journée", corps)
        self.assertIn("jusqu&#39;au ven. 18/09", corps)
        # Ending today, the trip names no end.
        self.assertEqual(corps.count("jusqu&#39;au"), 1)
        self.assertIn(f"{len(self.expected)} événement(s)", corps)

    def test_the_secondary_time_zone_gives_the_montreal_times(self):
        if not self.has_secondary_tz:
            self.skipTest("web_calendar_secondary_timezone is not installed")
        corps = self._body(self.olive).body_html
        # 18:00 UTC is 14:00 in Montréal (EDT), on Tuesday 15.
        self.assertRegex(corps, r"mar\. 14:00 – 14:30 Montr[eé]al")
        self.olive.secondary_tz = False
        self.assertNotIn("14:00 – 14:30", self._body(self.olive).body_html)

    def test_an_english_recipient_reads_english(self):
        corps = self._body(self.jane).body_html
        self.assertIn("Today's calendar", corps)
        self.assertIn("All day", corps)
        self.assertIn("until Fri 18/09", corps)
        self.assertNotIn("Toute la journée", corps)

    def test_event_text_is_escaped_and_only_web_addresses_are_links(self):
        corps = self._body(self.olive).body_html
        self.assertNotIn("<b>Injection</b>", corps)
        self.assertIn("&lt;b&gt;Injection&lt;/b&gt;", corps)
        self.assertNotIn('href="javascript:', corps)
        self.assertIn('href="https://meet.example.com/abc"', corps)
        self.assertIn("Salle 2", corps)
        self.assertIn("Jean appelle au 514 555-0100", corps)
        self.assertIn(f"/odoo/calendar/{self.expected[0].id}", corps)

    def test_a_cancelled_meeting_is_left_out(self):
        if not self.has_event_status:
            self.skipTest("bf_calendar_invite is not installed")
        names = self._names(self.olive)
        self.assertNotIn("Annulée", names)
        self.assertIn("Confirmée", names)

    def test_the_toggle_removes_the_section(self):
        self.config.include_calendar_events = False
        corps = self._body(self.olive).body_html
        self.assertNotIn("Christchurch", corps)
        self.assertNotIn("Événements du jour", corps)
        self.assertNotIn("événement(s)", corps)

    def test_the_section_comes_before_what_is_due(self):
        self.env["mail.activity"].create({
            "res_model_id": self.env["ir.model"]._get_id("res.partner"),
            "res_id": self.olive.partner_id.id,
            "user_id": self.olive.id,
            "summary": "Relancer",
            "date_deadline": "2026-09-10",
        })
        corps = self._body(self.olive).body_html
        self.assertLess(corps.index("Événements du jour"), corps.index("Relancer"))

    def test_the_accent_variant_reads_behind_white_text(self):
        for accent in ("#29ABE1", "#29ABE2", "#FFC107"):
            variant = on_white(accent)
            self.assertGreaterEqual(contrast_ratio(variant, "#FFFFFF"), 4.5, accent)
            self.assertGreaterEqual(contrast_ratio(variant, "#E8F6FD"), 4.5, accent)
        # A color that already reads is left alone.
        self.assertEqual(on_white("#714B67"), "#714B67")
