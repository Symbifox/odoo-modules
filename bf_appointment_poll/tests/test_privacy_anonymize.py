# -*- coding: utf-8 -*-
"""Anonymisation d'une réservation (RDV-001) : le sondage qui l'a
fait naître est supprimé avec elle."""

from datetime import timedelta

import pytz

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


@tagged("bf_appointment_poll", "bf_appointment_privacy")
class TestPollPrivacy(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tz="UTC"))
        calendar = cls.env["resource.calendar"].create({
            "name": "24/7 sondage vie privée", "tz": "UTC",
            "attendance_ids": [Command.create({
                "name": "j%d" % d, "dayofweek": str(d), "hour_from": 0.0,
                "hour_to": 24.0, "day_period": "morning"}) for d in range(7)],
        })
        resource = cls.env["resource.resource"].create({
            "name": "Matériel sondage vie privée", "calendar_id": calendar.id,
            "resource_type": "material", "tz": "UTC"})
        combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([resource.id])]})
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Type sondage vie privée", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": calendar.id,
            "combination_rel_ids": [Command.create({
                "sequence": 0, "combination_id": combination.id})],
        })
        cls.client = cls.env["res.partner"].create({
            "name": "Zébulon Quatremare", "email": "zq@test.invalid"})

    def _booking_with_poll(self):
        """Une réservation née d'un sondage, et un autre sondage sans elle."""
        now = fields.Datetime.context_timestamp(
            self.env["resource.booking"], fields.Datetime.now())
        slot = self.booking_type._bf_candidate_slots(
            now + timedelta(hours=1), now + timedelta(days=7), limit=1)[0]
        booking = self.booking_type._bf_create_booking(
            slot.astimezone(pytz.utc).replace(tzinfo=None), partners=self.client)
        poll = self.env["appointment.poll"].create({
            "name": "Coordination", "type_id": self.booking_type.id, "max_slots": 2})
        self.env["appointment.poll.participant"].create({
            "poll_id": poll.id, "name": "Zébulon Quatremare", "email": "zq@test.invalid"})
        poll.message_post(body="Relance à Zébulon Quatremare")
        poll.write({"booking_id": booking.id})
        other = self.env["appointment.poll"].create({
            "name": "Sans rendez-vous", "type_id": self.booking_type.id, "max_slots": 2})
        return booking, poll, other

    def _assert_poll_deleted(self, booking, poll, other):
        self.assertFalse(poll.exists())
        self.assertTrue(other.exists(), "un sondage sans cette réservation reste")
        self.assertFalse(self.env["appointment.poll.participant"].search([
            ("email", "=", "zq@test.invalid")]))
        self.assertFalse(self.env["mail.message"].sudo().search([
            ("model", "=", "appointment.poll"), ("res_id", "=", poll.id)]))
        entry = self.env["privacy.destruction.register"].sudo().search([
            ("res_model", "=", "resource.booking"), ("res_id", "=", booking.id)])
        self.assertEqual(len(entry), 1)
        return entry

    def test_poll_of_the_booking_is_deleted_and_registered(self):
        # La note suit le français du registre quand fr_CA est active : l'essai
        # l'active lui-même, une base d'essai neuve n'a que en_US.
        self.addCleanup(self.env.registry.clear_cache)
        self.env["res.lang"]._activate_lang("fr_CA")
        booking, poll, other = self._booking_with_poll()

        booking._bf_privacy_anonymize()

        entry = self._assert_poll_deleted(booking, poll, other)
        self.assertIn("sondage de disponibilités n° %s" % poll.id,
                      entry.document_description)

    def test_poll_deleted_on_a_base_without_french(self):
        """Sans fr_CA active (un locataire n'a que en_US), rien ne lève.

        `with_context(lang="fr_CA")` faisait lever `env.lang` (« Invalid
        language code: fr_CA ») et l'anonymisation tombait, cron compris.
        fr_CA est éteinte ici quoi qu'aient fait les autres essais ou la base,
        et en SQL : l'ORM refuse d'éteindre une langue qu'un usager porte.
        """
        booking, poll, other = self._booking_with_poll()
        self.addCleanup(self.env.registry.clear_cache)
        self.env.cr.execute("UPDATE res_lang SET active = false WHERE code = 'fr_CA'")
        self.env["res.lang"].invalidate_model(["active"])
        self.env.registry.clear_cache()
        self.assertFalse(self.env["res.lang"]._get_data(code="fr_CA").code,
                         "fr_CA est encore active : l'essai ne prouverait rien")

        booking._bf_privacy_anonymize()

        entry = self._assert_poll_deleted(booking, poll, other)
        self.assertIn("availability poll no. %s" % poll.id,
                      entry.document_description)
