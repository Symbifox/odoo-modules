from datetime import timedelta

import pytz

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.bf_property_access.tests.common import AccessCase


@tagged("post_install", "-at_install")
class TestAccessPortal(AccessCase, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.portal_alice = new_test_user(cls.env, login="alice_portail", groups="base.group_portal",
                                         partner_id=cls.alice.id, password="alice_portail-essai", tz="America/Toronto")
        cls.portal_bruno = new_test_user(cls.env, login="bruno_portail", groups="base.group_portal",
                                         partner_id=cls.bruno.id, password="bruno_portail-essai", tz="America/Toronto")

    def _decor(self):
        self.make_point(credential_kind="card")
        self.make_point(name="Porte 101", point_type="unit_door", unit_id=self.unit1.id)
        self.own(self.alice, self.unit1)
        self.own(self.bruno, self.unit2)
        self.sync()

    def test_portal_sees_only_its_own_live_rights(self):
        self._decor()
        mine = self.env["bf.property.access.grant"].with_user(self.portal_alice).search([])
        self.assertEqual(mine.partner_id, self.alice)
        self.assertEqual(len(mine), 2)
        bruno_grant = self.resident(self.bruno, self.unit2).grant_ids
        with self.assertRaises(AccessError):
            bruno_grant.with_user(self.portal_alice).read(["external_ref"])
        with self.assertRaises(AccessError):
            mine[:1].with_user(self.portal_alice).write({"external_ref": "X"})

    def test_removed_rights_leave_the_portal(self):
        self._decor()
        res = self.resident(self.alice, self.unit1)
        res.with_user(self.manager)._bf_confirm_departure(self.today)
        res.grant_ids.order_ids.filtered(lambda o: o.state == "waiting").with_user(
            self.concierge).action_done()
        visible = self.env["bf.property.access.grant"].with_user(self.portal_alice).search([])
        self.assertFalse(visible.filtered(lambda g: g.state in ("removed", "cancelled")))

    def test_my_access_page_lists_points_and_no_one_elses(self):
        self._decor()
        alice = self.resident(self.alice, self.unit1)
        alice.grant_ids.filtered(lambda g: g.state == "to_issue").order_ids.with_user(
            self.manager).write({"external_ref": "CARTE-0042"})
        alice.grant_ids.filtered(lambda g: g.state == "to_issue").order_ids.with_user(
            self.concierge).action_done()
        self.authenticate("alice_portail", "alice_portail-essai")
        page = self.url_open("/my/property/access").text
        self.assertIn("Portier nord", page)
        self.assertIn("CARTE-0042", page)
        self.assertIn("Porte 101", page)
        self.assertIn("1934", page)
        self.authenticate("bruno_portail", "bruno_portail-essai")
        page = self.url_open("/my/property/access").text
        self.assertNotIn("CARTE-0042", page)
        self.assertNotIn("Porte 101", page)

    def test_home_counter_counts_issued_rights(self):
        self._decor()
        alice = self.resident(self.alice, self.unit1)
        alice.grant_ids.filtered(lambda g: g.state == "to_issue").order_ids.with_user(
            self.concierge).action_done()
        self.authenticate("alice_portail", "alice_portail-essai")
        res = self.make_jsonrpc_request("/my/counters", {"counters": ["property_access_count"]})
        # Émis ET en attente des consentements : la carte paraît dès qu'un droit est en route.
        self.assertEqual(res["property_access_count"], 2)

    # Relecture 1 du lot 1 bis (2026-10-31)
    def test_r1_2_my_access_page_of_an_internal_user_shows_only_their_own(self):
        self._decor()
        self.manager.password = "acces_gestion-essai"
        self.authenticate("acces_gestion", "acces_gestion-essai")
        page = self.url_open("/my/property/access").text
        self.assertNotIn("Portier nord", page)
        self.assertIn("Aucun accès", page)

    def test_r1_1_portal_cannot_download_the_efvp(self):
        if "bf_property_access_privacy" not in self.env.registry._init_modules:
            self.skipTest("pont Loi 25 absent")
        self._decor()
        self.authenticate("alice_portail", "alice_portail-essai")
        for kind in ("pdf", "html"):
            r = self.url_open("/report/%s/bf_property_access_privacy.report_access_efvp/%s" % (kind, self.org.id))
            self.assertNotIn("Portier nord", r.text)
            self.assertNotEqual(r.headers.get("Content-Type", ""), "application/pdf")

    # Relecture 2 du lot 1 bis (2026-10-31)
    def test_r2_2_a_resident_whose_building_is_in_another_company_sees_their_doors(self):
        autre = self.env["res.company"].create({"name": "Société D (banc)"})
        self._decor()
        self.org.company_id = autre
        self.assertNotIn(autre, self.portal_alice.company_ids)
        self.authenticate("alice_portail", "alice_portail-essai")
        page = self.url_open("/my/property/access").text
        self.assertIn("Portier nord", page)
        self.authenticate("bruno_portail", "bruno_portail-essai")
        self.assertNotIn("Porte 101", self.url_open("/my/property/access").text)

    def test_r2_3_the_page_carries_the_privacy_notice(self):
        if "bf_property_access_privacy" not in self.env.registry._init_modules:
            self.skipTest("pont Loi 25 absent")
        self._decor()
        self.authenticate("alice_portail", "alice_portail-essai")
        self.assertIn("Vos renseignements", self.url_open("/my/property/access").text)


@tagged("post_install", "-at_install")
class TestVisitorPass(AccessCase, HttpCase):
    """Lot 4 : le visiteur annoncé au portail reçoit un accès temporaire."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.portal_alice = new_test_user(cls.env, login="alice_visite", groups="base.group_portal",
                                         partner_id=cls.alice.id, password="alice_visite-essai",
                                         tz="America/Toronto")
        cls.portal_bruno = new_test_user(cls.env, login="bruno_visite", groups="base.group_portal",
                                         partner_id=cls.bruno.id, password="bruno_visite-essai",
                                         tz="America/Toronto")

    def setUp(self):
        super().setUp()
        self.entrance = self.make_point()
        self.garage = self.make_point(name="Garage", point_type="garage")
        self.door = self.make_point(name="Porte 101", point_type="unit_door", unit_id=self.unit1.id)
        self.own(self.alice, self.unit1)
        self.own(self.bruno, self.unit2)
        self.sync()

    def announce(self, access=True, until="", unit=True):
        self.authenticate("alice_visite", "alice_visite-essai")
        demain = (self.days(1)).strftime("%Y-%m-%dT14:00")
        data = {"visitor_name": "Invitée Exemple", "visitor_kind": "guest", "date_expected": demain,
                "unit_id": str(self.unit1.id) if unit else "", "csrf_token": http_csrf(self)}
        if access:
            data["bf_access"] = "1"
            data["bf_access_until"] = until
        return self.url_open("/my/property/visitors/new", data=data)

    def visit(self):
        return self.env["bf.property.visit"].search([("host_partner_id", "=", self.alice.id)],
                                                    order="id desc", limit=1)

    def test_announced_visitor_gets_the_common_doors_for_the_visit(self):
        self.announce()
        visit = self.visit()
        temp = visit.access_pass_id
        self.assertEqual((temp.kind, temp.state), ("visitor", "confirmed"))
        self.assertEqual(temp.point_ids, self.entrance, "ni le garage ni la porte d'un logement")
        self.assertEqual(temp.date_to - temp.date_from, timedelta(hours=2, minutes=15))
        self.assertEqual(temp.grant_ids.order_ids.action, "add")
        page = self.url_open("/my/property/deliveries").text
        self.assertIn("Confirmé", page)

    def test_the_organisation_caps_the_window(self):
        self.org.visitor_pass_max_hours = 1
        self.announce(until=(self.days(2)).strftime("%Y-%m-%dT23:00"))
        temp = self.visit().access_pass_id
        self.assertEqual(temp.date_to - temp.date_from, timedelta(hours=1))

    def test_disabled_organisation_announces_without_access(self):
        self.org.visitor_pass_enabled = False
        r = self.announce()
        self.assertIn("error=access", r.url)
        self.assertTrue(self.visit())
        self.assertFalse(self.visit().access_pass_id)
        self.assertNotIn('name="bf_access"', self.url_open("/my/property/deliveries").text,
                         "la case n'est pas offerte quand l'organisation ne le permet pas")

    def test_cancelling_or_leaving_closes_the_access(self):
        self.announce()
        visit = self.visit()
        visit.with_user(self.portal_alice).action_cancel()
        self.assertEqual(visit.access_pass_id.state, "cancelled")

    def test_someone_else_cannot_give_access_to_my_visitor(self):
        self.announce(access=False)
        visit = self.visit()
        with self.assertRaises(AccessError):
            visit.with_user(self.portal_bruno)._bf_request_access()
        self.assertFalse(visit.access_pass_id)


def http_csrf(case):
    """Le jeton CSRF de la session ouverte par authenticate()."""
    page = case.url_open("/my/property/deliveries").text
    import re
    return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


@tagged("post_install", "-at_install")
class TestVisitorPassReview1(TestVisitorPass):
    """Constats de la relecture adverse 1 du lot 4 (2026-10-31), côté portail."""

    def test_r1_m5_cancelling_or_moving_by_write_closes_the_access(self):
        self.announce()
        visit = self.visit()
        visit.with_user(self.portal_alice).write({"state": "cancelled"})
        self.assertEqual(visit.access_pass_id.state, "cancelled")
        self.announce()
        visit = self.visit()
        visit.with_user(self.portal_alice).write({"date_expected": fields.Datetime.now() + timedelta(days=5)})
        self.assertEqual(visit.access_pass_id.state, "cancelled")

    def test_r1_m8_no_access_more_than_30_days_ahead(self):
        self.authenticate("alice_visite", "alice_visite-essai")
        far = (self.days(60)).strftime("%Y-%m-%dT14:00")
        r = self.url_open("/my/property/visitors/new", data={
            "visitor_name": "Lointaine", "visitor_kind": "guest", "date_expected": far,
            "unit_id": str(self.unit1.id), "csrf_token": http_csrf(self), "bf_access": "1"})
        self.assertIn("error=access", r.url)

    def test_r1_m8_host_departure_closes_their_visitors_access(self):
        self.announce()
        temp = self.visit().access_pass_id
        self.resident(self.alice, self.unit1).with_user(self.manager)._bf_confirm_departure(self.today)
        self.assertEqual(temp.state, "cancelled")


@tagged("post_install", "-at_install")
class TestVisitorPassReview2(TestVisitorPass):
    """Constats de la relecture adverse 2 du lot 4 (2026-10-31), côté portail."""

    def test_r2_b1_a_departed_resident_cannot_give_access(self):
        self.announce(access=False)
        visit = self.visit()
        self.resident(self.alice, self.unit1).with_user(self.manager)._bf_confirm_departure(self.today)
        with self.assertRaises(UserError):
            visit.with_user(self.portal_alice)._bf_request_access()

    def test_r2_i1_the_window_is_in_the_buildings_time_zone(self):
        self.portal_alice.tz = False
        self.announce()
        temp = self.visit().access_pass_id
        start = pytz.utc.localize(temp.date_from).astimezone(pytz.timezone("America/Toronto"))
        self.assertEqual((start.hour, start.minute), (13, 45), "14 h à l'immeuble, moins 15 min")

    def test_r2_m2_an_entrance_turned_garage_drops_the_visitor(self):
        self.announce()
        temp = self.visit().access_pass_id
        self.entrance.with_user(self.manager).point_type = "garage"
        self.assertFalse(temp.grant_ids.filtered(
            lambda g: g.state in ("awaiting_consent", "to_issue", "issued")),
            "aucun droit vivant, ni l'ancien ni un recréé")

    def test_r2_m3_renaming_the_visitor_closes_the_access(self):
        self.announce()
        visit = self.visit()
        visit.with_user(self.portal_alice).write({"visitor_name": "Quelqu'un d'autre"})
        self.assertEqual(visit.access_pass_id.state, "cancelled")

    def test_r2_m4_moving_within_the_building_keeps_visitors(self):
        self.unit2.ownership_ids.unlink()
        self.own(self.alice, self.unit2)
        self.sync()
        self.announce()
        temp = self.visit().access_pass_id
        self.resident(self.alice, self.unit1).with_user(self.manager)._bf_confirm_departure(self.today)
        self.assertEqual(temp.state, "confirmed")


@tagged("post_install", "-at_install")
class TestVisitorPassMutations(TestVisitorPass):
    """Mutantes survivantes de la QA du 2026-10-31."""

    def test_m_p6_moving_the_end_of_the_visit_closes_the_access(self):
        self.announce()
        visit = self.visit()
        visit.with_user(self.portal_alice).write(
            {"date_expected_end": visit.date_expected + timedelta(hours=8)})
        self.assertEqual(visit.access_pass_id.state, "cancelled")

    def test_m_p7_a_departed_visitor_loses_the_access(self):
        self.announce()
        visit = self.visit()
        visit.with_user(self.manager).action_arrived()
        visit.with_user(self.manager).action_departed()
        self.assertEqual(visit.access_pass_id.state, "ended")


@tagged("post_install", "-at_install")
class TestVisitorPassParcours(TestVisitorPass):
    """Défauts trouvés au parcours joué au navigateur (2026-11-01)."""

    def test_p_default_unit_takes_the_building_where_the_host_lives(self):
        r = self.announce(unit=False)
        self.assertNotIn("error=access", r.url)
        temp = self.visit().access_pass_id
        self.assertEqual(temp.state, "confirmed")
        self.assertEqual(temp.building_id, self.building)

    def test_p_the_form_no_longer_says_it_opens_no_door(self):
        self.authenticate("alice_visite", "alice_visite-essai")
        page = self.url_open("/my/property/deliveries").text
        self.assertNotIn("aucune porte", page)
        self.assertIn("accès temporaire", page)
