# -*- coding: utf-8 -*-
"""Anonymisation d'une réservation (RDV-001) : la copie que ce
satellite a faite sur l'ordre du jour part avec elle."""

from datetime import timedelta

import pytz

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged

NOM = "Zébulon Quatremare"


@tagged("bf_appointment_meeting", "bf_appointment_privacy")
class TestAgendaPrivacy(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tz="UTC"))
        calendar = cls.env["resource.calendar"].create({
            "name": "24/7 OdJ vie privée", "tz": "UTC",
            "attendance_ids": [Command.create({
                "name": "j%d" % d, "dayofweek": str(d), "hour_from": 0.0,
                "hour_to": 24.0, "day_period": "morning"}) for d in range(7)],
        })
        resource = cls.env["resource.resource"].create({
            "name": "Matériel OdJ vie privée", "calendar_id": calendar.id,
            "resource_type": "material", "tz": "UTC"})
        combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([resource.id])]})
        cls.client = cls.env["res.partner"].create({"name": NOM, "email": "zq@test.invalid"})
        cls.type = cls.env["resource.booking.type"].create({
            "name": "Type OdJ vie privée", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": calendar.id, "video_provider": "none",
            "requires_recording_consent": False, "bf_create_agenda": True,
            "project_id": cls._project().id,
            "combination_rel_ids": [Command.create({
                "sequence": 0, "combination_id": combination.id})],
        })
        cls.champ = cls.env["appointment.intake.field"].create({
            "type_id": cls.type.id, "name": "De quoi s'agit-il ?",
            "field_type": "textarea", "sequence": 10})

    @classmethod
    def _project(cls):
        # ⚠️ On reprend un projet existant : sur certaines bases, créer un projet
        # en essai bute sur `billing_type` (calcul stocké de sale_timesheet,
        # NOT NULL en base, absent de l'INSERT). Les autres essais du module
        # y tombent, et ce n'est pas l'objet de celui-ci.
        project = cls.env["project.project"].search([], limit=1)
        return project or cls.env["project.project"].create({"name": "Projet OdJ"})

    def _booking(self):
        now = fields.Datetime.context_timestamp(
            self.env["resource.booking"], fields.Datetime.now())
        slot = self.type._bf_candidate_slots(
            now + timedelta(hours=1), now + timedelta(days=7), limit=1)[0]
        booking = self.type._bf_create_booking(
            slot.astimezone(pytz.utc).replace(tzinfo=None),
            partners=self.client, confirm=False)
        self.env["appointment.intake.answer"].create({
            "booking_id": booking.id, "field_id": self.champ.id,
            "value": "Santé de %s" % NOM})
        booking.action_confirm()
        return booking

    def test_seeded_topic_and_requester_leave_with_the_booking(self):
        booking = self._booking()
        agenda = booking._bf_agenda()
        agenda.topic_ids.create({"agenda_id": agenda.id, "name": "Sujet de l'équipe"})
        signed = agenda.topic_ids.create({
            "agenda_id": agenda.id, "name": "Budget 2027", "source": "contributed",
            "contributor_name": NOM, "contributor_email": "zq@test.invalid"})
        booking._bf_privacy_anonymize()
        topics = agenda.topic_ids
        seeded = topics.filtered(lambda t: "RDV-001" in (t.name or ""))
        self.assertEqual(len(seeded), 1)
        self.assertFalse(seeded.contributor_name or seeded.contributor_email)
        self.assertFalse(seeded.description)
        self.assertFalse(any(NOM in (t.name or "") + str(t.description or "")
                             for t in topics))
        self.assertIn("Sujet de l'équipe", topics.mapped("name"),
                      "les autres sujets relèvent de RENC-001")
        self.assertFalse(agenda.bf_booking_partner_id)
        self.assertEqual(signed.name, "Budget 2027",
                         "une contribution du demandeur relève de la rencontre")
        self.assertFalse(signed.contributor_name or signed.contributor_email)
        entry = self.env["privacy.destruction.register"].sudo().search([
            ("res_model", "=", "resource.booking"), ("res_id", "=", booking.id)])
        self.assertIn("ordre du jour n° %s" % agenda.id, entry.document_description)
