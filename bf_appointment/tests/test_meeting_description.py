# -*- coding: utf-8 -*-
"""Description de l'événement d'agenda, et titre choisi à la main (2.47.0).

Deux défauts signalés en production, tous deux visibles sur un lien de
réservation personnel :

* l'agenda affichait le CONSEIL AU DEMANDEUR (« Décrivez brièvement le
  sujet… ») à la place du sujet. Un lien personnel ne passe par aucun
  formulaire d'accueil, donc le repli se déclenchait à tous les coups;
* le titre était toujours celui que la fabrique calcule, sans moyen de dire
  de quoi la rencontre parle.
"""

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("bf_appointment", "bf_appointment_description")
class TestMeetingDescription(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, tz="UTC"))
        attendances = [
            Command.create({
                "name": "j%s" % d, "dayofweek": str(d), "hour_from": 0.0,
                "hour_to": 24.0, "day_period": "morning",
            })
            for d in range(7)
        ]
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "24/7 desc", "attendance_ids": attendances, "tz": "UTC"})
        cls.resource = cls.env["resource.resource"].create({
            "name": "desc material", "calendar_id": cls.calendar.id,
            "resource_type": "material", "tz": "UTC"})
        cls.combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([cls.resource.id])]})
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Type desc", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id, "is_public": True,
            "listed_on_landing": False,
            "requester_advice": "Décrivez brièvement le sujet pour qu'on prépare.",
            "combination_rel_ids": [
                Command.create({"sequence": 0,
                                "combination_id": cls.combination.id})],
        })
        cls.client = cls.env["res.partner"].create({
            "name": "Client desc", "email": "desc@test.invalid"})
        cls.invite = cls.env["res.partner"].create({
            "name": "Invité desc", "email": "invite.desc@test.invalid"})

    def _booking(self, **vals):
        base = {
            "type_id": self.booking_type.id,
            "partner_ids": [Command.set([self.client.id])],
        }
        base.update(vals)
        return self.env["resource.booking"].create(base)

    # -- La consigne ne sort JAMAIS -----------------------------------------

    def test_no_answers_gives_an_empty_description(self):
        """🔴 Le cas du lien personnel : aucun formulaire, donc aucune réponse.

        L'OCA posait ``requester_advice``; le repli est retiré. Une
        description vide se lit mieux qu'une consigne adressée à quelqu'un
        d'autre, avant un moment déjà passé.
        """
        booking = self._booking()
        self.assertEqual(booking._bf_meeting_description(), "")

    def test_advice_never_leaks_even_with_confirmed_guests(self):
        """L'autre chemin de repli : un invité confirmé masque les réponses.

        Les masquer est voulu (ce que le demandeur écrit ne regarde pas ses
        invités), mais les remplacer par la consigne ne l'était pas.
        """
        booking = self._booking()
        champ = self.env["appointment.intake.field"].create({
            "type_id": self.booking_type.id, "name": "De quoi s'agit-il ?",
            "field_type": "text",
        })
        self.env["appointment.intake.answer"].create({
            "booking_id": booking.id, "field_id": champ.id,
            "value": "Le renouvellement du contrat",
        })
        self.env["resource.booking.guest"].create({
            "booking_id": booking.id, "email": "invite.desc@test.invalid",
            "state": "confirmed",
        })
        booking.invalidate_recordset()
        self.assertNotIn("Décrivez brièvement",
                         booking._bf_meeting_description() or "")

    def test_answers_are_still_rendered(self):
        """L'invariant : retirer le repli ne doit pas retirer les réponses."""
        booking = self._booking()
        champ = self.env["appointment.intake.field"].create({
            "type_id": self.booking_type.id, "name": "De quoi s'agit-il ?",
            "field_type": "text",
        })
        self.env["appointment.intake.answer"].create({
            "booking_id": booking.id, "field_id": champ.id,
            "value": "Le renouvellement du contrat",
        })
        booking.invalidate_recordset()
        rendu = booking._bf_meeting_description()
        self.assertIn("Le renouvellement du contrat", rendu)
        self.assertNotIn("Décrivez brièvement", rendu)

    def test_meeting_vals_carry_no_advice(self):
        """Le point de sortie réel : ce que reçoit calendar.event."""
        booking = self._booking()
        vals = booking._prepare_meeting_vals()
        self.assertNotIn("Décrivez brièvement", vals.get("description") or "")


@tagged("bf_appointment", "bf_appointment_onetime")
class TestOnetimeCustomTitle(TransactionCase):
    """Titre personnalisé à la création d'un lien de réservation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, tz="UTC"))
        attendances = [
            Command.create({
                "name": "j%s" % d, "dayofweek": str(d), "hour_from": 0.0,
                "hour_to": 24.0, "day_period": "morning",
            })
            for d in range(7)
        ]
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "24/7 titre", "attendance_ids": attendances, "tz": "UTC"})
        cls.resource = cls.env["resource.resource"].create({
            "name": "titre material", "calendar_id": cls.calendar.id,
            "resource_type": "material", "tz": "UTC"})
        cls.combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([cls.resource.id])]})
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Rencontre flexible", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id, "is_public": True,
            "listed_on_landing": False,
            "combination_rel_ids": [
                Command.create({"sequence": 0,
                                "combination_id": cls.combination.id})],
        })
        cls.destinataire = cls.env["res.partner"].create({
            "name": "Destinataire titre", "email": "titre@test.invalid"})
        cls.autre = cls.env["res.partner"].create({
            "name": "Autre destinataire", "email": "autre@test.invalid"})

    def _wizard(self, **vals):
        base = {"type_id": self.booking_type.id,
                "partner_id": self.destinataire.id}
        base.update(vals)
        return self.env["bf.appointment.onetime.wizard"].create(base)

    def test_custom_title_reaches_the_booking(self):
        w = self._wizard(custom_name="Renouvellement 2027")
        w.action_create_link()
        self.assertEqual(w.booking_id.name, "Renouvellement 2027")

    def test_blank_title_falls_back_to_the_computed_one(self):
        """Laisser le champ vide doit rendre exactement l'ancien comportement."""
        w = self._wizard(custom_name="   ")
        w.action_create_link()
        attendu = self.env["resource.booking"]._bf_build_title(
            self.booking_type, partner=self.destinataire,
            lang=self.destinataire.lang,
        )
        self.assertEqual(w.booking_id.name, attendu)

    def test_suggestion_prefills_and_follows_the_recipient(self):
        w = self._wizard()
        w._onchange_type_id()
        self.assertTrue(w.custom_name)
        premier = w.custom_name
        w.partner_id = self.autre
        w._onchange_partner_id()
        self.assertNotEqual(w.custom_name, premier,
                            "une suggestion intacte doit suivre le destinataire")

    def test_a_typed_title_survives_a_recipient_change(self):
        """🔴 Sans mémoire de la suggestion, changer de destinataire écraserait
        un titre saisi à la main."""
        w = self._wizard()
        w._onchange_type_id()
        w.custom_name = "Renouvellement 2027"
        w.partner_id = self.autre
        w._onchange_partner_id()
        self.assertEqual(w.custom_name, "Renouvellement 2027")

    def test_custom_title_reaches_the_calendar_event(self):
        """Le titre doit se rendre jusqu'à l'agenda : c'est là qu'on le lit."""
        from datetime import timedelta
        from odoo import fields

        w = self._wizard(custom_name="Renouvellement 2027")
        w.action_create_link()
        booking = w.booking_id
        booking.start = fields.Datetime.now() + timedelta(days=3)
        self.assertTrue(booking.meeting_id)
        self.assertEqual(booking.meeting_id.name, "Renouvellement 2027")
