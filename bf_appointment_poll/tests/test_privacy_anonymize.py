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

    def test_poll_of_the_booking_is_deleted_and_registered(self):
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

        booking._bf_privacy_anonymize()

        self.assertFalse(poll.exists())
        self.assertTrue(other.exists(), "un sondage sans cette réservation reste")
        self.assertFalse(self.env["appointment.poll.participant"].search([
            ("email", "=", "zq@test.invalid")]))
        self.assertFalse(self.env["mail.message"].sudo().search([
            ("model", "=", "appointment.poll"), ("res_id", "=", poll.id)]))
        entry = self.env["privacy.destruction.register"].sudo().search([
            ("res_model", "=", "resource.booking"), ("res_id", "=", booking.id)])
        self.assertIn("sondage de disponibilités n° %s" % poll.id,
                      entry.document_description)
