# -*- coding: utf-8 -*-
"""Invités additionnels et double confirmation (2.45.0)."""

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


@tagged("bf_appointment", "bf_appointment_guests")
class TestGuests(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC"))
        att = [Command.create({"name": "j%s" % d, "dayofweek": str(d),
                               "hour_from": 0.0, "hour_to": 24.0,
                               "day_period": "morning"}) for d in range(7)]
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "24/7 invites", "attendance_ids": att, "tz": "UTC"})
        res = cls.env["resource.resource"].create({
            "name": "invites mat", "calendar_id": cls.calendar.id,
            "resource_type": "material", "tz": "UTC"})
        combo = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([res.id])]})
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Type invités", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id, "is_public": True,
            "allow_guests": True, "max_guests": 3,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": combo.id})]})
        cls.demandeur = cls.env["res.partner"].create({
            "name": "Demandeur", "email": "demandeur@test.invalid"})
        cls.Guest = cls.env["resource.booking.guest"]

    def _booking(self):
        import pytz
        import datetime
        d = fields.Datetime.context_timestamp(
            self.booking_type, fields.Datetime.now()) + datetime.timedelta(hours=1)
        f = d + datetime.timedelta(days=10)
        quand = self.booking_type._bf_candidate_slots(d, f, limit=1)[0]
        return self.booking_type._bf_create_booking(
            quand.astimezone(pytz.utc).replace(tzinfo=None),
            partners=self.demandeur)

    # -- Analyse de la saisie ------------------------------------------------

    def test_parse_accepts_several_formats(self):
        brut = "a@x.invalid\nB@X.invalid, Nom <c@x.invalid>; d@x.invalid"
        adresses, _ec = self.Guest._bf_parse_emails(brut)
        self.assertEqual(adresses, ["a@x.invalid", "b@x.invalid",
                                    "c@x.invalid", "d@x.invalid"])

    def test_parse_drops_garbage_and_duplicates(self):
        brut = "bon@x.invalid\npas-une-adresse\nBON@X.invalid\n@rien\n"
        adresses, ecartees = self.Guest._bf_parse_emails(brut)
        self.assertEqual(adresses, ["bon@x.invalid"])
        self.assertEqual(ecartees, 3, "doublon et saisies fautives non comptés")

    def test_parse_excludes_the_requester(self):
        adresses, _e = self.Guest._bf_parse_emails(
            "demandeur@test.invalid\nautre@x.invalid",
            exclure=["demandeur@test.invalid"])
        self.assertEqual(adresses, ["autre@x.invalid"],
                         "le demandeur ne doit pas s'inviter lui-même")

    def test_parse_respects_the_cap(self):
        brut = "\n".join("g%d@x.invalid" % i for i in range(10))
        adresses, _e = self.Guest._bf_parse_emails(brut, maximum=3)
        self.assertEqual(len(adresses), 3)

    # -- Rien ne part sans confirmation --------------------------------------

    def test_guests_start_pending_and_create_no_contact(self):
        """🔴 Le cœur du dispositif : une adresse non confirmée n'est qu'une
        chaîne. Un formulaire public ne doit pas remplir le carnet d'adresses."""
        b = self._booking()
        self.Guest.create({"booking_id": b.id, "email": "invite@x.invalid"})
        self.assertEqual(b.guest_state, "pending")
        self.assertFalse(
            self.env["res.partner"].search([("email", "=ilike", "invite@x.invalid")]),
            "un contact a été créé avant confirmation")
        self.assertFalse(b.guest_ids.partner_id)
        self.assertNotIn(
            "invite@x.invalid", (b.meeting_id.partner_ids.mapped("email") or []),
            "un invité non confirmé est déjà dans l'événement")

    def test_confirmation_creates_contact_and_attendee(self):
        b = self._booking()
        g = self.Guest.create({"booking_id": b.id, "email": "invite2@x.invalid"})
        g._bf_confirm()
        self.assertEqual(g.state, "confirmed")
        self.assertTrue(g.partner_id, "aucun contact créé à la confirmation")
        self.assertIn(g.partner_id, b.partner_ids)
        self.assertIn(g.partner_id, b.meeting_id.partner_ids)
        self.assertTrue(g.confirmed_at)

    def test_decline_sends_nothing_and_creates_nothing(self):
        b = self._booking()
        g = self.Guest.create({"booking_id": b.id, "email": "invite3@x.invalid"})
        g._bf_decline()
        self.assertEqual(g.state, "declined")
        self.assertFalse(g.partner_id)
        self.assertFalse(
            self.env["res.partner"].search([("email", "=ilike", "invite3@x.invalid")]))
        self.assertEqual(b.guest_state, "declined")

    def test_confirming_twice_changes_nothing(self):
        b = self._booking()
        g = self.Guest.create({"booking_id": b.id, "email": "invite4@x.invalid"})
        g._bf_confirm()
        premier = g.confirmed_at
        g._bf_confirm()
        self.assertEqual(g.confirmed_at, premier)

    def test_existing_contact_is_reused(self):
        existant = self.env["res.partner"].create({
            "name": "Déjà connu", "email": "connu@x.invalid"})
        b = self._booking()
        g = self.Guest.create({"booking_id": b.id, "email": "connu@x.invalid"})
        g._bf_confirm()
        self.assertEqual(g.partner_id, existant, "un doublon de contact a été créé")

    def test_same_email_twice_on_one_booking_is_refused(self):
        from psycopg2 import IntegrityError
        from odoo.tools import mute_logger

        b = self._booking()
        self.Guest.create({"booking_id": b.id, "email": "double@x.invalid"})
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.cr.savepoint():
                self.Guest.create({"booking_id": b.id, "email": "double@x.invalid"})

    # -- Confidentialité des réponses du formulaire --------------------------

    def test_intake_answers_are_hidden_from_guests(self):
        """🔴 La description part dans l'invitation reçue par TOUS. Ce que le
        demandeur a écrit ne regarde pas forcément les gens qu'il convie."""
        b = self._booking()
        champ = self.env["appointment.intake.field"].create({
            "type_id": self.booking_type.id, "name": "De quoi s'agit-il ?"})
        self.env["appointment.intake.answer"].create({
            "booking_id": b.id, "field_id": champ.id,
            "value": "Litige confidentiel avec un fournisseur"})
        self.assertIn("Litige confidentiel", b._bf_meeting_description(),
                      "sans invité, les réponses doivent rester")
        g = self.Guest.create({"booking_id": b.id, "email": "curieux@x.invalid"})
        g._bf_confirm()
        self.assertNotIn("Litige confidentiel", b._bf_meeting_description(),
                         "les réponses ont fuité vers un invité")

    def test_intake_answers_shared_when_the_type_says_so(self):
        self.booking_type.guests_see_intake = True
        b = self._booking()
        champ = self.env["appointment.intake.field"].create({
            "type_id": self.booking_type.id, "name": "Sujet"})
        self.env["appointment.intake.answer"].create({
            "booking_id": b.id, "field_id": champ.id, "value": "Renouvellement"})
        self.Guest.create({"booking_id": b.id, "email": "ok@x.invalid"})._bf_confirm()
        self.assertIn("Renouvellement", b._bf_meeting_description())

    # -- Le GET ne décide rien -----------------------------------------------

    def test_guest_route_accepts_get_but_only_post_acts(self):
        """🔴 Un GET qui confirmerait serait déclenché par les antivirus de
        messagerie, produisant exactement le pourriel qu'on veut empêcher."""
        from odoo.addons.bf_appointment.controllers.main import AppointmentController

        route = AppointmentController.appointment_guests.original_routing
        self.assertIn("GET", route["methods"], "le lien du courriel se clique en GET")
        self.assertIn("POST", route["methods"], "l'action doit rester possible")

    def test_templates_exist(self):
        for xmlid in ("bf_appointment.mail_template_guest_invitation",
                      "bf_appointment.mail_template_guest_confirmation_request",
                      "bf_appointment.appointment_guests_confirm"):
            self.assertTrue(self.env.ref(xmlid, raise_if_not_found=False), xmlid)
