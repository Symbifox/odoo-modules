"""Tests for the meeting status and the POKE button.

The status is only worth having because it leaves Odoo: it is written into the
ICS `STATUS` property, read back from it, and shown on the popover. So these
walk the mapping in both directions, including the two values that must NOT
map — an event with no status, and a status a client invented — because those
are the ones a lenient implementation quietly turns into "confirmed".
"""

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCalendarStatus(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.organiser = cls.env["res.users"].create({
            "name": "Organiser",
            "login": "bf_status_organiser",
            "email": "organiser@example.com",
        })
        cls.guest = cls.env["res.partner"].create({
            "name": "Guest One",
            "email": "guest.one@example.com",
        })
        cls.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "https://odoo.example.com")

    def _make_event(self, **values):
        vals = {
            "name": "Project kickoff",
            "start": "2026-09-10 14:00:00",
            "stop": "2026-09-10 15:00:00",
            "user_id": self.organiser.id,
            "partner_ids": [Command.set([self.guest.id])],
        }
        vals.update(values)
        return self.env["calendar.event"].create(vals)

    # --- the field -----------------------------------------------------

    def test_a_new_event_is_confirmed(self):
        """Confirmed, not empty.

        An empty status would be the honest answer for an event nobody has
        ruled on, but it is the wrong default here: every event created before
        this field existed is already empty, and leaving new ones empty too
        makes the two indistinguishable in a filter.
        """
        self.assertEqual(self._make_event().bf_event_status, "confirmed")

    def test_the_field_carries_no_default(self):
        """🔴 Le contrôle qui garde l'historique, et qui a coûté une répétition.

        Un `default=` sur le champ est écrit dans TOUTES les lignes existantes
        au moment où la colonne est créée. Mesuré sur une copie de la
        production : la première version stampait « confirmée » sur les 15 464
        rencontres de l'historique, dont aucune n'avait été confirmée par qui
        que ce soit, et la première repoussée de l'agenda aurait porté
        `STATUS:CONFIRMED` vers Nextcloud pour toutes.

        Le défaut vit donc dans `create()`. Ce test échoue si quelqu'un le
        remet sur le champ « pour simplifier » : le symptôme, lui, ne se voit
        que sur une base qui a déjà un historique.
        """
        field = self.env["calendar.event"]._fields["bf_event_status"]
        self.assertIsNone(
            field.default,
            "un défaut est revenu sur le champ : il estampera tout "
            "l'historique au prochain déploiement sur une base neuve")

    def test_an_explicit_empty_status_is_respected(self):
        """Corollaire : on peut créer une rencontre sans statut."""
        event = self._make_event(bf_event_status=False)
        self.assertFalse(event.bf_event_status)

    def test_status_is_not_the_attendee_answer(self):
        """The two fields the whole feature exists to separate.

        Declining an invitation must not cancel the meeting, and cancelling a
        meeting must not rewrite anyone's answer. If these ever became the same
        field, this is the test that would notice.
        """
        event = self._make_event()
        event.bf_event_status = "cancelled"
        attendee = event.attendee_ids.filtered(
            lambda a: a.partner_id == self.guest)
        self.assertEqual(attendee.state, "needsAction",
                         "cancelling the meeting rewrote the guest's answer")

        event.bf_event_status = "confirmed"
        attendee.do_decline()
        self.assertEqual(event.bf_event_status, "confirmed",
                         "a guest declining cancelled the meeting itself")

    # --- the ICS mapping, both ways ------------------------------------

    def test_ics_status_round_trips(self):
        event = self._make_event()
        for odoo_value, ics_value in (
            ("tentative", "TENTATIVE"),
            ("confirmed", "CONFIRMED"),
            ("cancelled", "CANCELLED"),
        ):
            event.bf_event_status = odoo_value
            self.assertEqual(event._bf_ics_status(), ics_value)
            self.assertEqual(
                event._bf_status_from_ics(ics_value), odoo_value,
                "the reverse mapping lost %s" % ics_value)

    def test_an_event_without_a_status_carries_none(self):
        """False, not "CONFIRMED".

        This is the case that decides whether pushing the whole calendar to
        Nextcloud rewrites history: every event written before this field
        existed has an empty status, and a mapping that defaults them to
        CONFIRMED would stamp a confirmation on all of them at the next push.
        """
        event = self._make_event()
        event.bf_event_status = False
        self.assertFalse(event._bf_ics_status())

    def test_an_unknown_ics_status_is_dropped_not_guessed(self):
        event = self._make_event()
        for value in ("X-SOMETHING", "", "confirmedish", None):
            self.assertFalse(
                event._bf_status_from_ics(value),
                "%r was coerced into a status instead of being ignored" % value)

    def test_ics_status_is_case_insensitive(self):
        """Clients do write lowercase, whatever RFC 5545 says."""
        event = self._make_event()
        self.assertEqual(event._bf_status_from_ics("cancelled"), "cancelled")
        self.assertEqual(event._bf_status_from_ics(" Tentative "), "tentative")

    # --- the quick-create dialog ---------------------------------------

    def test_the_quick_create_dialog_carries_the_three_fields(self):
        """The whole point of the request: not having to open "More Options".

        Asserted on the rendered arch rather than on the inheritance record,
        because an xpath that lands in the wrong place still produces a valid
        view — it just puts the field somewhere nobody looks.
        """
        arch = self.env["calendar.event"].get_view(
            self.env.ref("calendar.view_calendar_event_form_quick_create").id,
            "form",
        )["arch"]
        for field in ("bf_event_status", "categ_ids", "alarm_ids"):
            self.assertIn('name="%s"' % field, arch,
                          "%s never reached the quick-create dialog" % field)

    def test_the_calendar_view_fetches_the_status(self):
        """Without this field in the arch it never reaches `rawRecord`.

        The popover's status buttons read `rawRecord.bf_event_status`, and the
        calendar model only fetches what the arch declares. Dropping the
        `invisible="1"` field would empty the buttons with no error anywhere —
        which is exactly the kind of failure a test has to catch instead.
        """
        arch = self.env["calendar.event"].get_view(
            self.env.ref("calendar.view_calendar_event_calendar").id,
            "calendar",
        )["arch"]
        self.assertIn('name="bf_event_status"', arch)


