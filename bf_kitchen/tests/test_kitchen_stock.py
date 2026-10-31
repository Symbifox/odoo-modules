"""Stock of the kitchen: locations, counts, losses, preparations held in stock.

The fixtures are TestKitchen's (salmon in g, a sauce made of cream, a dish made of salmon, sauce
and salt). Odoo 18 runs only the test methods a class defines itself: TestKitchen's are not run
twice."""
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .test_kitchen import TestKitchen


@tagged("post_install", "-at_install")
class TestKitchenStock(TestKitchen):

    # ── helpers ─────────────────────────────────────────────────────────────────────

    def warehouse(self):
        return self.env["stock.warehouse"].search([("company_id", "=", self.company.id)], limit=1)

    def kitchen_locations(self):
        wh = self.warehouse()
        wh._bf_kitchen_create_locations()
        Location = self.env["stock.location"]
        return {key: Location.search([("location_id", "=", wh.lot_stock_id.id), ("bf_kitchen_key", "=", key)])
                for key in ("walk_in", "freezer", "dry", "bar")}

    def put(self, product, location, qty):
        self.env["stock.quant"]._update_available_quantity(product, location, qty)

    def qty_at(self, product, location):
        product.invalidate_recordset()
        return product.with_context(location=location.id, strict=True).qty_available

    def new_count(self):
        return self.env["bf.kitchen.count"].create({"warehouse_id": self.warehouse().id})

    # ── locations and reasons ───────────────────────────────────────────────────────

    def test_s1_kitchen_locations_once(self):
        """The four kitchen locations are made under the stock, once, in the walking order."""
        locs = self.kitchen_locations()
        self.assertTrue(all(len(loc) == 1 for loc in locs.values()))
        self.assertEqual([locs[k].bf_kitchen_count_sequence for k in ("walk_in", "freezer", "dry", "bar")],
                         [10, 20, 30, 40])
        locs["walk_in"].name = "Chambre 2"
        self.assertFalse(self.warehouse()._bf_kitchen_create_locations(), "a renamed location is not made again")

    def test_s2_loss_reasons_reused(self):
        """The loss reasons reuse a tag of the same name instead of failing on its unique name."""
        self.env["res.lang"]._activate_lang("fr_CA")
        Tag = self.env["stock.scrap.reason.tag"]
        for key in ("expired", "cooking", "customer_return", "spilled", "staff_meal", "tasting"):
            self.assertTrue(self.env.ref(f"bf_kitchen.scrap_reason_{key}", raise_if_not_found=False), key)
        count = Tag.search_count([])
        Tag._bf_kitchen_create_reasons()
        self.assertEqual(Tag.search_count([]), count, "idempotent")
        tasting = self.env.ref("bf_kitchen.scrap_reason_tasting")
        self.env["ir.model.data"].search([("module", "=", "bf_kitchen"),
                                          ("name", "=", "scrap_reason_tasting")]).unlink()
        tasting.update_field_translations("name", {"fr_CA": "Goûter du chef"})
        Tag._bf_kitchen_create_reasons()
        self.assertEqual(Tag.search_count([]), count, "an existing 'Tasting' tag is reused, not duplicated")
        self.assertFalse(self.env.ref("bf_kitchen.scrap_reason_tasting", raise_if_not_found=False),
                         "no xmlid of ours on the database's own tag")
        self.assertEqual(tasting.with_context(lang="fr_CA").name, "Goûter du chef", "its translation is kept")
        self.assertEqual(Tag._bf_kitchen_reason("tasting"), tasting)

    # ── losses ──────────────────────────────────────────────────────────────────────

    def declare(self, user, product, qty, uom=None, reason="expired", location=None):
        Loss = self.env["bf.kitchen.loss"].with_user(user)
        vals = {"product_id": product.id, "quantity": qty,
                "reason_id": self.env.ref(f"bf_kitchen.scrap_reason_{reason}").id}
        if uom:
            vals["product_uom_id"] = uom.id
        if location:
            vals["location_id"] = location.id
        loss = Loss.create(vals)
        loss.action_declare()
        return self.env["stock.scrap"].search([("product_id", "=", product.id)], order="id desc", limit=1)

    def test_s3_loss_of_an_ingredient(self):
        """A cook declares a loss even with no stock: the scrap is done, the stock goes below,
        the loss carries its reason and its value at kitchen cost (for the manager)."""
        self.bill([(self.salmon, 1000.0, 0.03, {})])
        cook = self.mkuser("s3_cook", "bf_kitchen.group_kitchen_cook")
        before = self.on_hand(self.salmon)
        scrap = self.declare(cook, self.salmon, 0.5, uom=self.kg, reason="spilled")
        self.assertEqual(scrap.state, "done")
        self.assertTrue(scrap.bf_is_kitchen)
        self.assertEqual(scrap.scrap_reason_tag_ids, self.env.ref("bf_kitchen.scrap_reason_spilled"))
        self.assertAlmostEqual(self.on_hand(self.salmon), before - 500.0)
        self.assertAlmostEqual(scrap.sudo().bf_kitchen_cost, 15.0)
        with self.assertRaises(AccessError):
            scrap.with_user(cook).read(["bf_kitchen_cost"])

    def test_s4_loss_of_a_dish_is_exploded(self):
        """A dish lost leaves the stock as its ingredients (a kit), not as a dish."""
        cook = self.mkuser("s4_cook", "bf_kitchen.group_kitchen_cook")
        salmon, salt = self.on_hand(self.salmon), self.on_hand(self.salt_g)
        scrap = self.declare(cook, self.dish, 2.0, reason="cooking")
        self.assertEqual(scrap.state, "done")
        self.assertIn(self.salmon, scrap.move_ids.product_id)
        self.assertNotIn(self.dish, scrap.move_ids.product_id)
        self.assertAlmostEqual(self.on_hand(self.salmon), salmon - 2 * 240.0, msg="gross quantity, twice")
        self.assertAlmostEqual(self.on_hand(self.salt_g), salt - 6.0)

    def test_s5_loss_refused(self):
        """Only the kitchen declares a loss, of a positive quantity, in a unit of the item's kind."""
        clerk = self.mkuser("s5_clerk", "stock.group_stock_user")
        with self.assertRaises(AccessError):
            self.declare(clerk, self.salmon, 1.0)
        cook = self.mkuser("s5_cook", "bf_kitchen.group_kitchen_cook")
        with self.assertRaises(UserError):
            self.declare(cook, self.salmon, 0.0)
        with self.assertRaises(UserError):
            self.declare(cook, self.salmon, 1.0, uom=self.unit)

    # ── counts ──────────────────────────────────────────────────────────────────────

    def test_s6_count_walks_every_location(self):
        """The steps: the kitchen locations in their order, the stock location last. A location
        lists the items that have stock there, negative included, and the items stored there."""
        locs = self.kitchen_locations()
        root = self.warehouse().lot_stock_id
        self.put(self.salmon, locs["walk_in"], 2000.0)
        self.put(self.salt_g, root, -30.0)
        self.lemon.product_tmpl_id.with_company(self.company).bf_kitchen_location_id = locs["dry"]
        count = self.new_count()
        count.action_start()
        steps = count.step_ids
        self.assertEqual(steps[:4].location_id, locs["walk_in"] | locs["freezer"] | locs["dry"] | locs["bar"])
        self.assertEqual(steps[-1].location_id, root)
        walk_in = steps.filtered(lambda s: s.location_id == locs["walk_in"])
        self.assertIn(self.salmon, walk_in.line_ids.product_id)
        root_step = steps[-1]
        salt_line = root_step.line_ids.filtered(lambda l: l.product_id == self.salt_g)
        self.assertAlmostEqual(salt_line.expected_qty, -30.0, msg="a negative balance is listed")
        dry = steps.filtered(lambda s: s.location_id == locs["dry"])
        self.assertIn(self.lemon, dry.line_ids.product_id, "an item stored here, even at zero")

    def test_s7_apply_one_location(self):
        """Applying a location sets its stock to the count (whole purchase units plus a part),
        keeps the variance and its value, and leaves an item not entered as it was."""
        locs = self.kitchen_locations()
        self.bill([(self.salmon, 1000.0, 0.02, {"product_uom_id": self.g.id})])
        self.salmon.product_tmpl_id.uom_po_id = self.kg
        self.put(self.salmon, locs["walk_in"], 3000.0)
        self.put(self.cream, locs["walk_in"], 500.0)
        cook = self.mkuser("s7_cook", "bf_kitchen.group_kitchen_cook")
        count = self.new_count().with_user(cook)
        count.action_start()
        step = count.step_ids.filtered(lambda s: s.location_id == locs["walk_in"])
        salmon = step.line_ids.filtered(lambda l: l.product_id == self.salmon)
        self.assertEqual(salmon.purchase_uom_id, self.kg)
        salmon.write({"qty_purchase": 2.0, "qty_stock": 400.0})
        self.assertAlmostEqual(salmon.counted_qty, 2400.0)
        step.action_apply()
        self.assertEqual(step.state, "applied")
        self.assertAlmostEqual(self.qty_at(self.salmon, locs["walk_in"]), 2400.0)
        self.assertAlmostEqual(self.qty_at(self.cream, locs["walk_in"]), 500.0, msg="not entered, not touched")
        self.assertAlmostEqual(salmon.diff_qty, -600.0)
        self.assertAlmostEqual(salmon.sudo().diff_value, -12.0)
        moves = self.env["stock.move"].search([("product_id", "=", self.salmon.id), ("is_inventory", "=", True)])
        self.assertTrue(moves, "an inventory adjustment, as Odoo makes them")
        with self.assertRaises(UserError):
            salmon.qty_stock = 1.0

    def test_s8_finish_needs_the_stock_location(self):
        """A count does not finish while the stock location is not applied: the stock that sales and
        receipts left there would count twice. Counting 0 there resolves a negative balance."""
        self.kitchen_locations()
        root = self.warehouse().lot_stock_id
        self.put(self.salt_g, root, -30.0)
        count = self.new_count()
        count.action_start()
        with self.assertRaises(UserError):
            count.action_finish()
        root_step = count.step_ids[-1]
        root_step.line_ids.filtered(lambda l: l.product_id == self.salt_g).action_zero()
        root_step.action_apply()
        self.assertAlmostEqual(self.qty_at(self.salt_g, root), 0.0)
        count.action_finish()
        self.assertEqual(count.state, "done")
        self.assertTrue(all(s.state in ("applied", "skipped") for s in count.step_ids))
        with self.assertRaises(UserError):
            count.unlink()

    def test_s9_count_refused_outside_the_kitchen(self):
        """Only the kitchen starts a count and enters it; nothing is applied twice."""
        clerk = self.mkuser("s9_clerk", "stock.group_stock_user")
        with self.assertRaises(AccessError):
            self.new_count().with_user(clerk).action_start()
        locs = self.kitchen_locations()
        self.put(self.salt_g, locs["walk_in"], 100.0)
        count = self.new_count()
        count.action_start()
        step = count.step_ids.filtered(lambda s: s.location_id == locs["walk_in"])
        with self.assertRaises(UserError):
            step.action_apply()   # an item listed, nothing entered
        step.action_skip()
        with self.assertRaises(UserError):
            step.action_apply()

    def test_s10_count_reminder(self):
        """The reminder prepares one count for the kitchen manager with an activity, and waits
        while it is open or while the last count is recent."""
        chef = self.mkuser("s10_chef", "bf_kitchen.group_kitchen_manager")
        self.company.write({"bf_kitchen_count_frequency": "weekly", "bf_kitchen_manager_id": chef.id})
        Count = self.env["bf.kitchen.count"]
        Count._cron_bf_kitchen_count_reminder()
        made = Count.search([("company_id", "=", self.company.id)])
        self.assertEqual(len(made), 1)
        self.assertEqual(made.activity_ids.user_id, chef)
        Count._cron_bf_kitchen_count_reminder()
        self.assertEqual(Count.search_count([("company_id", "=", self.company.id)]), 1, "one open count")
        made.sudo().write({"state": "done", "date_done": fields.Datetime.now() - timedelta(days=2)})
        Count._cron_bf_kitchen_count_reminder()
        self.assertEqual(Count.search_count([("company_id", "=", self.company.id)]), 1, "recent count")
        made.sudo().date_done = fields.Datetime.now() - timedelta(days=8)
        Count._cron_bf_kitchen_count_reminder()
        self.assertEqual(Count.search_count([("company_id", "=", self.company.id)]), 2)

    def test_s11_count_warns_of_pending_transfers(self):
        """Before counting, the screen says that transfers of the warehouse are not validated."""
        wh = self.warehouse()
        picking = self.env["stock.picking"].create({
            "picking_type_id": wh.out_type_id.id, "location_id": wh.lot_stock_id.id,
            "location_dest_id": self.env.ref("stock.stock_location_customers").id,
            "move_ids": [Command.create({"name": "x", "product_id": self.salmon.id, "product_uom_qty": 10,
                                         "product_uom": self.g.id, "location_id": wh.lot_stock_id.id,
                                         "location_dest_id": self.env.ref("stock.stock_location_customers").id})]})
        picking.action_confirm()
        count = self.new_count()
        self.assertRegex(str(count.warning_html or ""), r"1 trans\w+.*(not validated|pas validés)")
        picking.action_cancel()
        count.invalidate_recordset()
        self.assertFalse(count.warning_html, "nothing pending, no warning")

    # ── preparations held in stock ──────────────────────────────────────────────────

    def test_s12_preparation_held_in_stock(self):
        """A manager holds a preparation in stock; a cook records a batch: the ingredients leave,
        the preparation enters. The dish keeps its plate cost."""
        self.bill([(self.cream, 1000.0, 0.01, {})])
        self.assertFalse(self.sauce.is_storable, "a preparation is made as a kit, not kept in stock")
        plate = self.dish_bom.bf_cost_portion
        chef = self.mkuser("s12_chef", "bf_kitchen.group_kitchen_manager")
        cook = self.mkuser("s12_cook", "bf_kitchen.group_kitchen_cook")
        with self.assertRaises(AccessError):
            self.sauce_bom.with_user(cook).action_bf_hold_in_stock()
        with self.assertRaises(UserError):
            self.dish_bom.with_user(chef).action_bf_hold_in_stock()
        self.sauce_bom.with_user(chef).action_bf_hold_in_stock()
        self.assertEqual(self.sauce_bom.type, "normal")
        self.assertTrue(self.sauce.is_storable, "held in stock means kept in stock")
        self.dish_bom.invalidate_recordset()
        self.assertAlmostEqual(self.dish_bom.bf_cost_portion, plate, msg="the plate cost does not move")
        cream, sauce = self.on_hand(self.cream), self.on_hand(self.sauce)
        wizard = self.env["bf.kitchen.prepared"].with_user(cook).create(
            {"bom_id": self.sauce_bom.id, "quantity": 400.0})
        wizard.action_done()
        self.assertAlmostEqual(self.on_hand(self.sauce), sauce + 400.0)
        self.assertAlmostEqual(self.on_hand(self.cream), cream - 400.0)
        production = self.env["mrp.production"].search([("bom_id", "=", self.sauce_bom.id)])
        self.assertEqual(production.state, "done")
        self.assertEqual(production.user_id, cook)
        with self.assertRaises(UserError):
            self.sauce_bom.with_user(chef).action_bf_make_on_the_fly()   # 400 g still in stock
        quants = self.env["stock.quant"].with_context(inventory_mode=True).search(
            [("product_id", "=", self.sauce.id), ("location_id.usage", "=", "internal")])
        quants.inventory_quantity = 0.0
        quants._apply_inventory()
        self.sauce_bom.with_user(chef).action_bf_make_on_the_fly()
        self.assertEqual(self.sauce_bom.type, "phantom")
        with self.assertRaises(UserError):
            self.env["bf.kitchen.prepared"].with_user(cook).create(
                {"bom_id": self.sauce_bom.id, "quantity": 100.0}).action_done()

    def test_s13_prepared_refused(self):
        """Recording a batch takes the kitchen, a positive quantity, a unit of the same kind."""
        self.sauce_bom.action_bf_hold_in_stock()
        clerk = self.mkuser("s13_clerk", "stock.group_stock_user")
        with self.assertRaises(AccessError):
            self.env["bf.kitchen.prepared"].with_user(clerk).create(
                {"bom_id": self.sauce_bom.id, "quantity": 10.0}).action_done()
        cook = self.mkuser("s13_cook", "bf_kitchen.group_kitchen_cook")
        with self.assertRaises(UserError):
            self.env["bf.kitchen.prepared"].with_user(cook).create(
                {"bom_id": self.sauce_bom.id, "quantity": 0.0}).action_done()
        with self.assertRaises(UserError):
            self.env["bf.kitchen.prepared"].with_user(cook).create(
                {"bom_id": self.sauce_bom.id, "quantity": 1.0, "product_uom_id": self.unit.id}).action_done()

    # ── guards ───────────────────────────────────────────────────────────────────────

    def test_r1_count_state_not_forged(self):
        """The count's states and links are the count's own: a cook cannot set them by RPC."""
        self.kitchen_locations()
        cook = self.mkuser("l2r1_cook", "bf_kitchen.group_kitchen_cook")
        count = self.new_count().with_user(cook)
        with self.assertRaises(AccessError):
            self.env["bf.kitchen.count"].with_user(cook).create({"warehouse_id": self.warehouse().id, "state": "done"})
        count.action_start()
        step = count.step_ids[0]
        with self.assertRaises(AccessError):
            count.write({"state": "draft"})
        with self.assertRaises(AccessError):
            step.write({"state": "applied"})
        with self.assertRaises(AccessError):
            step.write({"location_id": self.env.ref("stock.stock_location_customers").id})
        with self.assertRaises(AccessError):
            self.env["bf.kitchen.count.step"].with_user(cook).create(
                {"count_id": count.id, "location_id": self.warehouse().lot_stock_id.id})
        line = count.step_ids[-1].line_ids[:1] or self.env["bf.kitchen.count.line"].with_user(cook).create(
            {"step_id": count.step_ids[-1].id, "product_id": self.salt_g.id})
        for field in ("qty_before", "diff_qty"):
            with self.assertRaises(AccessError):
                line.write({field: 1})
        with self.assertRaises(UserError):
            count.write({"warehouse_id": self.warehouse().id})
        with self.assertRaises(UserError):
            count.unlink()

    def test_r1_stock_location_never_skipped(self):
        """The stock location cannot be skipped, and a count finishes only once it is applied."""
        self.kitchen_locations()
        count = self.new_count()
        count.action_start()
        root_step = count.step_ids[-1]
        self.assertTrue(root_step.is_stock_root)
        with self.assertRaises(UserError):
            root_step.action_skip()
        count.step_ids[0].action_skip()
        with self.assertRaises(UserError):
            count.action_finish()

    def test_r1_tracked_items_left_to_odoo(self):
        """An item tracked by lot is not counted here: its stock in a lot would be read
        as expected and set again without lot (doubled)."""
        locs = self.kitchen_locations()
        tracked = self.ingredient("Fromage au lot", self.g, tracking="lot")
        lot = self.env["stock.lot"].create({"name": "L1", "product_id": tracked.id, "company_id": self.company.id})
        self.env["stock.quant"]._update_available_quantity(tracked, locs["walk_in"], 2000.0, lot_id=lot)
        count = self.new_count()
        count.action_start()
        walk_in = count.step_ids.filtered(lambda s: s.location_id == locs["walk_in"])
        self.assertNotIn(tracked, walk_in.line_ids.product_id)
        with self.assertRaises(UserError):
            self.env["bf.kitchen.count.line"].create({"step_id": walk_in.id, "product_id": tracked.id})
        self.put(self.salmon, locs["walk_in"], 500.0)
        lot_s = self.env["stock.lot"].create({"name": "S1", "product_id": self.salmon.id, "company_id": self.company.id})
        self.env["stock.quant"]._update_available_quantity(self.salmon, locs["walk_in"], 300.0, lot_id=lot_s)
        line = self.env["bf.kitchen.count.line"].create({"step_id": walk_in.id, "product_id": self.salmon.id}) \
            if self.salmon not in walk_in.line_ids.product_id else walk_in.line_ids.filtered(
                lambda l: l.product_id == self.salmon)
        self.assertAlmostEqual(line.expected_qty, 500.0, msg="the expected stock is the stock the count sets")

    def test_r1_hold_rechecks_portions(self):
        """A preparation held in stock is no longer exploded at the sale: a dish taking a
        few grams of a stock kept in kg is refused, as it would be for an ingredient."""
        Product, Bom = self.env["product.product"], self.env["mrp.bom"]
        fond = Product.create({"name": "Fond (kg)", "type": "consu", "bf_kitchen_type": "preparation",
                               "uom_id": self.kg.id, "uom_po_id": self.kg.id, "sale_ok": False})
        fond_bom = Bom.create({"product_tmpl_id": fond.product_tmpl_id.id, "type": "phantom", "product_qty": 1.0,
                               "product_uom_id": self.kg.id,
                               "bom_line_ids": [Command.create({"product_id": self.salt_g.id, "product_qty": 1000.0,
                                                                "product_uom_id": self.g.id})]})
        plat = Product.create({"name": "Plat au fond", "type": "consu", "bf_kitchen_type": "dish",
                               "list_price": 10.0, "uom_id": self.unit.id, "uom_po_id": self.unit.id})
        Bom.create({"product_tmpl_id": plat.product_tmpl_id.id, "type": "phantom", "product_qty": 1.0,
                    "product_uom_id": self.unit.id,
                    "bom_line_ids": [Command.create({"product_id": fond.id, "product_qty": 4.0,
                                                     "product_uom_id": self.g.id})]})
        with self.assertRaises(ValidationError):
            fond_bom.action_bf_hold_in_stock()

    def test_r1_dish_type_not_switched_by_hand(self):
        """A kitchen manager does not switch a dish (or a preparation) by writing its type."""
        chef = self.mkuser("l2r1_chef", "bf_kitchen.group_kitchen_manager")
        with self.assertRaises(UserError):
            self.dish_bom.with_user(chef).write({"type": "normal"})
        with self.assertRaises(UserError):
            self.sauce_bom.with_user(chef).write({"type": "normal"})

    def test_r1_prepared_needs_stock(self):
        """A batch is recorded only for a preparation kept in stock."""
        self.sauce_bom.sudo().write({"type": "normal"})
        self.assertFalse(self.sauce.is_storable)
        with self.assertRaises(UserError):
            self.env["bf.kitchen.prepared"].create({"bom_id": self.sauce_bom.id, "quantity": 10.0}).action_done()

    def test_r1_locations_only_for_kitchens(self):
        """Install and migration make the kitchen locations only for a company with
        a kitchen recipe; a location moved within its warehouse is not made again."""
        other = self.env["res.company"].create({"name": "Pas une cuisine"})
        other_wh = self.env["stock.warehouse"].search([("company_id", "=", other.id)], limit=1)
        self.env["stock.warehouse"]._bf_kitchen_create_all_locations()
        Location = self.env["stock.location"]
        self.assertEqual(Location.search_count([("warehouse_id", "=", self.warehouse().id),
                                                ("bf_kitchen_key", "!=", False)]), 4)
        if other_wh:
            self.assertFalse(Location.search_count([("warehouse_id", "=", other_wh.id), ("bf_kitchen_key", "!=", False)]))
        locs = self.kitchen_locations()
        locs["bar"].location_id = locs["dry"]
        self.assertFalse(self.warehouse()._bf_kitchen_create_locations())

    def test_r1_native_scrap_valued(self):
        """A loss recorded from Odoo's own scrap is valued at kitchen cost too."""
        self.bill([(self.salmon, 1000.0, 0.03, {})])
        scrap = self.env["stock.scrap"].create({"product_id": self.salmon.id, "scrap_qty": 100.0,
                                                "product_uom_id": self.g.id,
                                                "location_id": self.warehouse().lot_stock_id.id})
        scrap.do_scrap()
        self.assertAlmostEqual(scrap.sudo().bf_kitchen_cost, 3.0)

    def test_r1_kitchen_locations_menu_for_stock_admin(self):
        """Writing a location is the Inventory administrator's: the menu is theirs."""
        menu = self.env.ref("bf_kitchen.menu_kitchen_locations")
        self.assertEqual(menu.groups_id, self.env.ref("stock.group_stock_manager"))

    def test_s14_counted_at_zero(self):
        """An item marked counted with nothing entered is counted at 0 (entering 0 over 0 changes
        no field, the phone would send nothing)."""
        locs = self.kitchen_locations()
        self.put(self.cream, locs["walk_in"], 500.0)
        count = self.new_count()
        count.action_start()
        step = count.step_ids.filtered(lambda s: s.location_id == locs["walk_in"])
        cook = self.mkuser("s14_cook", "bf_kitchen.group_kitchen_cook")
        line = step.line_ids.filtered(lambda l: l.product_id == self.cream).with_user(cook)
        line.is_counted = True
        step.with_user(cook).action_apply()
        self.assertAlmostEqual(self.qty_at(self.cream, locs["walk_in"]), 0.0)

    # ── switching how a preparation is made, empty and doubled stock ─────────────────

    def test_r2_switch_waits_for_sales_under_way(self):
        """A dish sold and not delivered yet was exploded with the sauce as a kit: switching the sauce
        is refused while those moves are under way (Odoo reads the delivered quantity of a kit
        through them, and the sale would stay at 0 delivered, never invoiced)."""
        partner = self.env["res.partner"].create({"name": "Client essai"})
        order = self.env["sale.order"].create({"partner_id": partner.id, "order_line": [
            Command.create({"product_id": self.dish.id, "product_uom_qty": 5.0})]})
        order.action_confirm()
        self.assertTrue(order.order_line.move_ids)
        with self.assertRaises(UserError):
            self.sauce_bom.action_bf_hold_in_stock()
        self.assertEqual(self.sauce_bom.type, "phantom")
        self.assertFalse(self.sauce.is_storable, "refused: nothing changed")
        order._action_cancel()
        # Without any sale (a transfer, a register order): a move of the dish's recipe still to do.
        line = self.dish_bom.bom_line_ids.filtered(lambda l: l.product_id == self.salmon)
        wh = self.warehouse()
        move = self.env["stock.move"].create({
            "name": "Tartare", "product_id": self.salmon.id, "product_uom_qty": 120.0, "product_uom": self.g.id,
            "location_id": wh.lot_stock_id.id, "location_dest_id": self.env.ref("stock.stock_location_customers").id,
            "bom_line_id": line.id})
        move._action_confirm()
        with self.assertRaises(UserError):
            self.sauce_bom.action_bf_hold_in_stock()
        move._action_cancel()
        self.sauce_bom.action_bf_hold_in_stock()
        self.assertEqual(self.sauce_bom.type, "normal")

    def test_r2_back_on_the_fly_needs_no_stock(self):
        """Made on the fly again, a preparation still in stock would never leave it: refused until it
        is used up or counted to 0, then it is no longer kept in stock. A kit is never counted."""
        locs = self.kitchen_locations()
        self.bill([(self.cream, 1000.0, 0.01, {})])
        self.sauce_bom.action_bf_hold_in_stock()
        self.sauce.product_tmpl_id.bf_kitchen_location_id = locs["walk_in"]
        self.env["bf.kitchen.prepared"].create({"bom_id": self.sauce_bom.id, "quantity": 300.0}).action_done()
        with self.assertRaises(UserError):
            self.sauce_bom.action_bf_make_on_the_fly()
        quants = self.env["stock.quant"].with_context(inventory_mode=True).search(
            [("product_id", "=", self.sauce.id), ("location_id.usage", "=", "internal")])
        quants.inventory_quantity = 0.0
        quants._apply_inventory()
        self.sauce_bom.action_bf_make_on_the_fly()
        self.assertEqual(self.sauce_bom.type, "phantom")
        self.env.invalidate_all()   # is_kits is not stored, and its cache does not follow the type
        self.assertTrue(self.sauce.with_company(self.company).is_kits)
        count = self.new_count()
        count.action_start()
        walk_in = count.step_ids.filtered(lambda s: s.location_id == locs["walk_in"])
        self.assertNotIn(self.sauce, count.step_ids.line_ids.product_id)
        with self.assertRaises(UserError):
            self.env["bf.kitchen.count.line"].create({"step_id": walk_in.id, "product_id": self.sauce.id})

    def test_r2_empty_stock_location_applies(self):
        """A stock location with nothing listed is applied as it is (or the count could never end), and
        what reached it since the start is listed when it is opened."""
        wh2 = self.warehouse()
        self.env["stock.quant"].sudo().search([("location_id", "=", wh2.lot_stock_id.id)]).unlink()
        self.env["product.template"].sudo().with_company(self.company).search(
            [("bf_kitchen_location_id", "=", wh2.lot_stock_id.id)]).bf_kitchen_location_id = False
        first = self.env["bf.kitchen.count"].create({"warehouse_id": wh2.id})
        first.action_start()
        root = first.step_ids.filtered("is_stock_root")
        self.assertFalse(root.line_ids)
        root.action_apply()
        first.action_finish()
        self.assertEqual(first.state, "done")
        second = self.env["bf.kitchen.count"].create({"warehouse_id": wh2.id})
        second.action_start()
        root = second.step_ids.filtered("is_stock_root")
        self.put(self.salt_g, wh2.lot_stock_id, 50.0)
        root.action_open()
        self.assertIn(self.salt_g, root.line_ids.product_id, "listed when the location is opened")
        root.action_open()
        self.assertEqual(len(root.line_ids), 1, "listed once")

    def test_r2_doubled_quants_set_once(self):
        """Two quants of the same item at one location (Odoo merges them only daily): the expected
        stock is their sum, and the count sets the total, not one of them."""
        locs = self.kitchen_locations()
        Quant = self.env["stock.quant"].sudo()
        for qty in (300.0, 200.0):
            Quant.create({"product_id": self.cream.id, "location_id": locs["walk_in"].id, "quantity": qty})
        count = self.new_count()
        count.action_start()
        walk_in = count.step_ids.filtered(lambda s: s.location_id == locs["walk_in"])
        line = walk_in.line_ids.filtered(lambda l: l.product_id == self.cream)
        self.assertAlmostEqual(line.expected_qty, 500.0)
        line.qty_stock = 450.0
        walk_in.action_apply()
        self.assertAlmostEqual(self.qty_at(self.cream, locs["walk_in"]), 450.0)
        self.assertAlmostEqual(line.diff_qty, -50.0)

    def test_r2_defaults_not_forged(self):
        """A default passed in the context sets nothing the count writes itself."""
        self.kitchen_locations()
        cook = self.mkuser("r2_cook", "bf_kitchen.group_kitchen_cook")
        Count = self.env["bf.kitchen.count"].with_user(cook).with_context(
            default_state="done", default_date_done=fields.Datetime.now())
        count = Count.create({"warehouse_id": self.warehouse().id})
        self.assertEqual(count.state, "draft")
        self.assertFalse(count.date_done)
        count.with_context(default_state="applied").action_start()
        self.assertEqual(set(count.step_ids.mapped("state")), {"to_count"})

    def test_r2_french_named_reason_found(self):
        """A loss reason the database already has under its French name only is the one used."""
        self.env["res.lang"]._activate_lang("fr_CA")
        Tag = self.env["stock.scrap.reason.tag"]
        self.env.ref("bf_kitchen.scrap_reason_expired").unlink()
        theirs = Tag.with_context(lang="fr_CA").create({"name": "Périmé"})
        self.assertEqual(Tag._bf_kitchen_reason("expired"), theirs)

    def test_r2_kitchen_recipe_born_a_kit(self):
        """A dish recipe made as a normal bill of materials (the Manufacturing default) is made a kit:
        otherwise its ingredients would never leave the stock."""
        chef = self.mkuser("r2_chef", "bf_kitchen.group_kitchen_manager")
        plat = self.env["product.product"].create({
            "name": "Plat normal", "type": "consu", "bf_kitchen_type": "dish", "list_price": 10.0,
            "uom_id": self.unit.id, "uom_po_id": self.unit.id})
        bom = self.env["mrp.bom"].with_user(chef).create({
            "product_tmpl_id": plat.product_tmpl_id.id, "type": "normal", "product_qty": 1.0,
            "product_uom_id": self.unit.id, "bom_line_ids": [
                Command.create({"product_id": self.salt_g.id, "product_qty": 3.0, "product_uom_id": self.g.id})]})
        self.assertEqual(bom.type, "phantom")
        self.assertEqual(self.dish_bom.with_user(chef).copy({"type": "normal"}).type, "phantom")

    def test_r2_storage_of_the_company(self):
        """An item is stored in a location of the company that keeps it."""
        other = self.env["res.company"].create({"name": "Autre cuisine"})
        other_wh = self.env["stock.warehouse"].search([("company_id", "=", other.id)], limit=1)
        with self.assertRaises(UserError):
            self.salmon.product_tmpl_id.with_company(self.company).bf_kitchen_location_id = other_wh.lot_stock_id

    def test_r2_one_count_at_a_time(self):
        """A second count of the same warehouse waits for the first: both would set the same stock."""
        self.kitchen_locations()
        self.new_count().action_start()
        with self.assertRaises(UserError):
            self.new_count().action_start()
