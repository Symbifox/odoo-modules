# -*- coding: utf-8 -*-
from datetime import timedelta
from unittest.mock import patch

import psycopg2.errors

from odoo import fields
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger


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

    def test_booking_by_someone_without_outreach_rights(self):
        """🔴 Booking without the Outreach groups: the booking goes through, the touch is logged.

        The target search ran with the booker's rights, outside the ``try``: their
        AccessError refused the booking itself (18.0.1.1.0).
        """
        booker = self.env["res.users"].create({
            "name": "Booker without outreach", "login": "booker.outreach@example.test",
            # Booking manager, as whoever books from the back office: a plain booking
            # user may only create the bookings they attend (OCA rule).
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("resource_booking.group_manager").id])],
        })
        self.assertFalse(booker.has_group("bf_outreach.group_bf_outreach_user"))
        booking = self.env["resource.booking"].with_user(booker).create({
            "type_id": self.booking_type.id,
            "partner_ids": [(6, 0, self.partner.ids)],
            "start": self.now + timedelta(days=3),
            "duration": 0.5,
        })
        self.assertIn(booking.state, ("scheduled", "confirmed"))
        self.assertEqual(self.target.touch_ids.booking_id, booking)
        self.assertEqual(self.target.stage_id, self.stage_meeting)

    def test_booking_never_logs_into_another_company(self):
        """The sync runs as the system, so the company rule is put back in the domain."""
        other = self.env["res.company"].create({"name": "Other outreach company"})
        self.campaign.company_id = other
        self.booking_type.company_id = self.env.company
        self._book()
        self.assertEqual(self.target.company_id, other)
        self.assertEqual(self.target.touch_count, 0, "a booking of A logged into B's outreach")
        self.assertNotEqual(self.target.stage_id, self.stage_meeting)

    def test_a_sql_error_in_the_sync_does_not_break_the_booking(self):
        """A swallowed SQL error without a savepoint left the transaction aborted."""
        def broken(records, *args, **kwargs):
            records.env.cr.execute("SELECT 1/0")
        Touch = type(self.env["bf.outreach.touch"])
        with patch.object(Touch, "create", broken), mute_logger(
                "odoo.addons.bf_outreach_appointment.models.resource_booking", "odoo.sql_db"):
            booking = self._book()
        self.env.cr.execute("SELECT 1")
        self.assertIn(booking.state, ("scheduled", "confirmed"))
        self.assertEqual(self.target.touch_count, 0)

    def test_the_caller_error_is_not_swallowed(self):
        """An invalid write left pending by the caller surfaces as itself.

        The first savepoint flushes everything on entry: without the flush before
        the ``try``, the caller's error was logged as "target search impossible"
        and the request died later on an opaque aborted transaction.
        """
        booking = self._book()
        ontario = self.env.ref("base.state_ca_on")
        self.env.flush_all()
        # Inside the block: assertRaises opens a savepoint, which flushes on entry.
        with self.assertRaises(psycopg2.errors.UniqueViolation), mute_logger("odoo.sql_db"):
            ontario.write({"code": "QC"})  # UNIQUE(country_id, code), raised at flush
            booking.write({"duration": 1.0})
