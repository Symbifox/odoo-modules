"""Sales and the theoretical/actual variance: the Kitchen consumption transfer every source uses,
and the variance report between two counts.

The fixtures are TestKitchen's (salmon in g, a sauce made of cream, a dish of 240 g of salmon, 30 g
of sauce and 3 g of salt). Odoo 18 runs only the test methods a class defines itself."""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .test_kitchen import TestKitchen


@tagged("post_install", "-at_install")
class TestKitchenVariance(TestKitchen):

    def warehouse(self):
        return self.env["stock.warehouse"].search([("company_id", "=", self.company.id)], limit=1)

    def put(self, product, qty):
        self.env["stock.quant"]._update_available_quantity(product, self.warehouse().lot_stock_id, qty)

    def consume(self, qty=1.0, product=None, event_date=None, source="other"):
        return self.env["stock.picking"]._bf_kitchen_consume(
            self.warehouse(), [((product or self.dish), qty, (product or self.dish).uom_id)], source, "Essai",
            event_date=event_date)

    def full_count(self, counted=None):
        """Count every location: an item of `counted` is set to its quantity, the others to what
        is expected (0 for a negative balance)."""
        counted = counted or {}
        count = self.env["bf.kitchen.count"].create({"warehouse_id": self.warehouse().id})
        count.action_start()
        for step in count.step_ids:
            for line in step.line_ids:
                qty = counted.get(line.product_id, max(line.expected_qty, 0.0))
                line.write({"qty_purchase": 0.0, "qty_stock": qty, "is_counted": True})
            step.action_apply()
        count.action_finish()
        return count

    def backdate(self, count, hours=2):
        """Put a count and every move made so far in the past: in a test, every date is now."""
        past = fields.Datetime.now() - timedelta(hours=hours)
        count.sudo().write({"date": past, "date_done": past})
        count.step_ids.sudo().write({"applied_date": past})
        self.env.flush_all()
        self.env.cr.execute("UPDATE stock_move SET date = %s WHERE date > %s", [past - timedelta(minutes=1), past])
        self.env.cr.execute("UPDATE stock_picking SET date_done = %s WHERE date_done > %s",
                            [past - timedelta(minutes=1), past])
        self.env.invalidate_all()

    def report(self, count_from, count_to):
        report = self.env["bf.kitchen.variance"].create({
            "warehouse_id": self.warehouse().id, "count_from_id": count_from.id, "count_to_id": count_to.id})
        report.action_compute()
        return report

    def rows(self, report, product, kind):
        return report.line_ids.filtered(lambda l: l.product_id == product and l.kind == kind)

    # ── the consumption transfer ──────────────────────────────────────────────────────

    def test_v1_consumption_explodes_the_dish(self):
        """A consumption of 2 dishes is one done transfer of the Kitchen consumption type: the dish
        is exploded into its ingredients (the sauce, a kit, into its cream), even with no stock."""
        picking = self.consume(2.0)
        self.assertEqual(picking.state, "done")
        ptype = self.warehouse().bf_kitchen_consumption_type_id
        self.assertEqual(picking.picking_type_id, ptype)
        self.assertEqual(ptype.code, "outgoing")
        self.assertEqual(picking.move_ids.product_id, self.salmon | self.cream | self.salt_g)
        by = {m.product_id: m.quantity for m in picking.move_ids}
        self.assertAlmostEqual(by[self.salmon], 480.0)
        self.assertAlmostEqual(by[self.cream], 60.0)
        self.assertAlmostEqual(by[self.salt_g], 6.0)
        self.assertAlmostEqual(self.salmon.with_context(location=self.warehouse().lot_stock_id.id).qty_available,
                               -480.0, msg="what is not in stock goes negative, as at the register")
        self.assertEqual(self.consume(1.0).picking_type_id, ptype, "one consumption type per warehouse")

    def test_v2_consumption_returned_once(self):
        """Putting a consumption back returns what is still out, once."""
        picking = self.consume(1.0)
        returns = picking._bf_kitchen_return()
        self.assertEqual(len(returns), 1)
        self.assertEqual(returns.state, "done")
        self.assertAlmostEqual(self.salmon.with_context(location=self.warehouse().lot_stock_id.id).qty_available, 0.0)
        self.assertFalse(picking._bf_kitchen_return(), "nothing left to return")

    def test_v3_source_set_by_the_kitchen_only(self):
        """The source and the day of a consumption are the server's: a stock user cannot forge them."""
        clerk = self.mkuser("v3_clerk", "stock.group_stock_manager")
        picking = self.consume(1.0)
        with self.assertRaises(AccessError):
            picking.with_user(clerk).write({"bf_kitchen_source": "invoice"})
        with self.assertRaises(AccessError):
            picking.with_user(clerk).write({"bf_kitchen_event_date": fields.Date.today()})
        wh = self.warehouse()
        with self.assertRaises(AccessError):
            self.env["stock.picking"].with_user(clerk).create({
                "picking_type_id": wh.out_type_id.id, "bf_kitchen_source": "other"})

    # ── the variance report ───────────────────────────────────────────────────────────

    def test_v4_variance_between_two_counts(self):
        """Between two counts: what was bought, consumed by sales, lost (by reason) and what the
        second count adjusted, the unexplained variance, valued at kitchen cost."""
        self.put(self.salmon, 1000.0)
        self.put(self.cream, 500.0)
        first = self.full_count()
        self.backdate(first)
        # bought: 2 kg at 30 $/kg, received from the bill (0.03 $/g)
        move = self.bill([(self.salmon, 2.0, 30.0, {"product_uom_id": self.kg.id})], post=False)
        move.action_bf_kitchen_receive()
        move.action_post()
        # sold: 3 dishes (720 g of salmon, 90 g of cream, 9 g of salt)
        self.consume(3.0)
        # lost: 100 g of salmon spilled
        loss = self.env["bf.kitchen.loss"].create({
            "product_id": self.salmon.id, "quantity": 100.0,
            "reason_id": self.env.ref("bf_kitchen.scrap_reason_spilled").id})
        loss.action_declare()
        # counted: 50 g of salmon missing (expected 1000 + 2000 - 720 - 100 = 2180)
        second = self.full_count({self.salmon: 2130.0})
        report = self.report(first, second)
        rows = lambda kind: self.rows(report, self.salmon, kind)
        self.assertAlmostEqual(sum(rows("purchase").mapped("qty")), 2000.0)
        self.assertAlmostEqual(sum(rows("consumption").mapped("qty")), 720.0)
        self.assertAlmostEqual(sum(rows("loss").mapped("qty")), 100.0)
        self.assertEqual(rows("loss").reason, self.env.ref("bf_kitchen.scrap_reason_spilled").name)
        self.assertAlmostEqual(sum(rows("unexplained").mapped("qty")), -50.0)
        self.assertAlmostEqual(sum(rows("unexplained").mapped("value")), -1.5)
        self.assertAlmostEqual(report.purchase_value, 60.0)
        self.assertAlmostEqual(report.unexplained_value, -1.5 + sum(
            report.line_ids.filtered(lambda l: l.kind == "unexplained" and l.product_id != self.salmon)
            .mapped("value")))
        self.assertIn(self.salmon, report.top_line_ids.product_id)
        self.assertAlmostEqual(report.unexplained_pct, 100.0 * report.unexplained_value / 60.0)
        self.assertNotIn(self.dish, report.line_ids.product_id, "a dish is a kit: its ingredients move")
        self.assertFalse(report.warning_html)
        # the moves of the first count and before it are not in the period
        self.assertFalse(self.rows(report, self.cream, "unexplained"),
                         "the cream was counted as expected and not adjusted since")

    def test_v5_late_consumption_warned(self):
        """A consumption recorded after the first count for a day before it is flagged: the first
        count already saw that food gone."""
        first = self.full_count()
        self.backdate(first, hours=30)
        late = self.consume(1.0, event_date=fields.Date.today() - timedelta(days=3))
        second = self.full_count()
        report = self.report(first, second)
        self.assertIn(late.name, report.warning_html)

    def test_v6_report_refused(self):
        """The report is the kitchen manager's; the counts are in the right order."""
        first = self.full_count()
        self.backdate(first)
        second = self.full_count()
        cook = self.mkuser("v6_cook", "bf_kitchen.group_kitchen_cook")
        with self.assertRaises(AccessError):
            self.env["bf.kitchen.variance"].with_user(cook).create({"warehouse_id": self.warehouse().id})
        report = self.env["bf.kitchen.variance"].create({
            "warehouse_id": self.warehouse().id, "count_from_id": second.id, "count_to_id": first.id})
        with self.assertRaises(UserError):
            report.action_compute()
        defaults = self.env["bf.kitchen.variance"].with_context(
            default_warehouse_id=self.warehouse().id).default_get(["count_from_id", "count_to_id", "warehouse_id"])
        self.assertEqual((defaults["count_from_id"], defaults["count_to_id"]), (first.id, second.id),
                         "the last two counts by default")

    def test_v7_preparations_made_and_used(self):
        """A batch of a preparation held in stock: its ingredients are used, the preparation made."""
        self.sauce_bom.action_bf_hold_in_stock()
        first = self.full_count()
        self.backdate(first)
        prepared = self.env["bf.kitchen.prepared"].create({"bom_id": self.sauce_bom.id, "quantity": 200.0})
        prepared.action_done()
        second = self.full_count()
        report = self.report(first, second)
        self.assertAlmostEqual(sum(self.rows(report, self.cream, "used").mapped("qty")), 200.0)
        self.assertAlmostEqual(sum(self.rows(report, self.sauce, "made").mapped("qty")), 200.0)

    def test_v8_loss_reasons_without_french(self):
        """A database without French installs the loss reasons: no search in a language it does not have."""
        Tag = self.env["stock.scrap.reason.tag"]
        expired = self.env.ref("bf_kitchen.scrap_reason_expired")
        self.env["ir.model.data"].search([("module", "=", "bf_kitchen"), ("name", "=", "scrap_reason_expired")]).unlink()
        expired.with_context(lang="en_US").name = "Old stock"
        Lang = type(self.env["res.lang"])
        real = Lang._lang_get
        with patch.object(Lang, "_lang_get", lambda lang, code: lang.browse() if code == "fr_CA" else real(lang, code)):
            Tag._bf_kitchen_create_reasons()
            self.assertTrue(Tag._bf_kitchen_reason("expired"))

    def test_v9_variance_from_a_count(self):
        """From a finished count, the variance since the previous one, computed."""
        first = self.full_count()
        self.backdate(first)
        second = self.full_count()
        action = second.action_bf_variance()
        report = self.env["bf.kitchen.variance"].browse(action["res_id"])
        self.assertEqual((report.count_from_id, report.count_to_id, report.computed), (first, second, True))
        cook = self.mkuser("v9_cook", "bf_kitchen.group_kitchen_cook")
        with self.assertRaises(AccessError):
            second.with_user(cook).action_bf_variance()

    def test_r1_warning_names_escaped(self):
        """A transfer name is anyone's to write: the report's warning escapes it."""
        first = self.full_count()
        self.backdate(first, hours=30)
        late = self.consume(1.0, event_date=fields.Date.today() - timedelta(days=3))
        clerk = self.mkuser("r1b1_clerk", "bf_kitchen.group_kitchen_cook")
        late.with_user(clerk).name = '<img src=x onerror="alert(1)">'
        second = self.full_count()
        report = self.report(first, second)
        self.assertNotIn("<img", str(report.warning_html))
        self.assertIn("&lt;img", str(report.warning_html))

    def test_r1_stock_location_closes_the_count(self):
        """Once the stock location is applied, no other location of the count is."""
        wh = self.warehouse()
        wh._bf_kitchen_create_locations()
        walk = self.env["stock.location"].search([("warehouse_id", "=", wh.id), ("bf_kitchen_key", "=", "walk_in")])
        self.env["stock.quant"]._update_available_quantity(self.salmon, walk, 1000.0)
        count = self.env["bf.kitchen.count"].create({"warehouse_id": wh.id})
        count.action_start()
        root = count.step_ids.filtered("is_stock_root")
        for line in root.line_ids:
            line.write({"qty_stock": 0.0, "is_counted": True})
        root.action_apply()
        step = count.step_ids.filtered(lambda s: s.location_id == walk)
        step.line_ids.write({"qty_stock": 500.0, "is_counted": True})
        with self.assertRaises(UserError):
            step.action_apply()

    def test_r1_server_fields_not_by_context(self):
        """A `default_*` of the context does not set the kitchen source of a transfer."""
        cook = self.mkuser("r1i2_cook", "bf_kitchen.group_kitchen_cook")
        wh = self.warehouse()
        picking = self.env["stock.picking"].with_user(cook).with_context(
            default_bf_kitchen_source="invoice", default_bf_kitchen_event_date="2020-01-01").create(
            {"picking_type_id": wh.out_type_id.id})
        self.assertFalse(picking.bf_kitchen_source)
        self.assertFalse(picking.bf_kitchen_event_date)

    def test_r1_returns_keep_their_source(self):
        """A returned consumption keeps its source and day: the report nets it there."""
        picking = self.consume(1.0, source="other", event_date=fields.Date.today())
        ret = picking._bf_kitchen_return()
        self.assertEqual((ret.bf_kitchen_source, ret.bf_kitchen_event_date), ("other", fields.Date.today()))

    def test_r1_kitchen_warehouse_setting(self):
        """The kitchen warehouse of the company, when set, is the one used."""
        # A second warehouse turns the multi-warehouse group on through the settings, which wake
        # modules of the test base that call the network: not the kitchen's business here.
        with patch.object(type(self.env["stock.warehouse"]), "_check_multiwarehouse_group", lambda wh: None):
            other = self.env["stock.warehouse"].create({"name": "Deuxième site", "code": "SITE2"})
        self.assertEqual(self.company._bf_kitchen_warehouse(), self.warehouse())
        self.company.bf_kitchen_warehouse_id = other
        self.assertEqual(self.company._bf_kitchen_warehouse(), other)

    def test_r2_kitchen_warehouse_everywhere(self):
        """The kitchen warehouse set for the company is the one of a new count, a new
        report, a loss of an item not put away, and a batch of a preparation."""
        with patch.object(type(self.env["stock.warehouse"]), "_check_multiwarehouse_group", lambda wh: None):
            other = self.env["stock.warehouse"].create({"name": "Cuisine centrale", "code": "CENT"})
        self.company.bf_kitchen_warehouse_id = other
        self.assertEqual(self.env["bf.kitchen.count"].create({}).warehouse_id, other)
        self.assertEqual(self.env["bf.kitchen.variance"].create({}).warehouse_id, other)
        loss = self.env["bf.kitchen.loss"].new({"product_id": self.salmon.id, "quantity": 1.0})
        self.assertEqual(loss.location_id, other.lot_stock_id)
        self.sauce_bom.action_bf_hold_in_stock()
        self.env["bf.kitchen.prepared"].create({"bom_id": self.sauce_bom.id, "quantity": 100.0}).action_done()
        production = self.env["mrp.production"].search([("bom_id", "=", self.sauce_bom.id)], limit=1)
        self.assertEqual(production.picking_type_id.warehouse_id, other)
