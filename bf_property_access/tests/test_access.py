from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from odoo.tests.common import new_test_user  # noqa: F401

from .common import AccessCase


@tagged("post_install", "-at_install")
class TestAccess(AccessCase):

    # ── Arrivée ──────────────────────────────────────────────────────────

    def test_owner_who_occupies_becomes_resident_with_orders_to_concierge(self):
        point = self.make_point()
        self.own(self.alice, self.unit1)
        self.sync()
        res = self.resident(self.alice, self.unit1)
        self.assertEqual(res.state, "active")
        self.assertEqual(res.origin, "owner")
        grant = res.grant_ids
        self.assertEqual(grant.point_id, point)
        self.assertEqual(grant.state, "to_issue")
        order = grant.order_ids
        self.assertEqual((order.action, order.state), ("add", "waiting"))
        act = order.activity_ids
        self.assertEqual(act.user_id, self.concierge)
        self.assertIn("Alice Exemple", act.summary)

    def test_owner_of_a_rented_unit_is_not_a_resident_but_the_occupant_is(self):
        self.make_point()
        self.own(self.alice, self.unit1)
        self.unit1.write({"is_rented": True, "occupant_id": self.bruno.id})
        self.sync()
        self.assertFalse(self.resident(self.alice, self.unit1))
        occ = self.resident(self.bruno, self.unit1)
        self.assertEqual((occ.state, occ.origin), ("active", "occupant"))

    def test_future_owner_is_upcoming_without_grants_until_arrival(self):
        self.make_point()
        self.own(self.alice, self.unit1, start=self.days(10))
        self.sync()
        res = self.resident(self.alice, self.unit1)
        self.assertEqual(res.state, "upcoming")
        self.assertFalse(res.grant_ids)
        res.with_user(self.manager).action_arrive_now()
        self.assertEqual(res.state, "active")
        self.assertEqual(res.date_arrival, self.today)
        self.assertEqual(res.grant_ids.state, "to_issue")

    def test_point_added_later_serves_residents_already_there(self):
        self.own(self.alice, self.unit1)
        self.sync()
        res = self.resident(self.alice, self.unit1)
        self.assertFalse(res.grant_ids)
        garage = self.make_point(name="Garage", point_type="garage")
        self.assertEqual(res.grant_ids.point_id, garage)

    def test_unit_scoped_point_only_serves_that_unit(self):
        self.own(self.alice, self.unit1)
        self.own(self.bruno, self.unit2)
        self.sync()
        locker = self.make_point(name="Casier 101", point_type="parcel",
                                 unit_id=self.unit1.id)
        self.assertEqual(self.resident(self.alice, self.unit1).grant_ids.point_id, locker)
        self.assertFalse(self.resident(self.bruno, self.unit2).grant_ids)

    def test_sync_twice_creates_nothing_more(self):
        self.make_point()
        self.own(self.alice, self.unit1)
        self.sync()
        self.sync()
        res = self.Resident.search([("partner_id", "=", self.alice.id)])
        self.assertEqual(len(res), 1)
        self.assertEqual(len(res.grant_ids), 1)
        self.assertEqual(len(res.grant_ids.order_ids), 1)

    # ── Porte du logement (art. 1934) ────────────────────────────────────

    def test_unit_door_waits_for_both_consents(self):
        self.own(self.alice, self.unit1)
        self.sync()
        door = self.make_point(name="Porte 101", point_type="unit_door",
                               unit_id=self.unit1.id, credential_kind="pin")
        grant = self.resident(self.alice, self.unit1).grant_ids
        self.assertEqual(grant.point_id, door)
        self.assertEqual(grant.state, "awaiting_consent")
        self.assertFalse(grant.order_ids)
        grant.with_user(self.manager).action_consent_landlord()
        self.assertEqual(grant.state, "awaiting_consent")
        self.assertFalse(grant.order_ids)
        grant.with_user(self.manager).action_consent_tenant()
        self.assertEqual(grant.state, "to_issue")
        self.assertEqual(grant.order_ids.action, "add")
        self.assertEqual(grant.consent_tenant_user_id, self.manager)

    def test_unit_door_requires_its_unit(self):
        with self.assertRaises(ValidationError):
            self.make_point(name="Porte", point_type="unit_door")

    def test_concierge_cannot_record_consent(self):
        self.own(self.alice, self.unit1)
        self.sync()
        self.make_point(name="Porte 101", point_type="unit_door", unit_id=self.unit1.id)
        grant = self.resident(self.alice, self.unit1).grant_ids
        with self.assertRaises(AccessError):
            grant.with_user(self.concierge).action_consent_tenant()

    # ── Départ : jamais deviné ───────────────────────────────────────────

    def _issued(self, partner, unit):
        res = self.resident(partner, unit)
        res.grant_ids.order_ids.with_user(self.concierge).action_done()
        self.assertEqual(set(res.grant_ids.mapped("state")), {"issued"})
        return res

    def test_ownership_ending_flags_departure_and_removes_nothing(self):
        self.make_point()
        own = self.own(self.alice, self.unit1)
        self.sync()
        res = self._issued(self.alice, self.unit1)
        own.date_end = self.days(-1)
        self.sync()
        self.assertEqual(res.state, "departure_pending")
        self.assertTrue(res.departure_signal)
        self.assertEqual(res.grant_ids.state, "issued")
        self.assertFalse(res.grant_ids.order_ids.filtered(lambda o: o.action == "remove"))
        act = res.activity_ids
        self.assertEqual(act.user_id, self.manager)

    def test_confirmed_departure_removes_access_and_spares_other_occupants(self):
        self.make_point()
        own = self.own(self.alice, self.unit1)
        self.sync()
        alice = self._issued(self.alice, self.unit1)
        # Le conjoint n'a rien signé : déclaré à la main (art. 1938).
        bruno = self.Resident.with_user(self.manager).create({
            "partner_id": self.bruno.id, "building_id": self.building.id,
            "unit_id": self.unit1.id,
        })
        self.assertEqual((bruno.state, bruno.origin), ("active", "declared"))
        own.date_end = self.days(-1)
        self.sync()
        wiz = self.env["bf.property.access.departure"].with_user(self.manager).create(
            {"resident_ids": [(6, 0, alice.ids)], "date_keys": self.today})
        wiz.action_confirm()
        self.assertEqual(alice.state, "left")
        self.assertEqual(alice.date_departure, self.today)
        self.assertEqual(alice.grant_ids.state, "to_remove")
        remove = alice.grant_ids.order_ids.filtered(lambda o: o.action == "remove")
        self.assertEqual(remove.state, "waiting")
        self.assertFalse(alice.activity_ids)
        self.assertEqual(bruno.state, "active")
        self.assertEqual(bruno.grant_ids.state, "to_issue")
        remove.with_user(self.concierge).action_done()
        self.assertEqual(alice.grant_ids.state, "removed")
        self.sync()
        self.assertEqual(bruno.state, "active", "un occupant déclaré n'est jamais signalé")

    def test_departure_date_cannot_be_in_the_future(self):
        self.make_point()
        self.own(self.alice, self.unit1)
        self.sync()
        res = self.resident(self.alice, self.unit1)
        with self.assertRaises(UserError):
            res.with_user(self.manager)._bf_confirm_departure(self.days(1))

    def test_concierge_cannot_confirm_departure(self):
        self.own(self.alice, self.unit1)
        self.sync()
        res = self.resident(self.alice, self.unit1)
        with self.assertRaises(AccessError):
            self.env["bf.property.access.departure"].with_user(self.concierge).create(
                {"resident_ids": [(6, 0, res.ids)]}).action_confirm()

    def test_dismissed_departure_is_not_flagged_again(self):
        """Le copropriétaire a vendu mais reste comme locataire du nouveau
        propriétaire : la personne le confirme, le cron ne revient pas."""
        self.make_point()
        own = self.own(self.alice, self.unit1)
        self.sync()
        res = self.resident(self.alice, self.unit1)
        own.date_end = self.days(-1)
        self.sync()
        res.with_user(self.manager).action_dismiss_departure()
        self.assertEqual((res.state, res.origin), ("active", "declared"))
        self.assertFalse(res.activity_ids)
        self.sync()
        self.assertEqual(res.state, "active")

    def test_signal_disappears_when_the_record_is_corrected(self):
        self.make_point()
        own = self.own(self.alice, self.unit1)
        self.sync()
        res = self.resident(self.alice, self.unit1)
        own.date_end = self.days(-1)
        self.sync()
        self.assertEqual(res.state, "departure_pending")
        own.date_end = False
        self.sync()
        self.assertEqual(res.state, "active")
        self.assertFalse(res.departure_signal)
        self.assertFalse(res.activity_ids)

    def test_upcoming_whose_source_vanishes_is_cancelled_not_flagged(self):
        own = self.own(self.alice, self.unit1, start=self.days(10))
        self.sync()
        res = self.resident(self.alice, self.unit1)
        own.unlink()
        self.sync()
        self.assertEqual(res.state, "cancelled")

    def test_state_cannot_be_written_directly(self):
        self.own(self.alice, self.unit1)
        self.sync()
        res = self.resident(self.alice, self.unit1)
        with self.assertRaises(UserError):
            res.with_user(self.manager).write({"state": "left"})

    def test_revoking_before_the_concierge_ticks_still_orders_a_removal(self):
        """L'ajout remis au concierge a pu être fait sans être coché."""
        self.make_point()
        self.own(self.alice, self.unit1)
        self.sync()
        res = self.resident(self.alice, self.unit1)
        add = res.grant_ids.order_ids
        self.assertEqual(add.state, "waiting")
        res.with_user(self.manager)._bf_confirm_departure(self.today)
        self.assertEqual(add.state, "cancelled")
        self.assertFalse(add.activity_ids)
        remove = res.grant_ids.order_ids - add
        self.assertEqual((remove.action, remove.state), ("remove", "waiting"))

    # ── Ordres et activités ──────────────────────────────────────────────

    def test_marking_the_activity_done_marks_the_order_done(self):
        self.make_point()
        self.own(self.alice, self.unit1)
        self.sync()
        order = self.resident(self.alice, self.unit1).grant_ids.order_ids
        order.activity_ids.with_user(self.concierge).action_feedback(feedback="Fait au portier")
        self.assertEqual(order.state, "done")
        self.assertEqual(order.done_by_id, self.concierge)
        self.assertEqual(order.grant_id.state, "issued")

    def test_only_the_point_responsible_or_a_manager_marks_done(self):
        other = self.env["res.users"].create({
            "name": "Autre (banc)", "login": "acces_autre",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("bf_property_core.group_bf_property_user").id])], "tz": "America/Toronto",
        })
        self.make_point()
        self.own(self.alice, self.unit1)
        self.sync()
        order = self.resident(self.alice, self.unit1).grant_ids.order_ids
        with self.assertRaises(AccessError):
            order.with_user(other).action_done()
        order.with_user(self.manager).action_done()
        self.assertEqual(order.done_by_id, self.manager)

    def test_point_responsible_must_be_able_to_read(self):
        outsider = self.env["res.users"].create({
            "name": "Sans accès (banc)", "login": "acces_sans",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])], "tz": "America/Toronto",
        })
        with self.assertRaises(ValidationError):
            self.make_point(user_id=outsider.id)

    def test_lost_credential_is_removed_alone_and_reissued(self):
        self.make_point(credential_kind="card")
        self.make_point(name="Garage", point_type="garage", credential_kind="card")
        self.own(self.alice, self.unit1)
        self.sync()
        res = self._issued(self.alice, self.unit1)
        lost = res.grant_ids.filtered(lambda g: g.point_id.name == "Garage")
        kept = res.grant_ids - lost
        lost.with_user(self.manager).action_report_lost()
        self.assertEqual((lost.state, lost.end_reason), ("to_remove", "lost"))
        self.assertEqual(kept.state, "issued")
        fresh = res.grant_ids - lost - kept
        self.assertEqual((fresh.point_id, fresh.state), (lost.point_id, "to_issue"))

    def test_connector_retries_then_hands_over_to_the_responsible(self):
        Connector = self.env["bf.property.access.connector"]
        conn = Connector.create({"name": "Appareil (banc)", "max_attempts": 2})
        self.env.cr.execute("UPDATE bf_property_access_connector SET kind = 'banc' WHERE id = %s",
                            [conn.id])
        conn.invalidate_recordset(["kind"])
        calls = []

        def push(self_, order):
            calls.append(order.id)
            return False, "appareil injoignable", None

        with patch.object(type(Connector), "_push_banc", push, create=True):
            self.make_point(connector_id=conn.id)
            self.own(self.alice, self.unit1)
            self.sync()
            order = self.resident(self.alice, self.unit1).grant_ids.order_ids
            self.assertEqual((order.state, order.attempts), ("pending", 1))
            self.assertFalse(order.activity_ids)
            self.env["bf.property.access.order"]._cron_send()
        self.assertEqual(len(calls), 2)
        self.assertEqual(order.state, "waiting")
        self.assertIn("injoignable", order.last_message)
        self.assertEqual(order.activity_ids.user_id, self.concierge)

    def test_connector_success_records_the_reference_never_a_code(self):
        Connector = self.env["bf.property.access.connector"]
        conn = Connector.create({"name": "Appareil (banc)"})
        self.env.cr.execute("UPDATE bf_property_access_connector SET kind = 'banc' WHERE id = %s",
                            [conn.id])
        conn.invalidate_recordset(["kind"])
        with patch.object(type(Connector), "_push_banc",
                          lambda self_, order: (True, "posé", "CARTE-0042"), create=True):
            self.make_point(connector_id=conn.id, credential_kind="card")
            self.own(self.alice, self.unit1)
            self.sync()
        grant = self.resident(self.alice, self.unit1).grant_ids
        self.assertEqual((grant.state, grant.external_ref), ("issued", "CARTE-0042"))
        self.assertEqual(grant.order_ids.state, "done")

    def test_connector_exception_does_not_break_the_sync(self):
        Connector = self.env["bf.property.access.connector"]
        conn = Connector.create({"name": "Appareil (banc)", "max_attempts": 1})
        self.env.cr.execute("UPDATE bf_property_access_connector SET kind = 'banc' WHERE id = %s",
                            [conn.id])
        conn.invalidate_recordset(["kind"])

        def boom(self_, order):
            raise ConnectionError("délai dépassé")

        with patch.object(type(Connector), "_push_banc", boom, create=True):
            self.make_point(connector_id=conn.id)
            self.own(self.alice, self.unit1)
            self.sync()
        order = self.resident(self.alice, self.unit1).grant_ids.order_ids
        self.assertEqual(order.state, "waiting")
        self.assertIn("délai dépassé", order.last_message)

    # ── Purge (Loi 25) et cloisonnement ──────────────────────────────────

    def test_purge_deletes_old_departures_and_keeps_recent_ones(self):
        self.make_point()
        self.org.access_log_retention_days = 30
        self.own(self.alice, self.unit1)
        self.own(self.bruno, self.unit2)
        self.sync()
        old = self.resident(self.alice, self.unit1)
        recent = self.resident(self.bruno, self.unit2)
        old.with_user(self.manager)._bf_confirm_departure(self.days(-40))
        recent.with_user(self.manager)._bf_confirm_departure(self.days(-5))
        # Les retraits faits : sinon la purge les garde.
        (old | recent).grant_ids.order_ids.filtered(
            lambda o: o.state == "waiting").with_user(self.concierge).action_done()
        self.Resident._cron_purge()
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_purge_never_when_retention_is_zero(self):
        self.org.access_log_retention_days = 0
        self.own(self.alice, self.unit1)
        self.sync()
        res = self.resident(self.alice, self.unit1)
        res.with_user(self.manager)._bf_confirm_departure(self.days(-400))
        self.Resident._cron_purge()
        self.assertTrue(res.exists())

    def test_other_company_manager_sees_nothing(self):
        other_co = self.env["res.company"].create({"name": "Autre société (banc)"})
        other_mgr = new_user = self.env["res.users"].create({
            "name": "Gestion autre (banc)", "login": "acces_autre_co",
            "company_id": other_co.id, "company_ids": [(6, 0, other_co.ids)],
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("bf_property_core.group_bf_property_manager").id])], "tz": "America/Toronto",
        })
        self.assertTrue(new_user)
        self.make_point()
        self.own(self.alice, self.unit1)
        self.sync()
        Res = self.Resident.with_user(other_mgr).with_company(other_co)
        self.assertFalse(Res.search([]))
        self.assertFalse(self.env["bf.property.access.order"].with_user(other_mgr)
                         .with_company(other_co).search([]))

    def test_sync_button_needs_write_access(self):
        with self.assertRaises(AccessError):
            self.Resident.with_user(self.concierge).action_sync_now()
        self.own(self.alice, self.unit1)
        self.Resident.with_user(self.manager).action_sync_now()
        self.assertTrue(self.resident(self.alice, self.unit1))

    def test_resident_with_grants_cannot_change_person_or_unit(self):
        self.make_point()
        self.own(self.alice, self.unit1)
        self.sync()
        res = self.resident(self.alice, self.unit1)
        with self.assertRaises(UserError):
            res.with_user(self.manager).write({"unit_id": self.unit2.id})

    def test_every_access_view_opens_for_each_role(self):
        self.make_point()
        self.own(self.alice, self.unit1)
        self.sync()
        for user in (self.manager, self.concierge):
            for model in ("bf.property.access.point", "bf.property.access.resident",
                          "bf.property.access.grant", "bf.property.access.order",
                          "bf.property.access.connector"):
                Model = self.env[model].with_user(user)
                for view_type in ("list", "form"):
                    Model.get_views([(False, view_type)])
                recs = Model.search([])
                recs.read([f for f in Model._fields if not Model._fields[f].groups])
        self.env["bf.property.building"].with_user(self.manager).get_views([(False, "form")])
        self.env["bf.property.organisation"].with_user(self.manager).get_views([(False, "form")])
        self.assertEqual(fields.Date.context_today(self.Resident), self.today)


