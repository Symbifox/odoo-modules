from datetime import datetime, timedelta

import pytz

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import AccessCase

TZ = pytz.timezone("America/Toronto")


def local(day, hour, minute=0):
    """Une heure de Montréal, rendue en UTC naïf comme Odoo la stocke."""
    return TZ.localize(datetime.combine(day, datetime.min.time()).replace(
        hour=hour, minute=minute)).astimezone(pytz.utc).replace(tzinfo=None)


@tagged("post_install", "-at_install")
class TestPass(AccessCase):

    def setUp(self):
        super().setUp()
        self.entrance = self.make_point()
        self.garage = self.make_point(name="Garage", point_type="garage", credential_kind="card")
        self.door = self.make_point(name="Porte 101", point_type="unit_door", unit_id=self.unit1.id)
        self.Pass = self.env["bf.property.access.pass"].with_user(self.manager)
        self.d = self.days(3)

    def contractor(self, **kw):
        vals = {"kind": "contractor", "holder_name": "Plomberie Exemple", "building_id": self.building.id,
                "point_ids": [(6, 0, [self.entrance.id])],
                "date_from": local(self.d, 13), "date_to": local(self.d, 16)}
        vals.update(kw)
        return self.Pass.create(vals)

    def entry(self, **kw):
        vals = {"kind": "unit_entry", "holder_name": "Ouvrier Exemple", "building_id": self.building.id,
                "unit_id": self.unit1.id, "point_ids": [(6, 0, [self.entrance.id, self.door.id])],
                "entry_reason": "works", "notice_given_on": fields.Datetime.now() - timedelta(hours=1),
                "date_from": local(self.d, 9), "date_to": local(self.d, 12)}
        vals.update(kw)
        return self.Pass.create(vals)

    # ── Entrepreneur ──

    def test_confirmed_pass_orders_each_point_with_its_window(self):
        temp = self.contractor(point_ids=[(6, 0, [self.entrance.id, self.garage.id])])
        temp.action_confirm()
        self.assertEqual(temp.state, "confirmed")
        self.assertEqual(temp.grant_ids.point_id, self.entrance | self.garage)
        self.assertEqual(set(temp.grant_ids.mapped("state")), {"to_issue"})
        order = temp.grant_ids.order_ids.filtered(lambda o: o.point_id == self.entrance)
        self.assertIn("Donner à Plomberie Exemple", order.summary)
        self.assertEqual(order.valid_from, temp.date_from)
        self.assertEqual(order.hand_over_activity_id.date_deadline, self.d)
        self.assertEqual(temp.grant_ids.filtered(lambda g: g.point_id == self.garage).credential_kind, "pin")

    def test_window_end_orders_the_removal(self):
        temp = self.contractor()
        temp.action_confirm()
        temp.grant_ids.order_ids.with_user(self.concierge).action_done()
        self.env.cr.execute("UPDATE bf_property_access_pass SET date_from = %s, date_to = %s WHERE id = %s",
                            [fields.Datetime.now() - timedelta(hours=3),
                             fields.Datetime.now() - timedelta(minutes=1), temp.id])
        temp.invalidate_recordset()
        self.assertEqual(self.env["bf.property.access.pass"]._cron_expire(), 1)
        self.assertEqual(temp.state, "ended")
        grant = temp.grant_ids
        self.assertEqual((grant.state, grant.end_reason), ("to_remove", "expired"))
        remove = grant.order_ids.filtered(lambda o: o.action == "remove")
        self.assertIn("Retirer l'accès temporaire de Plomberie Exemple", remove.summary)

    def test_cancel_before_the_concierge_acts_cancels_and_orders_removal(self):
        temp = self.contractor()
        temp.action_confirm()
        temp.action_cancel()
        self.assertEqual(temp.state, "cancelled")
        self.assertEqual(temp.grant_ids.state, "to_remove", "l'ajout a pu être fait sans être coché")

    def test_only_a_manager_confirms_and_a_confirmed_pass_is_frozen(self):
        temp = self.contractor()
        with self.assertRaises(AccessError):
            temp.with_user(self.concierge).action_confirm()
        temp.action_confirm()
        with self.assertRaises(UserError):
            temp.write({"date_to": local(self.d, 20)})
        with self.assertRaises(UserError):
            temp.write({"state": "draft"})

    def test_past_window_and_empty_points_are_refused(self):
        with self.assertRaises(UserError):
            self.contractor(date_from=local(self.days(-2), 9), date_to=local(self.days(-2), 10)).action_confirm()
        with self.assertRaises(UserError):
            self.contractor(point_ids=[(5,)]).action_confirm()

    def test_unit_door_of_another_unit_is_refused(self):
        with self.assertRaises(ValidationError):
            self.contractor(point_ids=[(6, 0, [self.door.id])])
        with self.assertRaises(ValidationError):
            self.entry(unit_id=self.unit2.id)

    def test_lost_pin_of_a_pass_is_reissued(self):
        temp = self.contractor()
        temp.action_confirm()
        grant = temp.grant_ids
        grant.with_user(self.manager).action_report_lost()
        self.assertEqual(grant.state, "to_remove")
        self.assertEqual(len(temp.grant_ids.filtered(lambda g: g.state == "to_issue")), 1)

    def test_purge_takes_old_ended_passes_only_once_closed(self):
        self.org.access_log_retention_days = 30
        temp = self.contractor()
        temp.action_confirm()
        temp.action_end()
        self.env.cr.execute("UPDATE bf_property_access_pass SET date_from = %s, date_to = %s WHERE id = %s",
                            [fields.Datetime.now() - timedelta(days=41),
                             fields.Datetime.now() - timedelta(days=40), temp.id])
        temp.invalidate_recordset()
        self.Resident._cron_purge()
        self.assertTrue(temp.exists(), "le retrait n'est pas fait")
        temp.grant_ids.order_ids.filtered(lambda o: o.state == "waiting").with_user(
            self.concierge).action_done()
        self.Resident._cron_purge()
        self.assertFalse(temp.exists())

    def test_a_grant_has_exactly_one_holder(self):
        temp = self.contractor()
        temp.action_confirm()
        self.own(self.alice, self.unit1)
        self.sync()
        res = self.resident(self.alice, self.unit1)
        with self.assertRaises(ValidationError):
            self.env["bf.property.access.grant"].sudo().create(
                {"resident_id": res.id, "pass_id": temp.id, "point_id": self.entrance.id})

    # ── Entrée au logement (art. 1930-1933) ──

    def test_unit_entry_needs_24_hours_notice(self):
        with self.assertRaises(UserError) as caught:
            self.entry(notice_given_on=False).action_confirm()
        self.assertIn("1931", str(caught.exception))
        soon = fields.Datetime.now() + timedelta(hours=10)
        with self.assertRaises(UserError) as caught:
            self.entry(notice_given_on=fields.Datetime.now() - timedelta(hours=1),
                       date_from=soon, date_to=soon + timedelta(hours=1)).action_confirm()
        self.assertIn("24 heures", str(caught.exception))

    def test_works_outside_7_to_19_need_the_tenants_agreement(self):
        late = self.entry(date_from=local(self.d, 18), date_to=local(self.d, 20))
        with self.assertRaises(UserError) as caught:
            late.action_confirm()
        self.assertIn("1933", str(caught.exception))
        late.write({"tenant_agreed_outside_hours": True})
        with self.assertRaises(UserError):
            late.action_confirm()
        late.write({"tenant_agreement_note": "Accord écrit du locataire, courriel du 28 oct."})
        late.action_confirm()
        self.assertEqual(late.state, "confirmed")

    def test_a_showing_before_9_is_refused(self):
        early = self.entry(entry_reason="showing_buyer", date_from=local(self.d, 8), date_to=local(self.d, 9))
        with self.assertRaises(UserError) as caught:
            early.action_confirm()
        self.assertIn("1932", str(caught.exception))

    def test_urgency_needs_a_reason_and_no_notice(self):
        urgent = self.entry(entry_reason="urgent", notice_given_on=False,
                            date_from=local(self.d, 2), date_to=local(self.d, 4))
        with self.assertRaises(UserError):
            urgent.action_confirm()
        urgent.write({"urgent_reason": "Fuite d'eau au plafond du 001."})
        urgent.action_confirm()
        self.assertEqual(urgent.state, "confirmed")

    def test_unit_entry_gets_the_door_without_the_two_consents(self):
        temp = self.entry()
        temp.action_confirm()
        door_grant = temp.grant_ids.filtered(lambda g: g.point_id == self.door)
        self.assertEqual(door_grant.state, "to_issue", "une entrée n'est pas une serrure posée")
        self.assertEqual(door_grant.unit_id, self.unit1)

    def test_default_points_are_the_common_doors_and_the_units_door(self):
        form_pass = self.Pass.new({"kind": "unit_entry", "building_id": self.building.id,
                                   "unit_id": self.unit1.id, "holder_name": "X"})
        form_pass._onchange_default_points()
        self.assertEqual(form_pass.point_ids._origin, self.entrance | self.door)

    def test_residents_are_untouched_by_passes(self):
        self.own(self.alice, self.unit1)
        self.sync()
        res = self.resident(self.alice, self.unit1)
        before = res.grant_ids
        temp = self.entry()
        temp.action_confirm()
        temp.action_end()
        self.assertEqual(res.grant_ids, before)
        self.assertEqual(res.state, "active")

    def test_pass_views_open_for_both_roles(self):
        self.contractor().action_confirm()
        for user in (self.manager, self.concierge):
            Model = self.env["bf.property.access.pass"].with_user(user)
            for view_type in ("list", "form"):
                Model.get_views([(False, view_type)])
            Model.search([]).read(["display_name", "state", "grant_ids"])


