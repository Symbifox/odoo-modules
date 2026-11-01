from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.bf_property_access.tests.common import AccessCase


def _has_rent_due_until(env):
    return "rent_due_until" in env["bf.rental.lease"]._fields


@tagged("post_install", "-at_install")
class TestRentalAccess(AccessCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.landlord = cls.env["bf.property.organisation"].create(
            {"name": "Locateur (banc)", "kind": "landlord"})
        cls.building.organisation_id = cls.landlord
        cls.Lease = cls.env["bf.rental.lease"]

    def lease(self, tenants, unit=None, start=None, end=None, **kw):
        vals = {
            "organisation_id": self.landlord.id,
            "building_id": self.building.id,
            "unit_id": unit.id if unit else False,
            "tenant_ids": [(6, 0, tenants.ids)],
            "duration_kind": "fixed",
            "date_start": start or self.days(-30),
            "date_end": end or self.days(335),
            "rent": 1000.0,
        }
        vals.update(kw)
        return self.Lease.create(vals)

    def test_signatories_become_residents_as_soon_as_the_lease_is_saved(self):
        self.make_point()
        lease = self.lease(self.alice | self.bruno, self.unit1)
        for partner in (self.alice, self.bruno):
            res = self.resident(partner, self.unit1)
            self.assertEqual((res.state, res.origin, res.lease_id), ("active", "lease", lease))
            self.assertEqual(res.grant_ids.state, "to_issue")

    def test_future_lease_makes_upcoming_residents_without_access(self):
        self.make_point()
        self.lease(self.alice, self.unit1, start=self.days(20), end=self.days(385))
        res = self.resident(self.alice, self.unit1)
        self.assertEqual(res.state, "upcoming")
        self.assertFalse(res.grant_ids)

    def test_a_past_end_date_is_not_a_departure(self):
        """🔴 Art. 1941 : le bail à durée fixe se reconduit de plein droit."""
        self.make_point()
        self.lease(self.alice, self.unit1, start=self.days(-400), end=self.days(-35))
        self.sync()
        res = self.resident(self.alice, self.unit1)
        self.assertEqual(res.state, "active")
        self.assertFalse(res.departure_signal)

    def test_rent_due_until_past_flags_departure_without_removing(self):
        if not _has_rent_due_until(self.env):
            self.skipTest("bf_rental_rent absent")
        self.make_point()
        lease = self.lease(self.alice, self.unit1, start=self.days(-400), end=self.days(-35))
        res = self.resident(self.alice, self.unit1)
        res.grant_ids.order_ids.with_user(self.concierge).action_done()
        lease.rent_due_until = self.days(-1)
        self.assertEqual(res.state, "departure_pending")
        self.assertIn("loyer n'est dû", res.departure_signal)
        self.assertEqual(res.grant_ids.state, "issued")
        self.assertFalse(res.grant_ids.order_ids.filtered(lambda o: o.action == "remove"))

    def test_rent_due_until_still_ahead_changes_nothing(self):
        if not _has_rent_due_until(self.env):
            self.skipTest("bf_rental_rent absent")
        self.make_point()
        lease = self.lease(self.alice, self.unit1)
        lease.rent_due_until = self.days(10)
        self.assertEqual(self.resident(self.alice, self.unit1).state, "active")

    def test_abandonment_flags_departure(self):
        self.make_point()
        lease = self.lease(self.alice, self.unit1)
        self.env["bf.rental.abandonment"].create({
            "lease_id": lease.id, "date_noticed": self.today, "kind": "deguerpissement",
        })
        res = self.resident(self.alice, self.unit1)
        self.assertEqual(res.state, "departure_pending")
        self.assertIn("préavis", res.departure_signal)

    def test_removing_one_cotenant_flags_only_that_one(self):
        self.make_point()
        lease = self.lease(self.alice | self.bruno, self.unit1)
        lease.tenant_ids = [(3, self.bruno.id)]
        self.assertEqual(self.resident(self.alice, self.unit1).state, "active")
        bruno = self.resident(self.bruno, self.unit1)
        self.assertEqual(bruno.state, "departure_pending")
        self.assertIn("signataire", bruno.departure_signal)

    def test_newer_lease_on_the_same_unit_flags_the_former_tenant(self):
        self.make_point()
        self.lease(self.alice, self.unit1, start=self.days(-400), end=self.days(-35))
        self.lease(self.bruno, self.unit1, start=self.days(-5))
        self.assertEqual(self.resident(self.alice, self.unit1).state, "departure_pending")
        self.assertIn("plus récent", self.resident(self.alice, self.unit1).departure_signal)
        self.assertEqual(self.resident(self.bruno, self.unit1).state, "active")

    def test_renewal_as_a_new_lease_keeps_the_tenant_and_follows_the_lease(self):
        self.make_point()
        self.lease(self.alice, self.unit1, start=self.days(-400), end=self.days(-35))
        new = self.lease(self.alice, self.unit1, start=self.days(-34))
        res = self.resident(self.alice, self.unit1)
        self.assertEqual((res.state, res.lease_id), ("active", new))
        self.assertEqual(len(res.grant_ids), 1)

    def test_lease_without_unit_gets_common_points_only(self):
        entrance = self.make_point()
        self.make_point(name="Porte 101", point_type="unit_door", unit_id=self.unit1.id)
        self.lease(self.alice, None, is_room=True)
        res = self.resident(self.alice)
        self.assertEqual(res.state, "active")
        self.assertEqual(res.grant_ids.point_id, entrance)

    def test_declared_spouse_keeps_access_when_the_signatory_leaves(self):
        """Art. 1938 : le conjoint cohabitant a droit au maintien dans les lieux."""
        self.make_point()
        lease = self.lease(self.alice, self.unit1)
        spouse = self.Resident.with_user(self.manager).create({
            "partner_id": self.chloe.id, "building_id": self.building.id,
            "unit_id": self.unit1.id,
        })
        self.env["bf.rental.abandonment"].create({
            "lease_id": lease.id, "date_noticed": self.today, "kind": "deguerpissement",
        })
        alice = self.resident(self.alice, self.unit1)
        alice.with_user(self.manager)._bf_confirm_departure(self.today)
        self.assertEqual(alice.state, "left")
        self.assertEqual(spouse.state, "active")
        self.assertEqual(spouse.grant_ids.state, "to_issue")

    def test_lease_is_saved_even_if_the_access_sync_fails(self):
        Resident = type(self.env["bf.property.access.resident"])
        with patch.object(Resident, "_bf_sync", side_effect=RuntimeError("panne")):
            lease = self.lease(self.alice, self.unit1)
            lease.rent = 1100.0
        self.assertEqual(lease.rent, 1100.0)
        self.assertFalse(self.resident(self.alice, self.unit1))
        self.sync()
        self.assertEqual(self.resident(self.alice, self.unit1).state, "active")

    def test_archived_lease_flags_departure(self):
        self.make_point()
        lease = self.lease(self.alice, self.unit1)
        lease.active = False
        res = self.resident(self.alice, self.unit1)
        self.assertEqual(res.state, "departure_pending")
        self.assertIn("archivé", res.departure_signal)

    # Relecture adverse 1 (2026-10-31)
    def test_r1_4_lease_whose_unit_is_in_another_building_is_ignored(self):
        other = self.env["bf.property.building"].create(
            {"name": "Ailleurs (banc)", "organisation_id": self.landlord.id})
        self.make_point()
        lease = self.lease(self.alice, self.unit1)
        self.env.cr.execute("UPDATE bf_rental_lease SET building_id = %s WHERE id = %s",
                            [other.id, lease.id])
        lease.invalidate_recordset()
        self.lease(self.bruno, self.unit2)
        self.Resident._cron_sync()
        self.assertEqual(self.resident(self.bruno, self.unit2).state, "active")

    def test_r1_5_the_lease_prevails_over_the_occupant_register(self):
        self.make_point()
        self.unit1.write({"is_rented": True, "occupant_id": self.alice.id})
        lease = self.lease(self.alice, self.unit1)
        res = self.resident(self.alice, self.unit1)
        self.assertEqual(res.state, "active")
        self.env["bf.rental.abandonment"].create({
            "lease_id": lease.id, "date_noticed": self.today, "kind": "deguerpissement",
        })
        self.assertEqual(res.state, "departure_pending")
        self.assertIn("préavis", res.departure_signal)

    def test_r1_5_registered_occupant_without_lease_on_a_leased_unit_is_flagged(self):
        self.make_point()
        self.unit1.write({"is_rented": True, "occupant_id": self.chloe.id})
        self.sync()
        self.assertEqual(self.resident(self.chloe, self.unit1).state, "active")
        self.lease(self.alice, self.unit1)
        self.assertEqual(self.resident(self.chloe, self.unit1).state, "departure_pending")

    def test_r1_2_cotenant_who_left_but_stays_on_the_lease_is_not_reissued(self):
        self.make_point()
        self.lease(self.alice | self.bruno, self.unit1)
        bruno = self.resident(self.bruno, self.unit1)
        bruno.with_user(self.manager)._bf_confirm_departure(self.today)
        self.Resident._cron_sync()
        rows = self.Resident.search([("partner_id", "=", self.bruno.id)])
        self.assertEqual(rows.mapped("state"), ["left"])
        self.assertTrue(bruno.stale_source_noted)

    def test_r1_10_parking_lease_makes_no_resident(self):
        spot = self.env["bf.property.unit"].create(
            {"name": "P-1", "building_id": self.building.id, "unit_type": "parking"})
        self.make_point()
        self.lease(self.alice, spot)
        self.assertFalse(self.resident(self.alice, spot))

    # Relecture adverse 2 (2026-10-31), R2-3 [I]
    def test_r2_3_an_ended_lease_does_not_erase_the_owner_who_took_the_unit_back(self):
        self.make_point()
        lease = self.lease(self.bruno, self.unit1, start=self.days(-400), end=self.days(-35))
        self.env["bf.rental.abandonment"].create({
            "lease_id": lease.id, "date_noticed": self.days(-20), "kind": "deguerpissement",
        })
        self.own(self.alice, self.unit1)
        self.sync()
        self.assertEqual(self.resident(self.alice, self.unit1).state, "active")
        self.assertEqual(self.resident(self.bruno, self.unit1).state, "departure_pending")
