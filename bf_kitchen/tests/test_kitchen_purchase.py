"""Supplier orders: delivery days, min and max on Odoo's reordering rules, the order guide and its
request for quotation.

The fixtures are TestKitchen's (salmon in g, a cream sauce, a dish). Odoo 18 runs only the test
methods a class defines itself."""
from datetime import date, timedelta
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .test_kitchen import TestKitchen


@tagged("post_install", "-at_install")
class TestKitchenPurchase(TestKitchen):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.warehouse = env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1)
        cls.salmon.product_tmpl_id.uom_po_id = cls.kg
        env["product.supplierinfo"].create({"partner_id": cls.vendor.id, "product_tmpl_id": cls.salmon.product_tmpl_id.id,
                                            "price": 30.0, "min_qty": 2.0})
        env["product.supplierinfo"].create({"partner_id": cls.vendor.id, "product_tmpl_id": cls.cream.product_tmpl_id.id,
                                            "price": 0.01})
        days = env["bf.kitchen.weekday"].search([("code", "in", (1, 4))])
        cls.vendor.write({"bf_kitchen_delivery_day_ids": [Command.set(days.ids)], "bf_kitchen_order_days": 1})

    def rule(self, product, low, high, user=None):
        return self.env["stock.warehouse.orderpoint"].with_user(user or self.env.user).create({
            "product_id": product.id, "warehouse_id": self.warehouse.id, "location_id": self.warehouse.lot_stock_id.id,
            "product_min_qty": low, "product_max_qty": high, "trigger": "manual"})

    def guide(self):
        guide = self.env["bf.kitchen.order.guide"].create({"partner_id": self.vendor.id})
        guide.action_compute()
        return guide

    def line(self, guide, product):
        return guide.line_ids.filtered(lambda l: l.product_id == product)

    def test_p1_delivery_days(self):
        """Delivering Tuesday and Friday, ordered a day ahead: on a Monday, the next delivery the kitchen
        can still order for is Tuesday, covering until Friday; on a Tuesday, Friday until next Tuesday."""
        self.assertEqual(self.vendor._bf_kitchen_deliveries(date(2026, 11, 2)), (date(2026, 11, 3), date(2026, 11, 6)))
        self.assertEqual(self.vendor._bf_kitchen_deliveries(date(2026, 11, 3)), (date(2026, 11, 6), date(2026, 11, 10)))
        other = self.env["res.partner"].create({"name": "Sans jours", "bf_food_supplier": True, "bf_kitchen_order_days": 2})
        self.assertEqual(other._bf_kitchen_deliveries(date(2026, 11, 2)), (date(2026, 11, 4), date(2026, 11, 11)))

    def test_p2_rules_of_kitchen_items_only(self):
        """A kitchen manager sets the min and max of kitchen items, not of other products; a stock
        manager keeps all of them."""
        chef = self.mkuser("p2_chef", "bf_kitchen.group_kitchen_manager")
        rule = self.rule(self.salmon, 1000, 5000, user=chef)
        rule.with_user(chef).product_max_qty = 6000
        screw = self.env["product.product"].create({"name": "Vis (essai)", "is_storable": True})
        with self.assertRaises(AccessError):
            self.rule(screw, 1, 5, user=chef)
        boss = self.mkuser("p2_boss", "bf_kitchen.group_kitchen_manager,stock.group_stock_manager")
        self.rule(screw, 1, 5, user=boss)

    def test_p3_guide_up_to_the_max(self):
        """Below its min, an item is ordered up to its max, rounded up to the supplier's unit and at
        least the supplier's minimum; an item without rule is ordered only to cover what is missing."""
        self.rule(self.salmon, 1000, 5000)
        self.env["stock.quant"]._update_available_quantity(self.salmon, self.warehouse.lot_stock_id, 400.0)
        guide = self.guide()
        salmon = self.line(guide, self.salmon)
        self.assertEqual(salmon.uom_id, self.kg)
        self.assertAlmostEqual(salmon.forecast, 400.0)
        self.assertAlmostEqual(salmon.suggested, 5.0, msg="4 600 g to reach the max: 5 kg")
        self.assertAlmostEqual(self.line(guide, self.cream).suggested, 0.0)
        self.assertTrue(guide.delivery_date and guide.cover_until > guide.delivery_date)

    def test_p4_known_demand_counts(self):
        """What the kitchen knows it will use (a bridge: the school meals ordered) comes off the forecast."""
        self.rule(self.salmon, 1000, 5000)
        self.env["stock.quant"]._update_available_quantity(self.salmon, self.warehouse.lot_stock_id, 400.0)
        Guide = type(self.env["bf.kitchen.order.guide"])
        with patch.object(Guide, "_bf_known_demand", lambda guide, date_to: {self.salmon: 1000.0}):
            guide = self.guide()
        salmon = self.line(guide, self.salmon)
        self.assertAlmostEqual(salmon.known_demand, 1000.0)
        self.assertAlmostEqual(salmon.suggested, 6.0, msg="5 600 g: 6 kg")

    def test_p5_minimum_of_the_supplier(self):
        """A small need is ordered at the supplier's minimum."""
        self.rule(self.salmon, 1000, 1500)
        self.env["stock.quant"]._update_available_quantity(self.salmon, self.warehouse.lot_stock_id, 900.0)
        self.assertAlmostEqual(self.line(self.guide(), self.salmon).suggested, 2.0, msg="600 g, at least 2 kg")

    def test_p6_request_for_quotation(self):
        """The guide makes Odoo's request for quotation, in the supplier's unit, for the delivery date;
        only with the Purchase rights; a cook does not open the guide."""
        self.rule(self.salmon, 1000, 5000)
        chef = self.mkuser("p6_chef", "bf_kitchen.group_kitchen_manager")
        guide = self.env["bf.kitchen.order.guide"].with_user(chef).create({"partner_id": self.vendor.id})
        guide.action_compute()
        with self.assertRaises(AccessError):
            guide.action_create_rfq()
        buyer = self.mkuser("p6_buyer", "bf_kitchen.group_kitchen_manager,purchase.group_purchase_user")
        guide = self.env["bf.kitchen.order.guide"].with_user(buyer).create({"partner_id": self.vendor.id})
        guide.action_compute()
        guide.line_ids.filtered(lambda l: l.product_id == self.cream).qty = 0.0
        action = guide.action_create_rfq()
        order = self.env["purchase.order"].browse(action["res_id"])
        self.assertEqual(order.state, "draft")
        self.assertEqual(order.partner_id, self.vendor)
        self.assertEqual(order.order_line.product_id, self.salmon)
        self.assertEqual((order.order_line.product_qty, order.order_line.product_uom), (5.0, self.kg))
        local = guide._bf_local_noon(guide.delivery_date)
        self.assertEqual(order.order_line.date_planned, local, "noon of the delivery day, in the company's zone")
        self.assertEqual(order.picking_type_id, self.warehouse.in_type_id)
        empty = self.env["bf.kitchen.order.guide"].with_user(buyer).create({"partner_id": self.vendor.id})
        empty.action_compute()
        empty.line_ids.qty = 0.0
        with self.assertRaises(UserError):
            empty.action_create_rfq()
        cook = self.mkuser("p6_cook", "bf_kitchen.group_kitchen_cook")
        with self.assertRaises(AccessError):
            self.env["bf.kitchen.order.guide"].with_user(cook).create({"partner_id": self.vendor.id})

    def test_r1_tiers_smallest_minimum(self):
        """A cheaper tier at 10 kg does not make the guide order 10 kg: the need, at least the
        smallest tier's minimum; the request for quotation gets Odoo's price for that quantity."""
        self.env["product.supplierinfo"].create({"partner_id": self.vendor.id, "product_tmpl_id": self.salmon.product_tmpl_id.id,
                                                 "price": 25.0, "min_qty": 10.0})
        self.rule(self.salmon, 1000, 5000)
        self.env["stock.quant"]._update_available_quantity(self.salmon, self.warehouse.lot_stock_id, 400.0)
        guide = self.guide()
        salmon = self.line(guide, self.salmon)
        self.assertAlmostEqual(salmon.suggested, 5.0)
        self.assertAlmostEqual(salmon.price_unit, 30.0)

    def test_r1_drafts_and_preferred_supplier(self):
        """A request for quotation not confirmed yet counts: computed again, the guide suggests
        0. An item sold by two suppliers is in the preferred one's guide only."""
        self.rule(self.salmon, 1000, 5000)
        buyer = self.mkuser("r1d_buyer", "bf_kitchen.group_kitchen_manager,purchase.group_purchase_user")
        guide = self.env["bf.kitchen.order.guide"].with_user(buyer).create({"partner_id": self.vendor.id})
        guide.action_compute()
        guide.action_create_rfq()
        again = self.env["bf.kitchen.order.guide"].with_user(buyer).create({"partner_id": self.vendor.id})
        again.action_compute()
        self.assertAlmostEqual(self.line(again, self.salmon).suggested, 0.0)
        self.assertAlmostEqual(self.line(again, self.salmon).in_progress, 5000.0)
        other = self.env["res.partner"].create({"name": "Autre poissonnerie", "bf_food_supplier": True})
        self.env["product.supplierinfo"].create({"partner_id": other.id, "product_tmpl_id": self.salmon.product_tmpl_id.id,
                                                 "price": 31.0, "sequence": 99})
        second = self.env["bf.kitchen.order.guide"].create({"partner_id": other.id})
        second.action_compute()
        self.assertFalse(self.line(second, self.salmon), "the salmon is ordered from its preferred supplier")

    def test_r1_rules_stay_manual_and_in_the_warehouse(self):
        """Under the kitchen scope, a rule is manual, has no route nor group, stays in the
        warehouse's stock, and the native Order button needs the Purchase rights."""
        chef = self.mkuser("r1r_chef", "bf_kitchen.group_kitchen_manager")
        Rule = self.env["stock.warehouse.orderpoint"].with_user(chef)
        rule = Rule.create({"product_id": self.salmon.id, "warehouse_id": self.warehouse.id,
                            "location_id": self.warehouse.lot_stock_id.id, "product_min_qty": 1, "product_max_qty": 2})
        self.assertEqual(rule.trigger, "manual", "never the native automatic default")
        route = self.env.ref("purchase_stock.route_warehouse0_buy")
        with self.assertRaises(AccessError):
            rule.write({"route_id": route.id})
        with self.assertRaises(AccessError):
            rule.write({"trigger": "auto"})
        with self.assertRaises(AccessError):
            rule.write({"location_id": self.env.ref("stock.stock_location_customers").id})
        with self.assertRaises(AccessError):
            Rule.create({"product_id": self.cream.id, "warehouse_id": self.warehouse.id, "route_id": route.id,
                         "location_id": self.warehouse.lot_stock_id.id})
        with self.assertRaises(AccessError):
            rule.action_replenish()
        with self.assertRaises(AccessError):
            rule.action_replenish_auto()

    def test_r1_nightly_cleanup_spares_kitchen_levels(self):
        """Odoo's nightly clean-up removes the manual rules it made once they need nothing:
        not a kitchen stock level."""
        rule = self.env["stock.warehouse.orderpoint"].sudo().create({
            "product_id": self.salmon.id, "warehouse_id": self.warehouse.id, "location_id": self.warehouse.lot_stock_id.id,
            "trigger": "manual", "product_min_qty": 0, "product_max_qty": 0})
        self.env.cr.execute("UPDATE stock_warehouse_orderpoint SET create_uid = 1 WHERE id = %s", [rule.id])
        rule.invalidate_recordset()
        rule.product_min_qty, rule.product_max_qty = 10.0, 20.0
        self.env["stock.quant"]._update_available_quantity(self.salmon, self.warehouse.lot_stock_id, 500.0)
        self.env["stock.warehouse.orderpoint"]._unlink_processed_orderpoints()
        self.assertTrue(rule.exists())
        rule.write({"product_min_qty": 0.0, "product_max_qty": 0.0})
        self.env["stock.warehouse.orderpoint"]._unlink_processed_orderpoints()
        self.assertFalse(rule.exists(), "a rule with no level is Odoo's to clean")

    def test_r1_guides_are_their_owner_s(self):
        """A kitchen manager does not read nor write another person's guide and its lines."""
        self.rule(self.salmon, 1000, 5000)
        one = self.mkuser("r1g_one", "bf_kitchen.group_kitchen_manager")
        two = self.mkuser("r1g_two", "bf_kitchen.group_kitchen_manager")
        guide = self.env["bf.kitchen.order.guide"].with_user(one).create({"partner_id": self.vendor.id})
        guide.action_compute()
        Line = self.env["bf.kitchen.order.guide.line"].with_user(two)
        self.assertFalse(Line.search([("guide_id", "=", guide.id)]))
        with self.assertRaises(AccessError):
            guide.line_ids[:1].with_user(two).write({"qty": 999.0})

    def test_r2_native_report_after_cleanup(self):
        """The clean-up returns only the rules really removed: a kept kitchen level is not
        handed to the native replenishment report as gone (it would make a twin rule)."""
        rule = self.rule(self.salmon, 10, 20)
        self.env.cr.execute("UPDATE stock_warehouse_orderpoint SET create_uid = 1 WHERE id = %s", [rule.id])
        rule.invalidate_recordset()
        self.env["stock.quant"]._update_available_quantity(self.salmon, self.warehouse.lot_stock_id, 500.0)
        removed = self.env["stock.warehouse.orderpoint"]._unlink_processed_orderpoints()
        self.assertNotIn(rule, removed)
        self.assertTrue(rule.exists())
        self.env["stock.warehouse.orderpoint"].with_context(search_default_filter_to_reorder=False).action_open_orderpoints()

    def test_r2_automatic_rules_are_the_stock_manager_s(self):
        """An automatic rule set by a stock manager is not the kitchen manager's to change."""
        auto = self.env["stock.warehouse.orderpoint"].create({
            "product_id": self.salmon.id, "warehouse_id": self.warehouse.id, "location_id": self.warehouse.lot_stock_id.id,
            "trigger": "auto", "product_min_qty": 1, "product_max_qty": 2})
        chef = self.mkuser("r2a_chef2", "bf_kitchen.group_kitchen_manager")
        with self.assertRaises(AccessError):
            auto.with_user(chef).write({"product_min_qty": 500.0, "product_max_qty": 1000.0})

    def test_r2_time_zone_of_the_kitchen(self):
        """A company without a time zone: the warehouse's address gives it, not the user's."""
        self.company.partner_id.tz = False
        self.warehouse.partner_id = self.env["res.partner"].create({"name": "Cuisine centrale", "tz": "America/Toronto"})
        self.env.user.tz = "Pacific/Auckland"
        guide = self.env["bf.kitchen.order.guide"].create({"partner_id": self.vendor.id})
        noon = guide._bf_local_noon(date(2026, 11, 3))
        self.assertEqual(noon.strftime("%Y-%m-%d %H"), "2026-11-03 17", "noon in Toronto (EST), in UTC")

    def test_r2_declared_allergen_on_an_ingredient(self):
        """An allergen an ingredient declares is neither archived nor deleted."""
        sesame = self.env.ref("bf_food_allergen.allergen_sesame")
        self.lemon.product_tmpl_id.bf_allergen_input_ids = [Command.set(sesame.ids)]
        with self.assertRaises(UserError):
            sesame.active = False
        with self.assertRaises(UserError):
            sesame.unlink()