@tagged("post_install", "-at_install")
class TestPassReview1(TestPass):
    """Constats de la relecture adverse 1 du lot 4 (2026-10-31)."""

    def test_r1_b1_hours_are_the_buildings_not_the_managers(self):
        self.manager.tz = "Pacific/Auckland"
        Pass = self.env["bf.property.access.pass"].with_context(tz=False).with_user(self.manager)
        ok = Pass.create({"kind": "unit_entry", "holder_name": "Ouvrier", "building_id": self.building.id,
                          "unit_id": self.unit1.id, "point_ids": [(6, 0, [self.entrance.id])],
                          "entry_reason": "works", "notice_given_on": fields.Datetime.now() - timedelta(hours=1),
                          "date_from": local(self.d, 9), "date_to": local(self.d, 12)})
        ok.action_confirm()
        self.assertEqual(ok.state, "confirmed")
        order = ok.grant_ids.order_ids
        self.assertEqual(order.hand_over_activity_id.date_deadline, self.d)
        self.assertIn("09:00", order.with_context(tz=False).summary.replace("9:00", "09:00"))
        late = Pass.create({"kind": "unit_entry", "holder_name": "Ouvrier", "building_id": self.building.id,
                            "unit_id": self.unit1.id, "point_ids": [(6, 0, [self.entrance.id])],
                            "entry_reason": "works", "notice_given_on": fields.Datetime.now() - timedelta(hours=1),
                            "date_from": local(self.d, 19), "date_to": local(self.d, 21)})
        with self.assertRaises(UserError):
            late.action_confirm()

    def test_r1_i1_a_prospective_tenant_showing_needs_no_notice(self):
        soon = self.entry(entry_reason="showing_tenant", notice_given_on=False,
                          tenant_notice_received_on=self.days(-10),
                          date_from=local(self.d, 10), date_to=local(self.d, 11))
        soon.action_confirm()
        self.assertEqual(soon.state, "confirmed")
        self.assertTrue(soon.message_ids.filtered(lambda m: "1932" in (m.body or "")),
                        "sans accompagnement, l'avertissement de l'art. 1932 al. 2")
        with self.assertRaises(UserError):
            self.entry(entry_reason="showing_buyer", notice_given_on=False,
                       date_from=local(self.d, 10), date_to=local(self.d, 11)).action_confirm()

    def test_r1_i2_holder_name_and_company_follow_their_sources(self):
        self.own(self.alice, self.unit1)
        self.sync()
        grant = self.resident(self.alice, self.unit1).grant_ids[:1]
        self.alice.name = "Alix Exemple"
        self.assertEqual(grant.holder_label, "Alix Exemple")
        self.assertIn("Alix Exemple", grant.order_ids[:1].summary)
        autre = self.env["res.company"].create({"name": "Société E (banc)"})
        self.org.company_id = autre
        self.assertEqual(grant.company_id, autre)
        self.assertEqual(grant.order_ids[:1].company_id, autre)

    def test_r1_i3_a_point_that_becomes_a_unit_door_drops_temporary_rights(self):
        temp = self.contractor()
        temp.action_confirm()
        self.entrance.with_user(self.manager).write({"point_type": "unit_door", "unit_id": self.unit1.id})
        self.assertIn(temp.grant_ids.state, ("to_remove", "cancelled"))
        other = self.env["bf.property.building"].create({"name": "Ailleurs (banc)", "organisation_id": self.org.id})
        temp2 = self.contractor(point_ids=[(6, 0, [self.garage.id])])
        temp2.action_confirm()
        self.garage.with_user(self.manager).building_id = other
        self.assertIn(temp2.grant_ids.state, ("to_remove", "cancelled"))

    def test_r1_m1_a_pass_is_not_born_confirmed(self):
        temp = self.contractor(state="confirmed")
        self.assertEqual(temp.state, "draft")

    def test_r1_m2_notice_cannot_be_dated_in_the_future(self):
        with self.assertRaises(UserError):
            self.entry(notice_given_on=fields.Datetime.now() + timedelta(days=1)).action_confirm()

    def test_r1_m3_evidence_is_frozen_once_confirmed(self):
        temp = self.entry(date_from=local(self.d, 18), date_to=local(self.d, 20),
                          tenant_agreed_outside_hours=True, tenant_agreement_note="Accord écrit")
        temp.action_confirm()
        for field, value in (("tenant_agreement_note", ""), ("holder_name", "Autre"),
                             ("accompanied_by", "X"), ("notice_method", "verbal")):
            with self.assertRaises(UserError):
                temp.write({field: value})

    def test_r1_m4_multi_day_works_suggest_one_access_per_day(self):
        with self.assertRaises(UserError) as caught:
            self.entry(date_from=local(self.d, 8), date_to=local(self.d + timedelta(days=2), 17)).action_confirm()
        self.assertIn("par jour", str(caught.exception))

    def test_r1_m6_a_reactivated_point_serves_the_pass_again(self):
        temp = self.contractor()
        temp.action_confirm()
        self.entrance.with_user(self.manager).active = False
        self.entrance.with_user(self.manager).active = True
        self.assertTrue(temp.grant_ids.filtered(lambda g: g.state == "to_issue"))

    def test_r1_m7_old_drafts_are_purged(self):
        self.org.access_log_retention_days = 30
        draft = self.contractor()
        self.env.cr.execute("UPDATE bf_property_access_pass SET date_from = %s, date_to = %s WHERE id = %s",
                            [fields.Datetime.now() - timedelta(days=41),
                             fields.Datetime.now() - timedelta(days=40), draft.id])
        draft.invalidate_recordset()
        self.Resident._cron_purge()
        self.assertFalse(draft.exists())