@tagged("post_install", "-at_install")
class TestReview1(AccessCase):
    """Constats de la relecture adverse 1 (2026-10-31) : un essai par constat."""

    def _order(self):
        self.make_point()
        self.own(self.alice, self.unit1)
        self.sync()
        return self.resident(self.alice, self.unit1).grant_ids.order_ids

    # R1-1 [B] activité détournée
    def test_r1_1_rewritten_activity_does_not_mark_an_order_done(self):
        nobody = self.env["res.users"].create({
            "name": "Interne (banc)", "login": "acces_interne",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])], "tz": "America/Toronto",
        })
        order = self._order()
        act = self.env["mail.activity"].create({
            "res_model_id": self.env.ref("base.model_res_partner").id,
            "res_id": self.bruno.id, "user_id": nobody.id,
            "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
        })
        act.with_user(nobody).write({
            "res_model_id": self.env.ref("bf_property_access.model_bf_property_access_order").id,
            "res_id": order.id,
            "activity_type_id": self.env.ref("bf_property_access.mail_activity_access_order").id,
        })
        act.with_user(nobody).action_feedback(feedback="détourné")
        self.assertEqual(order.state, "waiting")
        self.assertEqual(order.grant_id.state, "to_issue")

    def test_r1_1_concierge_cannot_redirect_to_another_points_order(self):
        other_concierge = new_user = self.env["res.users"].create({
            "name": "Concierge B (banc)", "login": "acces_conc_b",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("bf_property_core.group_bf_property_user").id])], "tz": "America/Toronto",
        })
        self.assertTrue(new_user)
        self.make_point(name="Garage", point_type="garage", user_id=other_concierge.id)
        order_a = self._order().filtered(lambda o: o.point_id.name == "Portier nord")
        order_b = self.resident(self.alice, self.unit1).grant_ids.order_ids - order_a
        act = order_a.hand_over_activity_id
        act.with_user(self.concierge).write({"res_id": order_b.id})
        act.with_user(self.concierge).action_feedback(feedback="détourné")
        self.assertEqual(order_b.state, "waiting")
        self.assertTrue(order_b.activity_ids, "l'ordre B garde son rappel")

    def test_r1_1_activity_closed_by_a_former_responsible_is_handed_over_again(self):
        """Le rappel suit le nouveau responsable."""
        order = self._order()
        act = order.hand_over_activity_id
        order.point_id.user_id = self.manager
        self.assertEqual(act.user_id, self.manager)
        act.with_user(self.manager).action_feedback(feedback="fait")
        self.assertEqual((order.state, order.done_by_id), ("done", self.manager))

    # R1-2 [B] départ confirmé défait par la synchro
    def test_r1_2_confirmed_departure_is_not_undone_by_the_file(self):
        self.make_point()
        self.own(self.alice, self.unit1)
        self.sync()
        alice = self.resident(self.alice, self.unit1)
        alice.with_user(self.manager)._bf_confirm_departure(self.today)
        self.sync()
        self.sync()
        rows = self.Resident.search([("partner_id", "=", self.alice.id)])
        self.assertEqual(rows.mapped("state"), ["left"])
        self.assertTrue(alice.stale_source_noted)
        self.assertEqual(len(alice.activity_ids), 1, "une seule activité, pas une par nuit")
        self.assertFalse(alice.grant_ids.order_ids.filtered(
            lambda o: o.action == "add" and o.state == "waiting"))

    def test_r1_2_a_new_arrival_after_the_departure_is_a_new_resident(self):
        self.make_point()
        own = self.own(self.alice, self.unit1, start=self.days(-400))
        self.sync()
        alice = self.resident(self.alice, self.unit1)
        alice.with_user(self.manager)._bf_confirm_departure(self.days(-10))
        own.date_start = self.days(-5)
        self.sync()
        rows = self.Resident.search([("partner_id", "=", self.alice.id)], order="id")
        self.assertEqual(rows.mapped("state"), ["left", "active"])

    # R1-3 [B] la purge n'emporte pas un retrait à faire
    def test_r1_3_purge_keeps_a_departure_whose_removal_is_not_done(self):
        self.make_point()
        self.org.access_log_retention_days = 30
        self.own(self.alice, self.unit1)
        self.sync()
        alice = self.resident(self.alice, self.unit1)
        alice.grant_ids.order_ids.with_user(self.concierge).action_done()
        alice.with_user(self.manager)._bf_confirm_departure(self.days(-120))
        remove = alice.grant_ids.order_ids.filtered(lambda o: o.action == "remove")
        self.Resident._cron_purge()
        self.assertTrue(alice.exists())
        self.assertTrue(remove.exists())
        self.assertEqual(remove.state, "waiting")
        remove.with_user(self.concierge).action_done()
        self.Resident._cron_purge()
        self.assertFalse(alice.exists())
        self.assertFalse(self.env["mail.activity"].search(
            [("res_model", "=", "bf.property.access.order"), ("res_id", "=", remove.id)]))

    # R1-4 [B] une donnée incohérente n'arrête pas le cron
    def test_r1_4_one_failing_building_does_not_stop_the_others(self):
        other = self.env["bf.property.building"].create(
            {"name": "Autre immeuble (banc)", "organisation_id": self.org.id})
        unit = self.env["bf.property.unit"].create({"name": "1", "building_id": other.id})
        self.own(self.alice, self.unit1)
        self.own(self.bruno, unit)
        Resident = type(self.Resident)
        real = Resident._bf_access_sources

        def flaky(self_, buildings=None):
            if buildings is not None and self.building in buildings:
                raise ValueError("donnée incohérente")
            return real(self_, buildings)

        with patch.object(Resident, "_bf_access_sources", flaky), \
                self.assertLogs("odoo.addons.bf_property_access.models.bf_property_access_resident",
                                level="ERROR"):
            self.Resident._cron_sync()
        self.assertFalse(self.resident(self.alice, self.unit1))
        self.assertTrue(self.Resident.search([("partner_id", "=", self.bruno.id)]))

    # R1-6 [I] gardes par contexte contournables par RPC
    def test_r1_6_context_key_does_not_unlock_protected_fields(self):
        order = self._order()
        res = order.resident_id
        ctx = {"bf_access_state_ok": True}
        with self.assertRaises(UserError):
            res.with_user(self.manager).with_context(**ctx).write({"state": "left"})
        with self.assertRaises(UserError):
            order.grant_id.with_user(self.manager).with_context(**ctx).write({"state": "removed"})
        with self.assertRaises(UserError):
            order.with_user(self.manager).with_context(**ctx).write({"action": "remove"})
        with self.assertRaises(UserError):
            order.with_user(self.manager).write({"done_by_id": self.concierge.id})
        with self.assertRaises(UserError):
            res.with_user(self.manager).write({"origin": "declared"})
        order.with_user(self.manager).write({"external_ref": "CARTE-7"})
        self.assertEqual(order.external_ref, "CARTE-7")

    def test_r1_6_create_cannot_choose_a_state_or_an_automatic_origin(self):
        res = self.Resident.with_user(self.manager).create({
            "partner_id": self.chloe.id, "building_id": self.building.id,
            "state": "left", "origin": "owner", "date_departure": self.today,
        })
        self.assertEqual((res.state, res.origin), ("active", "declared"))
        self.assertFalse(res.date_departure)

    # R1-7 [I] point dont la portée change ou archivé
    def test_r1_7_narrowed_point_revokes_the_residents_it_no_longer_serves(self):
        point = self.make_point()
        self.own(self.alice, self.unit1)
        self.own(self.bruno, self.unit2)
        self.sync()
        point.with_user(self.manager).unit_id = self.unit1
        bruno = self.resident(self.bruno, self.unit2)
        self.assertEqual(bruno.grant_ids.state, "to_remove")
        self.assertEqual(self.resident(self.alice, self.unit1).grant_ids.state, "to_issue")

    def test_r1_7_archived_point_revokes_and_cannot_be_deleted_meanwhile(self):
        point = self.make_point()
        self.own(self.alice, self.unit1)
        self.sync()
        with self.assertRaises(UserError):
            point.with_user(self.manager).unlink()
        point.with_user(self.manager).active = False
        grant = self.resident(self.alice, self.unit1).grant_ids
        self.assertEqual(grant.state, "to_remove")
        self.assertEqual(grant.order_ids.filtered(lambda o: o.action == "add").state, "cancelled")

    # R1-8 [I] départ à confirmer remis à quelqu'un qui peut confirmer
    def test_r1_8_access_manager_must_be_a_manager_and_concierge_is_not_a_fallback(self):
        with self.assertRaises(ValidationError):
            self.building.access_user_id = self.concierge
        self.building.access_user_id = False
        self.make_point()
        own = self.own(self.alice, self.unit1)
        self.sync()
        own.date_end = self.days(-1)
        self.sync()
        res = self.resident(self.alice, self.unit1)
        self.assertEqual(res.state, "departure_pending")
        self.assertFalse(res.activity_ids.filtered(lambda a: a.user_id == self.concierge))

    # R1-11 [m] arrivée déclarée annulable
    def test_r1_11_upcoming_declared_arrival_can_be_cancelled(self):
        res = self.Resident.with_user(self.manager).create({
            "partner_id": self.chloe.id, "building_id": self.building.id,
            "date_arrival": self.days(5),
        })
        self.assertEqual(res.state, "upcoming")
        with self.assertRaises(AccessError):
            res.with_user(self.concierge).action_cancel_arrival()
        res.with_user(self.manager).action_cancel_arrival()
        self.assertEqual(res.state, "cancelled")


