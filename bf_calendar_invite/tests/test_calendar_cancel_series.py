"""Cancelling one meeting of a series, the rest of it, or all of it.

The cancellation dialog used to refuse a recurring meeting and send the person
to the form, where it was already. What it does now is ask the question core
asks of anyone who edits a series (this one, this one and the following ones,
the whole series) and answer it for exactly the dates chosen.

Three things have to hold and none of them is visible from the button:

* the scope reaches the dates it says, and leaves the others alone — above
  all the ones already held, which a strike-through would rewrite;
* the guests are written to ONCE, with one `.ics` that cancels each date by its
  own RECURRENCE-ID, instead of once per date or by a series-wide CANCEL;
* the first occurrence of a series is named by its slot like the others. Its
  invitation carries the series RRULE and no RECURRENCE-ID, and a cancellation
  built the same way reads, for a calendar client, as "the whole series".
"""

import re
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged
from odoo.tools import format_date


@tagged("post_install", "-at_install")
class TestCalendarCancelSeries(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.organiser = cls.env["res.users"].create({
            "name": "Series Organiser",
            "login": "bf_cancel_series_organiser",
            "email": "series.organiser@example.com",
        })
        cls.guest = cls.env["res.partner"].create({
            "name": "Series Guest One",
            "email": "series.guest.one@example.com",
        })
        cls.other_guest = cls.env["res.partner"].create({
            "name": "Series Guest Two",
            "email": "series.guest.two@example.com",
        })
        cls.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "https://odoo.example.com")

    # -- helpers ---------------------------------------------------------

    def _make_series(self, first_in_days, count=4, partners=None):
        """A daily series whose first occurrence starts `first_in_days` from now.

        Daily, not weekly: a weekly rule needs the weekday flag to match the
        start, and the test would then depend on the day it runs. Dates are
        relative to now for the same reason — a test written around a calendar
        date goes stale the day after it.
        """
        start = (fields.Datetime.now() + timedelta(days=first_in_days)).replace(
            microsecond=0, second=0)
        event = self.env["calendar.event"].create({
            "name": "Statutory meeting",
            "start": start,
            "stop": start + timedelta(hours=1),
            "user_id": self.organiser.id,
            "partner_ids": [Command.set((partners or self.guest).ids)],
            "recurrency": True,
            "rrule_type": "daily",
            "interval": 1,
            "end_type": "count",
            "count": count,
            "event_tz": "UTC",
        })
        occurrences = event.recurrence_id.calendar_event_ids.sorted("start")
        self.assertEqual(len(occurrences), count)
        return occurrences

    def _cancel(self, event, scope, **values):
        wizard = self.env["bf.calendar.event.cancel"].create(dict({
            "event_ids": [Command.set(event.ids)],
            "scope": scope,
        }, **values))
        wizard.action_apply()
        return wizard

    def _mails(self, occurrences):
        return self.env["mail.mail"].sudo().search([
            ("model", "=", "calendar.event"),
            ("res_id", "in", occurrences.ids),
        ])

    # Le gabarit se rend dans la langue des invités, et celle de la base
    # d'essai peut différer de celle de la CI : on accepte les deux libellés
    # plutôt que de dépendre de la langue active.
    SINGULAR = ("The meeting below is cancelled.",
                "La rencontre ci-dessous est annulée.")
    PLURAL = ("The meetings below are cancelled.",
              "Les rencontres ci-dessous sont annulées.")

    # Une ligne par date, repérée par son style : le nom du jour change avec la
    # langue, et le texte d'aperçu du courriel répète le début du corps, donc
    # compter le fuseau ou le jour compterait aussi l'aperçu.
    DATE_ROW = "color:#6B7280;margin-top:4px;"

    def _says(self, body, wording):
        return any(text in body for text in wording)

    def _attachment_text(self, mail):
        self.assertEqual(len(mail.attachment_ids), 1)
        attachment = mail.attachment_ids
        self.assertEqual(attachment.name, "cancellation.ics")
        return attachment.raw.decode("utf-8")

    # -- the scope -------------------------------------------------------

    def test_this_meeting_only_leaves_the_rest_of_the_series_alone(self):
        series = self._make_series(2)
        self._cancel(series[1], "self_only")
        self.assertEqual(series[1].bf_event_status, "cancelled")
        self.assertEqual(series[1].show_as, "free")
        for other in series - series[1]:
            self.assertNotEqual(other.bf_event_status, "cancelled")
            self.assertEqual(other.show_as, "busy")

    def test_this_one_and_the_following_ones(self):
        series = self._make_series(2)
        self._cancel(series[1], "future_events")
        self.assertNotEqual(series[0].bf_event_status, "cancelled")
        self.assertEqual(
            series[1:].mapped("bf_event_status"), ["cancelled"] * 3)
        self.assertEqual(series[1:].mapped("show_as"), ["free"] * 3)

    def test_the_whole_series_leaves_what_already_happened(self):
        """🔴 « Toute la série » ne réécrit pas le passé de l'agenda.

        Série quotidienne commencée il y a deux jours : les deux premières
        occurrences sont derrière nous, et elles ont eu lieu.
        """
        series = self._make_series(-2)
        past = series.filtered(lambda e: e.stop < fields.Datetime.now())
        coming = series - past
        self.assertEqual(len(past), 2)
        self.assertEqual(len(coming), 2)
        self._cancel(series[0], "all_events")
        for event in past:
            self.assertNotEqual(event.bf_event_status, "cancelled")
            self.assertEqual(event.show_as, "busy")
        for event in coming:
            self.assertEqual(event.bf_event_status, "cancelled")
            self.assertEqual(event.show_as, "free")

    def test_the_whole_series_does_not_depend_on_which_date_is_open(self):
        series = self._make_series(2)
        self._cancel(series[2], "all_events")
        self.assertEqual(series.mapped("bf_event_status"), ["cancelled"] * 4)

    def test_the_occurrences_stay_in_the_series(self):
        """Cancelled is a status, not a way out of the recurrence."""
        series = self._make_series(2)
        recurrence = series.recurrence_id
        self._cancel(series[0], "all_events")
        self.assertEqual(series.recurrence_id, recurrence)
        self.assertEqual(len(recurrence.calendar_event_ids), 4)
        self.assertTrue(all(series.mapped("follow_recurrence")))

    def test_the_reason_goes_on_every_date(self):
        series = self._make_series(2)
        self._cancel(series[0], "future_events", reason="Client unavailable")
        self.assertEqual(
            series.mapped("bf_cancellation_reason"), ["Client unavailable"] * 4)

    def test_a_plain_meeting_is_unchanged(self):
        """The scope is asked on a series; a single meeting never sees it."""
        event = self.env["calendar.event"].create({
            "name": "One-off",
            "start": fields.Datetime.now() + timedelta(days=3),
            "stop": fields.Datetime.now() + timedelta(days=3, hours=1),
            "user_id": self.organiser.id,
            "partner_ids": [Command.set(self.guest.ids)],
        })
        wizard = self.env["bf.calendar.event.cancel"].create({
            "event_ids": [Command.set(event.ids)],
        })
        self.assertFalse(wizard.has_series)
        self.assertFalse(wizard.target_summary)
        self.assertEqual(wizard.scope, "self_only")
        wizard.action_apply()
        self.assertEqual(event.bf_event_status, "cancelled")

    def test_the_dialog_counts_and_dates_what_it_would_reach(self):
        series = self._make_series(2)
        wizard = self.env["bf.calendar.event.cancel"].create({
            "event_ids": [Command.set(series[1].ids)],
        })
        self.assertTrue(wizard.has_series)
        self.assertEqual(wizard.scope, "self_only", "the safe answer is the default")
        self.assertFalse(wizard.target_summary, "one date needs no summary")
        wizard.scope = "future_events"
        self.assertEqual(wizard.target_ids, series[1:])
        self.assertTrue(wizard.target_summary.startswith("3 "))
        # The range itself, not only the count: a swapped or wrong-zone range must fail.
        first = format_date(self.env, fields.Datetime.context_timestamp(wizard, series[1].start).date())
        last = format_date(self.env, fields.Datetime.context_timestamp(wizard, series[-1].start).date())
        self.assertIn(first, wizard.target_summary)
        self.assertTrue(wizard.target_summary.endswith(last + "."), wizard.target_summary)
        wizard.scope = "all_events"
        self.assertEqual(wizard.target_ids, series)
        self.assertTrue(wizard.target_summary.startswith("4 "))

    def test_the_dialog_does_not_count_what_is_already_cancelled(self):
        series = self._make_series(2)
        self._cancel(series[1], "self_only")
        wizard = self.env["bf.calendar.event.cancel"].create({
            "event_ids": [Command.set(series[0].ids)],
            "scope": "all_events",
        })
        self.assertEqual(wizard.target_ids, series - series[1])

    def test_the_dialog_computes_on_an_unsaved_record(self):
        """🔴 L'état où le client web lit la boîte : un enregistrement NEUF.

        Le piège des invités en `NewId(origin=…)` a déjà coûté une passe de
        navigateur; la portée ajoute un calcul de plus sur ce même état.
        """
        series = self._make_series(2, partners=self.guest | self.organiser.partner_id)
        wizard = self.env["bf.calendar.event.cancel"].with_user(
            self.organiser,
        ).with_context(
            default_event_ids=[Command.set(series[1].ids)],
        ).new({"scope": "future_events"})
        self.assertEqual(len(wizard.target_ids), 3)
        self.assertIn(self.guest.name, wizard.recipient_ids.mapped("name"))
        self.assertNotIn(
            self.organiser.partner_id.name, wizard.recipient_ids.mapped("name"))

    def test_a_double_click_is_not_an_error(self):
        series = self._make_series(2)
        wizard = self._cancel(series[0], "all_events", notify=True)
        wizard.action_apply()
        self.assertEqual(len(self._mails(series)), 1)

    # -- the notice ------------------------------------------------------

    def test_one_notice_for_the_whole_action(self):
        """Quatre dates, un courriel."""
        series = self._make_series(2)
        self._cancel(series[0], "all_events", notify=True)
        mails = self._mails(series)
        self.assertEqual(len(mails), 1)
        self.assertIn(self.guest, mails.recipient_ids)

    def test_no_notice_unless_asked_on_a_series_either(self):
        series = self._make_series(2)
        self._cancel(series[0], "all_events")
        self.assertFalse(self._mails(series))

    def test_the_attachment_cancels_each_date_by_its_own_slot(self):
        series = self._make_series(2)
        uid_before = series[0]._bf_ics_uid_get()
        self._cancel(series[1], "future_events", notify=True)
        text = self._attachment_text(self._mails(series))
        self.assertEqual(text.count("BEGIN:VCALENDAR"), 1, "one calendar, not four")
        self.assertEqual(text.count("BEGIN:VEVENT"), 3)
        self.assertEqual(text.count("METHOD:CANCEL"), 1)
        self.assertEqual(text.count("STATUS:CANCELLED"), 3)
        self.assertEqual(text.count("RECURRENCE-ID"), 3)
        self.assertNotIn("RRULE", text, "a series-wide rule would cancel more than asked")
        self.assertEqual(set(re.findall(r"^UID:([^\r\n]+)", text, re.M)), {uid_before})

    def test_the_first_occurrence_is_named_by_its_slot(self):
        """🔴 Le cas qu'une annulation date par date ne rencontre jamais.

        La rencontre de BASE annonce le UID de la série et sa RRULE, sans
        RECURRENCE-ID. Annulée ainsi, elle dit « toute la série » à l'agenda
        des invités, même quand on n'en voulait qu'une.
        """
        series = self._make_series(2)
        base = series[0]
        self.assertEqual(series.recurrence_id.base_event_id, base)
        invitation = base._get_ics_file()[base.id].decode("utf-8")
        self.assertIn("RRULE", invitation)
        self.assertNotIn("RECURRENCE-ID", invitation)
        self._cancel(base, "self_only", notify=True)
        text = self._attachment_text(self._mails(series))
        self.assertEqual(text.count("BEGIN:VEVENT"), 1)
        self.assertIn("RECURRENCE-ID", text)
        self.assertNotIn("RRULE", text)
        self.assertIn("METHOD:CANCEL", text)

    def test_every_date_carries_a_newer_revision(self):
        series = self._make_series(2)
        before = series.mapped("bf_ics_sequence")
        self._cancel(series[0], "all_events", notify=True)
        self.assertEqual(
            series.mapped("bf_ics_sequence"), [seq + 1 for seq in before])
        text = self._attachment_text(self._mails(series))
        # One revision per VEVENT, each the one its occurrence now holds.
        found = sorted(int(n) for n in re.findall(r"^SEQUENCE:(\d+)", text, re.M))
        self.assertEqual(found, sorted(series.mapped("bf_ics_sequence")))

    def test_the_body_names_every_date(self):
        series = self._make_series(2)
        self._cancel(series[0], "all_events", notify=True)
        body = self._mails(series).body_html
        self.assertTrue(self._says(body, self.PLURAL))
        self.assertFalse(self._says(body, self.SINGULAR))
        self.assertEqual(body.count("line-through"), 1, "the title is named once")
        self.assertEqual(body.count(self.DATE_ROW), len(series))
        # Each row writes ITS date: four copies of the first one would count right.
        rows = [" ".join(re.sub(r"<[^>]+>", "", chunk).split())
                for chunk in re.findall(r"margin-top:\s*4px[^>]*>(.*?)</div>", body, re.S)]
        self.assertEqual(len(rows), len(series))
        self.assertEqual(len(set(rows)), len(series), rows)
        self.assertTrue(all(re.search(r"\d", row) for row in rows), rows)

    def test_a_single_date_reads_as_it_always_did(self):
        series = self._make_series(2)
        self._cancel(series[1], "self_only", notify=True)
        mail = self._mails(series)
        self.assertEqual(len(mail), 1)
        self.assertTrue(self._says(mail.body_html, self.SINGULAR))
        self.assertFalse(self._says(mail.body_html, self.PLURAL))
        self.assertEqual(mail.body_html.count(self.DATE_ROW), 1)
        self.assertEqual(self._attachment_text(mail).count("BEGIN:VEVENT"), 1)

    def test_an_all_day_meeting_is_cancelled_with_its_notice(self):
        """🔴 Already broken in 6.1.1: `format_date(value=…)` on the all-day branch.

        Odoo's render helper is `format_date(date, date_format, lang_code)`; the
        keyword the template used does not exist, so cancelling an all-day
        meeting with the notice ticked raised "Échec du rendu du modèle QWeb"
        and the cancellation itself was lost with it.
        """
        day = (fields.Datetime.now() + timedelta(days=5)).date()
        event = self.env["calendar.event"].create({
            "name": "Office closed",
            "allday": True,
            "start": day,
            "stop": day,
            "start_date": day,
            "stop_date": day,
            "user_id": self.organiser.id,
            "partner_ids": [Command.set(self.guest.ids)],
        })
        self.assertTrue(event.allday)
        self._cancel(event, "self_only", notify=True)
        self.assertEqual(event.bf_event_status, "cancelled")
        mail = self._mails(event)
        self.assertEqual(len(mail), 1)
        self.assertNotIn("NoneType", mail.body_html)
        self.assertEqual(mail.body_html.count(self.DATE_ROW), 1)
        row = mail.body_html.split(self.DATE_ROW, 1)[1].split("</div>", 1)[0]
        self.assertIn(str(day.year), row, "the row must write the day, not stay empty")

    def test_two_unrelated_meetings_with_the_same_guests_get_two_notices(self):
        """One notice per series: grouped on guests alone they shared a title."""
        day = fields.Datetime.now() + timedelta(days=3)
        budget, staff = (self.env["calendar.event"].create({
            "name": name, "start": day + timedelta(hours=offset),
            "stop": day + timedelta(hours=offset + 1),
            "user_id": self.organiser.id,
            "partner_ids": [Command.set(self.guest.ids)],
        }) for name, offset in (("Budget review", 0), ("Staff sync", 3)))
        wizard = self.env["bf.calendar.event.cancel"].create({
            "event_ids": [Command.set((budget | staff).ids)],
            "scope": "self_only", "notify": True,
        })
        wizard.action_apply()
        mails = self._mails(budget | staff)
        self.assertEqual(len(mails), 2, "two meetings, two messages")
        by_subject = {m.subject: m for m in mails}
        for name in ("Budget review", "Staff sync"):
            mail = next(m for s, m in by_subject.items() if name in s)
            self.assertEqual(mail.body_html.count(self.DATE_ROW), 1)
            self.assertEqual(self._attachment_text(mail).count("BEGIN:VEVENT"), 1)

    def test_the_context_cannot_widen_a_notice_beyond_the_series(self):
        """Ids in a request context are not believed, and never leave the series."""
        series = self._make_series(2)
        day = fields.Datetime.now() + timedelta(days=3)
        stranger = self.env["calendar.event"].create({
            "name": "Someone else's private matter", "start": day,
            "stop": day + timedelta(hours=1), "user_id": self.organiser.id,
        })
        ids = [series[0].id, series[1].id, stranger.id]
        regular = series[0].with_user(self.organiser).with_context(bf_cancellation_batch=ids)
        self.assertFalse(regular.env.su)
        self.assertEqual(regular._bf_cancellation_batch(), series[0],
                         "a caller's context is not believed")
        module_own = series[0].sudo().with_context(bf_cancellation_batch=ids)
        self.assertEqual(module_own._bf_cancellation_batch(), series[:2],
                         "the module's own send still merges, minus what is not in the series")

    def test_a_regular_user_cancelling_a_series_sends_one_merged_notice(self):
        """The path of a person, not of the superuser the other tests run as."""
        series = self._make_series(2)
        wizard = self.env["bf.calendar.event.cancel"].with_user(self.organiser).create({
            "event_ids": [Command.set(series[0].ids)],
            "scope": "all_events", "notify": True,
        })
        wizard.action_apply()
        mail = self._mails(series)
        self.assertEqual(len(mail), 1)
        self.assertEqual(self._attachment_text(mail).count("BEGIN:VEVENT"), len(series))
        self.assertEqual(mail.body_html.count(self.DATE_ROW), len(series))

    def _make_all_day_series(self, first_in_days, count=3):
        day = (fields.Datetime.now() + timedelta(days=first_in_days)).date()
        event = self.env["calendar.event"].create({
            "name": "Office closed", "allday": True,
            "start": day, "stop": day, "start_date": day, "stop_date": day,
            "user_id": self.organiser.id,
            "partner_ids": [Command.set(self.guest.ids)],
            "recurrency": True, "rrule_type": "daily", "interval": 1,
            "end_type": "count", "count": count, "event_tz": "UTC",
        })
        occurrences = event.recurrence_id.calendar_event_ids.sorted("start")
        self.assertEqual(len(occurrences), count)
        return occurrences

    def test_an_all_day_series_names_each_date_as_core_writes_it(self):
        """RECURRENCE-ID must have the type and zone of DTSTART (RFC 5545 §3.8.4.4).

        Core writes an all-day DTSTART as a floating date-time; a UTC RECURRENCE-ID
        names an instant no instance has, and a client does not match it.
        """
        series = self._make_all_day_series(3)
        self._cancel(series[0], "all_events", notify=True)
        text = self._attachment_text(self._mails(series))
        events = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S)
        self.assertEqual(len(events), len(series))
        for vevent in events:
            start = re.search(r"^DTSTART[^:\r\n]*:(\S+)", vevent, re.M).group(1)
            slot = re.search(r"^RECURRENCE-ID[^:\r\n]*:(\S+)", vevent, re.M).group(1)
            self.assertEqual(slot, start)
            self.assertFalse(slot.endswith("Z"), "floating, like the DTSTART it names")

    def test_the_whole_series_keeps_an_all_day_occurrence_that_is_still_today(self):
        """An all-day meeting ends with its LAST DAY, not at its 18:00 UTC stop."""
        series = self._make_all_day_series(0)
        today = fields.Datetime.now().replace(hour=21, minute=0, second=0)
        with patch.object(fields.Datetime, "now", return_value=today):
            reached = series[0].with_context(tz="America/Toronto")._bf_series_scope("all_events")
        self.assertEqual(reached, series, "today's all-day occurrence has not ended")

    def test_an_empty_choice_says_there_is_nothing_to_cancel(self):
        series = self._make_series(2, count=2)
        self._cancel(series[0], "all_events")
        wizard = self.env["bf.calendar.event.cancel"].create({
            "event_ids": [Command.set(series[0].ids)], "scope": "all_events",
        })
        self.assertFalse(wizard.target_ids)
        # The wording follows the language of the database running the test.
        self.assertTrue(
            "Nothing would change" in wizard.recipient_warning
            or "Rien ne changerait" in wizard.recipient_warning,
            wizard.recipient_warning,
        )
        self.assertNotIn("guest", wizard.recipient_warning, "not the 'no guest' reason")

    def test_a_date_already_cancelled_is_not_announced_twice(self):
        series = self._make_series(2)
        self._cancel(series[1], "self_only", notify=True)
        self.assertEqual(len(self._mails(series)), 1)
        self._cancel(series[0], "all_events", notify=True)
        mails = self._mails(series).sorted("id")
        self.assertEqual(len(mails), 2)
        self.assertEqual(self._attachment_text(mails[1]).count("BEGIN:VEVENT"), 3)

    def test_a_guest_invited_to_some_dates_hears_about_those_only(self):
        series = self._make_series(2)
        series[2].write({
            "partner_ids": [Command.set((self.guest | self.other_guest).ids)],
        })
        self._cancel(series[0], "all_events", notify=True)
        mails = self._mails(series)
        self.assertEqual(len(mails), 2, "one notice per guest list")
        by_guests = {
            frozenset(mail.recipient_ids.ids): self._attachment_text(mail).count(
                "BEGIN:VEVENT")
            for mail in mails
        }
        self.assertEqual(by_guests, {
            frozenset(self.guest.ids): 3,
            frozenset((self.guest | self.other_guest).ids): 1,
        })

    def test_nobody_is_written_to_when_no_guest_has_an_address(self):
        silent = self.env["res.partner"].create({"name": "No address"})
        series = self._make_series(2, partners=silent)
        self._cancel(series[0], "all_events", notify=True)
        self.assertFalse(self._mails(series))
        self.assertEqual(series.mapped("bf_event_status"), ["cancelled"] * 4)