@tagged("post_install", "-at_install")
class TestPassReview2(TestPass):
    """Constats de la relecture adverse 2 du lot 4 (2026-10-31)."""

    def test_r2_i3_showing_to_a_prospective_tenant_without_notice_needs_the_tenants_notice(self):
        with self.assertRaises(UserError):
            self.entry(entry_reason="showing_tenant", notice_given_on=False,
                       date_from=local(self.d, 10), date_to=local(self.d, 11)).action_confirm()
        ok = self.entry(entry_reason="showing_tenant", notice_given_on=False,
                        tenant_notice_received_on=self.days(-20),
                        date_from=local(self.d, 10), date_to=local(self.d, 11))
        ok.action_confirm()
        self.assertEqual(ok.state, "confirmed")

    def test_r2_m1_computed_holder_fields_cannot_be_rewritten(self):
        temp = self.contractor()
        temp.action_confirm()
        for field, value in (("holder_label", "Faux"), ("company_id", False),
                             ("partner_id", self.bruno.id)):
            with self.assertRaises(UserError):
                temp.grant_ids.with_user(self.manager).write({field: value})

    def test_r2_m6_vacant_dwelling_needs_no_notice_but_must_be_vacant(self):
        empty = self.env["bf.property.unit"].create({"name": "999", "building_id": self.building.id})
        ok = self.entry(entry_reason="vacant", unit_id=empty.id, notice_given_on=False,
                        point_ids=[(6, 0, [self.entrance.id])])
        ok.action_confirm()
        self.assertEqual(ok.state, "confirmed")
        self.own(self.alice, self.unit1)
        self.sync()
        with self.assertRaises(UserError):
            self.entry(entry_reason="vacant", notice_given_on=False).action_confirm()

    def test_r2_m7_major_works_need_ten_days_written_notice(self):
        with self.assertRaises(UserError) as caught:
            self.entry(works_major=True, notice_method="written").action_confirm()
        self.assertIn("1922", str(caught.exception))
        start = self.days(12)
        ok = self.entry(works_major=True, notice_method="written",
                        date_from=local(start, 9), date_to=local(start, 12))
        ok.action_confirm()
        self.assertEqual(ok.state, "confirmed")

    def test_r2_m8_grant_views_show_the_holder(self):
        arch = self.env["bf.property.access.grant"].get_views([(False, "list")])["views"]["list"]["arch"]
        self.assertIn("holder_label", arch)

    def test_r2_m9_the_concierge_reads_the_pass_note(self):
        temp = self.contractor(note="Visiteur de Alice Exemple (101).")
        temp.action_confirm()
        self.assertIn("Alice Exemple", temp.grant_ids.order_ids.last_message)