@tagged("post_install", "-at_install")
class TestCalendarPoke(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ⚠️ `_activate_lang` bascule le drapeau `active` du `res.lang` et
        # RIEN d'autre : les `.po` d'un module ne sont importés qu'à son
        # installation, pour les langues actives à ce moment-là. Sur une base
        # neuve où seul l'anglais l'est — la CI en fabrique une par lot — le
        # corps sortait en anglais et les assertions de langue tombaient, alors
        # que le code visé était bon. Sur les bases où le français était déjà
        # installé, personne ne le voyait.
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_US")
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_calendar_invite"], ["fr_CA"])
        cls.env.registry.clear_cache()
        cls.organiser = cls.env["res.users"].create({
            "name": "Organiser",
            "login": "bf_poke_organiser",
            "email": "organiser@example.com",
        })
        cls.guest = cls.env["res.partner"].create({
            "name": "Late Guest",
            "email": "late@example.com",
        })
        cls.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "https://odoo.example.com")

    def _make_event(self, **values):
        vals = {
            "name": "Weekly sync",
            "start": "2026-09-10 14:00:00",
            "stop": "2026-09-10 15:00:00",
            "user_id": self.organiser.id,
            "partner_ids": [Command.set([self.guest.id])],
        }
        vals.update(values)
        return self.env["calendar.event"].create(vals)

    def _composer(self, event):
        action = event.action_bf_poke()
        return self.env["mail.compose.message"].with_context(
            **action["context"]).create({})

    def test_poke_opens_on_the_poke_template(self):
        event = self._make_event()
        action = event.action_bf_poke()
        self.assertEqual(
            action["context"]["default_template_id"],
            self.env.ref("bf_calendar_invite.mail_template_calendar_poke").id,
            "POKE opened the composer on the invitation template")

    def test_poke_does_not_attach_an_ics(self):
        """The defect this guards against would move the meeting.

        The EMAIL button on the same model attaches invitation.ics. Reusing its
        action without clearing the report would send a second .ics for the same
        UID, which most clients read as an update: a note asking whether we are
        still meeting would silently redraw the entry in the guest's calendar.
        """
        composer = self._composer(self._make_event())
        self.assertNotIn("invitation.ics", composer.attachment_ids.mapped("name"))

    def test_poke_gives_the_video_link_back(self):
        event = self._make_event(
            videocall_location="https://meet.example.com/room-42")
        self.assertIn("https://meet.example.com/room-42", self._composer(event).body)

    def test_poke_prefers_the_video_link_over_the_location(self):
        """Someone who is missing needs the room, not the street address."""
        event = self._make_event(
            location="1 rue Principale",
            videocall_location="https://meet.example.com/room-42")
        self.assertEqual(event._bf_poke_join_url(),
                         "https://meet.example.com/room-42")

    def test_poke_falls_back_to_a_location_that_is_a_link(self):
        """An event created from a call room stores the URL in `location`."""
        event = self._make_event(location="https://meet.example.com/room-7")
        self.assertEqual(event._bf_poke_join_url(),
                         "https://meet.example.com/room-7")

    def test_a_physical_address_is_not_offered_as_a_link(self):
        event = self._make_event(location="1 rue Principale")
        self.assertFalse(event._bf_poke_join_url())
        self.assertIn("1 rue Principale", self._composer(event).body,
                      "the address was dropped instead of being written out")

    def test_poke_is_written_in_the_guests_language(self):
        """Same rule as the invitation: the reader's language, not the sender's."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.guest.lang = "fr_CA"
        event = self._make_event()
        self.assertEqual(event.bf_mail_lang, "fr_CA")
        body = self._composer(event).body
        self.assertNotIn("Just checking that we are still meeting", body,
                         "a French guest was poked in English")
