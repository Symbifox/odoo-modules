# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_outreach")
class TestOutreachAppointmentBridge(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.now = fields.Datetime.now()
        cls.stage_meeting = cls.env.ref("bf_outreach.stage_meeting")
        cls.partner = cls.env["res.partner"].create(
            {"name": "Prospect rendez-vous inc.", "email": "rdv@example.com"}
        )
        cls.campaign = cls.env["bf.outreach.campaign"].create(
            {
                "name": "Campagne passerelle rendez-vous",
                "date_start": cls.now.date() - timedelta(days=5),
                "state": "running",
                "working_days_only": False,
            }
        )
        cls.target = cls.env["bf.outreach.target"].create(
            {
                "name": "Prospect rendez-vous inc.",
                "campaign_id": cls.campaign.id,
                "partner_id": cls.partner.id,
                "email": "rdv@example.com",
            }
        )
        # Une réservation ne peut être « planifiée » que si une combinaison de
        # ressources est disponible : on monte un horaire ouvert en permanence,
        # une ressource et sa combinaison.
        cls.calendar = cls.env["resource.calendar"].create(
            {
                "name": "Ouvert en tout temps",
                "tz": "UTC",
                "attendance_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Jour %s" % day,
                            "dayofweek": str(day),
                            "hour_from": 0.0,
                            "hour_to": 23.99,
                        },
                    )
                    for day in range(7)
                ],
            }
        )
        cls.resource = cls.env["resource.resource"].create(
            {
                "name": "Ressource de test",
                "calendar_id": cls.calendar.id,
                "resource_type": "material",
                "tz": "UTC",
            }
        )
        cls.combination = cls.env["resource.booking.combination"].create(
            {"resource_ids": [(6, 0, cls.resource.ids)]}
        )
        cls.booking_type = cls.env["resource.booking.type"].create(
            {
                "name": "Découverte 30 min",
                "duration": 0.5,
                "resource_calendar_id": cls.calendar.id,
                "combination_rel_ids": [
                    (0, 0, {"sequence": 1, "combination_id": cls.combination.id})
                ],
            }
        )

    def _book(self, start=None):
        return self.env["resource.booking"].create(
            {
                "type_id": self.booking_type.id,
                # partner_id est calculé à partir de partner_ids (avec inverse) :
                # on ne renseigne que la source.
                "partner_ids": [(6, 0, self.partner.ids)],
                "start": start or (self.now + timedelta(days=3)),
                "duration": 0.5,
            }
        )

    def test_booking_creates_a_meeting_touch_and_moves_the_stage(self):
        self.assertEqual(self.target.touch_count, 0)
        booking = self._book()
        self.assertIn(booking.state, ("scheduled", "confirmed"))
        self.assertEqual(self.target.touch_count, 1)
        touch = self.target.touch_ids
        self.assertEqual(touch.kind, "meeting")
        self.assertEqual(touch.booking_id, booking)
        self.assertAlmostEqual(touch.duration, 30.0, places=1)
        self.assertEqual(self.target.stage_id, self.stage_meeting)

    def test_same_booking_is_never_logged_twice(self):
        booking = self._book()
        self.assertEqual(self.target.touch_count, 1)
        # Une modification de la réservation ne doit pas dupliquer l'interaction.
        booking.write({"duration": 1.0})
        self.assertEqual(self.target.touch_count, 1)

    def test_booking_for_an_unrelated_partner_is_ignored(self):
        other = self.env["res.partner"].create({"name": "Personne d'autre"})
        self.env["resource.booking"].create(
            {
                "type_id": self.booking_type.id,
                "partner_ids": [(6, 0, other.ids)],
                "start": self.now + timedelta(days=4),
                "duration": 0.5,
            }
        )
        self.assertEqual(self.target.touch_count, 0)

    def test_closed_target_is_left_alone(self):
        self.target.stage_id = self.env.ref("bf_outreach.stage_won")
        self._book()
        self.assertEqual(self.target.touch_count, 0)

    def test_booking_url_comes_from_the_campaign(self):
        self.assertFalse(self.target.booking_url)
        self.booking_type.write({"is_public": True, "slug": "decouverte-test"})
        self.campaign.booking_type_id = self.booking_type
        self.target.invalidate_recordset()
        self.assertIn("decouverte-test", self.target.booking_url or "")

    def _langues(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"]._load_module_terms(
            ["bf_outreach_appointment"], ["fr_CA"], overwrite=True)
        internes = [(6, 0, [self.env.ref("base.group_user").id])]
        fr = self.env["res.users"].create({
            "name": "Owner fr_CA", "login": "owner.outreach@example.test", "lang": "fr_CA",
            "groups_id": internes,
        })
        en = self.env["res.users"].create({
            "name": "Organizer en_US", "login": "organizer.outreach@example.test",
            "lang": "en_US", "groups_id": internes,
        })
        return fr, en

    def _book_as(self, organizer=None):
        valeurs = {
            "type_id": self.booking_type.id,
            "partner_ids": [(6, 0, self.partner.ids)],
            "start": self.now + timedelta(days=4),
            "duration": 0.5,
        }
        if organizer:
            valeurs["user_id"] = organizer.id
        # Booked from an English context, as a visitor would.
        return self.env["resource.booking"].with_context(lang="en_US").create(valeurs)

    def test_booking_title_reads_in_the_target_owner_language(self):
        """The target owner reads the touch: their language wins over the organizer's."""
        fr, en = self._langues()
        self.target.user_id = fr
        self._book_as(organizer=en)
        self.assertEqual(self.target.touch_ids.summary, "Rendez-vous : Découverte 30 min")

    def test_booking_title_falls_back_to_the_organizer(self):
        """No owner on the target: the organizer reads it, never the superuser."""
        fr, en = self._langues()
        self.target.user_id = False
        self._book_as(organizer=fr)
        self.assertEqual(self.target.touch_ids.summary, "Rendez-vous : Découverte 30 min")