@tagged("post_install", "-at_install")
class TestPassGuide(TestPass):
    """Défauts d'écran relevés par le guide (2026-10-31)."""

    def test_g_display_name_uses_the_translated_kind(self):
        temp = self.contractor()
        kinds = dict(temp._fields["kind"]._description_selection(temp.env))
        self.assertTrue(temp.display_name.endswith(kinds[temp.kind]))

    def test_g_chatter_dates_are_short(self):
        temp = self.contractor()
        self.assertNotIn(" s", temp._bf_fmt(temp.date_from).replace(" sept", ""))


@tagged("post_install", "-at_install")
class TestPassMutations(TestPass):
    """Mutantes survivantes de la QA du 2026-10-31."""

    def test_m_a6_a_showing_ending_after_21_is_refused(self):
        late = self.entry(entry_reason="showing_buyer", date_from=local(self.d, 19),
                          date_to=local(self.d, 21, 30))
        with self.assertRaises(UserError) as caught:
            late.action_confirm()
        self.assertIn("1932", str(caught.exception))

    def test_m_a8_a_tenants_notice_dated_in_the_future_does_not_waive_the_notice(self):
        with self.assertRaises(UserError):
            self.entry(entry_reason="showing_tenant", notice_given_on=False,
                       tenant_notice_received_on=self.days(5),
                       date_from=local(self.d, 10), date_to=local(self.d, 11)).action_confirm()

    def test_m_a9_major_works_need_a_written_notice(self):
        start = self.days(12)
        with self.assertRaises(UserError) as caught:
            self.entry(works_major=True, notice_method="verbal",
                       date_from=local(start, 9), date_to=local(start, 12)).action_confirm()
        self.assertIn("1922", str(caught.exception))

    def test_m_a10_an_evacuation_over_a_week_needs_three_months(self):
        start = self.days(12)
        with self.assertRaises(UserError) as caught:
            self.entry(works_major=True, evacuation_over_week=True, notice_method="written",
                       date_from=local(start, 9), date_to=local(start, 12)).action_confirm()
        self.assertIn("90", str(caught.exception))

    def test_m_a12_a_visitor_never_gets_the_garage(self):
        temp = self.contractor(kind="visitor", point_ids=[(6, 0, [self.entrance.id, self.garage.id])])
        temp.action_confirm()
        live = temp.grant_ids.filtered(lambda g: g.state in ("awaiting_consent", "to_issue", "issued"))
        self.assertEqual(live.point_id, self.entrance)

    def test_m_a13_an_archived_point_withdraws_the_temporary_right(self):
        temp = self.contractor()
        temp.action_confirm()
        self.entrance.with_user(self.manager).active = False
        self.assertIn(temp.grant_ids.state, ("to_remove", "cancelled"))
        # Même quand le contexte montre les archivés (import, action serveur) :
        # le Many2many ne cache plus le point, la garde `point.active` seule joue.
        temp2 = self.contractor(point_ids=[(6, 0, [self.garage.id])])
        temp2.action_confirm()
        self.garage.with_user(self.manager).with_context(active_test=False).active = False
        self.assertIn(temp2.grant_ids.state, ("to_remove", "cancelled"))