@tagged("post_install", "-at_install")
class TestReview2(AccessCase):
    """Constats de la relecture adverse 2 (2026-10-31) : un essai par constat."""

    def _tomorrow(self):
        return patch.object(fields.Date, "context_today",
                            lambda *a, **k: self.days(1))

    # R2-1 [B] source sans date réelle : départ confirmé défait le lendemain
    def test_r2_1_registered_occupant_stays_gone_the_next_day(self):
        self.make_point()
        self.unit1.write({"is_rented": True, "occupant_id": self.bruno.id})
        self.sync()
        res = self.resident(self.bruno, self.unit1)
        res.with_user(self.manager)._bf_confirm_departure(self.today)
        with self._tomorrow():
            self.sync()
        rows = self.Resident.search([("partner_id", "=", self.bruno.id)])
        self.assertEqual(rows.mapped("state"), ["left"])
        self.assertTrue(res.stale_source_noted)

    def test_r2_1_owner_without_purchase_date_stays_gone_the_next_day(self):
        self.make_point()
        own = self.own(self.alice, self.unit1)
        own.date_start = False
        self.sync()
        res = self.resident(self.alice, self.unit1)
        res.with_user(self.manager)._bf_confirm_departure(self.today)
        with self._tomorrow():
            self.sync()
        self.assertEqual(self.Resident.search(
            [("partner_id", "=", self.alice.id)]).mapped("state"), ["left"])

    # R2-2 [B] la purge n'efface pas la garde
    def test_r2_2_purge_keeps_the_guard_while_the_file_still_names_the_person(self):
        self.make_point()
        self.org.access_log_retention_days = 30
        own = self.own(self.alice, self.unit1)
        self.sync()
        res = self.resident(self.alice, self.unit1)
        res.with_user(self.manager)._bf_confirm_departure(self.days(-60))
        res.grant_ids.order_ids.filtered(lambda o: o.state == "waiting").with_user(
            self.concierge).action_done()
        self.sync()
        self.assertTrue(res.stale_source_noted)
        self.Resident._cron_purge()
        self.assertTrue(res.exists(), "la garde reste tant que le dossier nomme la personne")
        self.sync()
        self.assertEqual(self.Resident.search(
            [("partner_id", "=", self.alice.id)]).mapped("state"), ["left"])
        own.date_end = self.days(-1)
        self.Resident._cron_purge()
        self.assertFalse(res.exists(), "dossier corrigé : la purge reprend")

    # R2-4 [m] annuler l'arrivée d'un résident tiré du dossier
    def test_r2_4_cannot_cancel_the_arrival_of_a_resident_from_the_file(self):
        self.own(self.alice, self.unit1, start=self.days(10))
        self.sync()
        res = self.resident(self.alice, self.unit1)
        with self.assertRaises(UserError):
            res.with_user(self.manager).action_cancel_arrival()

    # R2-5 [m] rappel supprimé au lieu d'être fait
    def test_r2_5_deleted_reminder_is_handed_over_again_at_night(self):
        self.make_point()
        self.own(self.alice, self.unit1)
        self.sync()
        order = self.resident(self.alice, self.unit1).grant_ids.order_ids
        order.hand_over_activity_id.with_user(self.concierge).unlink()
        self.assertFalse(order.hand_over_activity_id)
        self.env["bf.property.access.order"]._cron_send()
        self.assertEqual(order.hand_over_activity_id.user_id, self.concierge)
        self.assertEqual(order.state, "waiting")

    # R2-7 [m] retrait et ajout ouverts ensemble
    def test_r2_7_new_addition_warns_about_an_open_removal(self):
        point = self.make_point()
        self.own(self.alice, self.unit1)
        self.sync()
        res = self.resident(self.alice, self.unit1)
        res.grant_ids.order_ids.with_user(self.concierge).action_done()
        point.with_user(self.manager).active = False
        point.with_user(self.manager).active = True
        add = res.grant_ids.order_ids.filtered(
            lambda o: o.action == "add" and o.state == "waiting")
        self.assertIn("AVANT", add.last_message)

    # R2-8 [m] support d'un droit émis
    def test_r2_8_credential_of_an_issued_grant_cannot_change(self):
        self.make_point()
        self.own(self.alice, self.unit1)
        self.sync()
        grant = self.resident(self.alice, self.unit1).grant_ids
        grant.with_user(self.manager).credential_kind = "card"
        grant.order_ids.with_user(self.concierge).action_done()
        with self.assertRaises(UserError):
            grant.with_user(self.manager).credential_kind = "pin"

    # R2-9 [m] journal d'un ordre clos
    def test_r2_9_closed_order_reference_cannot_be_rewritten(self):
        self.make_point()
        self.own(self.alice, self.unit1)
        self.sync()
        order = self.resident(self.alice, self.unit1).grant_ids.order_ids
        order.with_user(self.manager).external_ref = "CARTE-1"
        order.with_user(self.concierge).action_done()
        with self.assertRaises(UserError):
            order.with_user(self.manager).external_ref = "CARTE-2"

    # Pose sur la démo (18.0.1.0.1) : signal rédigé dans la langue de la société
    def test_signal_is_written_in_the_company_language(self):
        self.env.company.partner_id.lang = "en_US"
        self.make_point()
        own = self.own(self.alice, self.unit1)
        self.sync()
        own.date_end = self.days(-1)
        self.Resident.with_context(lang=None).sudo()._bf_sync(buildings=self.building)
        res = self.resident(self.alice, self.unit1)
        self.assertEqual(res.state, "departure_pending")
        self.assertEqual(res.with_context(lang="en_US").departure_signal, res.departure_signal)

    # R2-10 [m] doublon déclaré, consentements sans rappel
    def test_r2_10_declaring_an_existing_resident_is_a_clear_error(self):
        self.own(self.alice, self.unit1)
        self.sync()
        with self.assertRaises(UserError):
            self.Resident.with_user(self.manager).create({
                "partner_id": self.alice.id, "building_id": self.building.id,
                "unit_id": self.unit1.id})

    def test_r2_10_awaiting_consents_reminds_the_access_manager(self):
        self.own(self.alice, self.unit1)
        self.sync()
        self.make_point(name="Porte 101", point_type="unit_door", unit_id=self.unit1.id)
        grant = self.resident(self.alice, self.unit1).grant_ids
        self.assertEqual(grant.activity_ids.user_id, self.manager)
        grant.with_user(self.manager).action_consent_landlord()
        grant.with_user(self.manager).action_consent_tenant()
        self.assertFalse(grant.activity_ids)

    def test_r2_activity_reassigned_to_someone_else_does_not_count(self):
        """L'activité remise, réassignée à un tiers qui n'est ni gestionnaire ni
        responsable du point : la fermer ne fait pas l'ordre, le rappel revient."""
        other = self.env["res.users"].create({
            "name": "Tiers (banc)", "login": "acces_tiers",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("bf_property_core.group_bf_property_user").id])], "tz": "America/Toronto",
        })
        self.make_point()
        self.own(self.alice, self.unit1)
        self.sync()
        order = self.resident(self.alice, self.unit1).grant_ids.order_ids
        order.hand_over_activity_id.user_id = other
        order.hand_over_activity_id.with_user(other).action_feedback(feedback="pas à moi")
        self.assertEqual(order.state, "waiting")
        self.assertEqual(order.hand_over_activity_id.user_id, self.concierge)
