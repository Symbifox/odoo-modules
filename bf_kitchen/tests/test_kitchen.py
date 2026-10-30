from datetime import date, timedelta
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, new_test_user, tagged
from odoo.tools import mute_logger
from odoo.tools.safe_eval import safe_eval

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.bf_kitchen.models.account_move import AccountMove


@tagged("post_install", "-at_install")
class TestKitchen(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.company = env.company
        env.user.groups_id = [Command.link(env.ref("bf_kitchen.group_kitchen_manager").id)]
        cls.g = env.ref("uom.product_uom_gram")
        cls.kg = env.ref("uom.product_uom_kgm")
        cls.unit = env.ref("uom.product_uom_unit")
        cls.vendor = env["res.partner"].create({"name": "Poissonnerie Essai", "bf_food_supplier": True})
        Product = env["product.product"]

        def ingredient(name, uom, **kw):
            return Product.create(dict({
                "name": name, "type": "consu", "is_storable": True, "bf_kitchen_type": "ingredient",
                "uom_id": uom.id, "uom_po_id": uom.id, "sale_ok": False,
                "property_account_expense_id": cls.company_data["default_account_expense"].id,
                "supplier_taxes_id": [Command.clear()],
            }, **kw))

        cls.ingredient = staticmethod(ingredient)
        cls.salmon = ingredient("Saumon entier", cls.g, bf_yield_pct=50.0)
        cls.salt_g = ingredient("Sel (g)", cls.g)
        cls.salt_kg = ingredient("Sel (kg)", cls.kg)
        cls.lemon = ingredient("Citron", cls.unit, bf_allergen_input_ids=[])
        cls.cream = ingredient("Crème 35 %", cls.g, bf_allergen_input_ids=[
            Command.set(env.ref("bf_food_allergen.allergen_milk").ids)])
        cls.sauce = Product.create({"name": "Sauce citronnée", "type": "consu", "bf_kitchen_type": "preparation",
                                    "uom_id": cls.g.id, "uom_po_id": cls.g.id, "sale_ok": False})
        cls.dish = Product.create({"name": "Tartare de saumon", "type": "consu", "bf_kitchen_type": "dish",
                                   "list_price": 20.0, "uom_id": cls.unit.id, "uom_po_id": cls.unit.id})
        Bom = env["mrp.bom"]
        cls.sauce_bom = Bom.create({
            "product_tmpl_id": cls.sauce.product_tmpl_id.id, "type": "phantom",
            "product_qty": 100.0, "product_uom_id": cls.g.id,
            "bom_line_ids": [Command.create({"product_id": cls.cream.id, "product_qty": 100.0,
                                             "product_uom_id": cls.g.id})],
        })
        cls.dish_bom = Bom.create({
            "product_tmpl_id": cls.dish.product_tmpl_id.id, "type": "phantom",
            "product_qty": 1.0, "product_uom_id": cls.unit.id, "bf_target_pct": 30.0,
            "bom_line_ids": [
                Command.create({"product_id": cls.salmon.id, "bf_net_qty": 120.0, "product_uom_id": cls.g.id}),
                Command.create({"product_id": cls.sauce.id, "product_qty": 30.0, "product_uom_id": cls.g.id}),
                Command.create({"product_id": cls.salt_g.id, "product_qty": 3.0, "product_uom_id": cls.g.id}),
            ],
        })
        cls.today = date.today()

    def bill(self, lines, invoice_date=None, post=True, partner=None):
        """lines: list of (product or None, quantity, price_unit, extra vals)."""
        vals = []
        for product, qty, price, extra in lines:
            line = {"quantity": qty, "price_unit": price, "tax_ids": [Command.clear()]}
            if product:
                line["product_id"] = product.id
            else:
                line["name"] = extra.pop("name", "Article sans produit")
                line["account_id"] = self.company_data["default_account_expense"].id
            line.update(extra)
            vals.append(Command.create(line))
        move = self.env["account.move"].create({
            "move_type": "in_invoice", "partner_id": (partner or self.vendor).id,
            "invoice_date": invoice_date or self.today, "invoice_line_ids": vals,
        })
        if post:
            move.action_post()
        return move

    def cost(self, product):
        return product.with_company(self.company).bf_cost_unit

    # ── recipe lines ──────────────────────────────────────────────────────────────────

    def test_yield_copied_and_net_inverse(self):
        line = self.dish_bom.bom_line_ids.filtered(lambda l: l.product_id == self.salmon)
        self.assertEqual(line.bf_yield_pct, 50.0, "the ingredient's yield is copied on the new line")
        self.assertAlmostEqual(line.product_qty, 240.0, msg="gross = net / yield")
        self.salmon.bf_yield_pct = 80.0
        self.assertEqual(line.bf_yield_pct, 50.0, "a later change of the default leaves the line alone")
        line.bf_net_qty = 100.0
        self.assertAlmostEqual(line.product_qty, 200.0)
        with self.assertRaises(ValidationError):
            line.bf_yield_pct = 0.0

    def test_three_grams_in_kg_refused(self):
        with self.assertRaises(ValidationError):
            self.env["mrp.bom.line"].create({
                "bom_id": self.dish_bom.id, "product_id": self.salt_kg.id,
                "product_qty": 3.0, "product_uom_id": self.g.id})
        # the same 3 g with the salt stocked in g passes (setup), and 30 g in kg too
        self.env["mrp.bom.line"].create({
            "bom_id": self.dish_bom.id, "product_id": self.salt_kg.id,
            "product_qty": 30.0, "product_uom_id": self.g.id})
        # per portion: 3 g for a recipe of 10 portions is 0.3 g a portion, refused in kg
        bom10 = self.dish_bom.copy({"product_qty": 10.0, "bom_line_ids": []})
        with self.assertRaises(ValidationError):
            self.env["mrp.bom.line"].create({
                "bom_id": bom10.id, "product_id": self.salt_kg.id,
                "product_qty": 30.0, "product_uom_id": self.g.id})

    # ── ingredient cost ───────────────────────────────────────────────────────────────

    def test_cost_from_last_bill_by_date(self):
        recent = self.today - timedelta(days=2)
        older = self.today - timedelta(days=20)
        self.bill([(self.salmon, 1000.0, 0.025, {})], invoice_date=recent)
        self.assertAlmostEqual(self.cost(self.salmon), 0.025)
        # a late-entered older bill does not replace the more recent price
        self.bill([(self.salmon, 1000.0, 0.040, {})], invoice_date=older)
        self.assertAlmostEqual(self.cost(self.salmon), 0.025)
        self.assertEqual(self.salmon.with_company(self.company).bf_cost_source, "bill")
        self.assertEqual(self.salmon.with_company(self.company).bf_cost_date, recent)

    def test_bill_in_other_unit_converted(self):
        self.bill([(self.salmon, 2.0, 30.0, {"product_uom_id": self.kg.id})])
        self.assertAlmostEqual(self.cost(self.salmon), 0.03, msg="30 $/kg is 0.03 $/g")

    def test_draft_and_cancel_fall_back(self):
        first = self.bill([(self.salmon, 1000.0, 0.020, {})], invoice_date=self.today - timedelta(days=10))
        second = self.bill([(self.salmon, 1000.0, 0.030, {})], invoice_date=self.today - timedelta(days=1))
        self.assertAlmostEqual(self.cost(self.salmon), 0.030)
        second.button_draft()
        self.assertAlmostEqual(self.cost(self.salmon), 0.020, msg="back to the previous bill")
        second.action_post()
        self.assertAlmostEqual(self.cost(self.salmon), 0.030)
        second.button_draft()
        second.button_cancel()
        self.assertAlmostEqual(self.cost(self.salmon), 0.020)
        first.button_draft()
        self.assertAlmostEqual(self.cost(self.salmon), 0.0, msg="no bill, no entered cost")

    def test_reversal_cancel_falls_back(self):
        self.bill([(self.salmon, 1000.0, 0.020, {})], invoice_date=self.today - timedelta(days=10))
        second = self.bill([(self.salmon, 1000.0, 0.050, {})], invoice_date=self.today - timedelta(days=1))
        self.assertAlmostEqual(self.cost(self.salmon), 0.050)
        second._reverse_moves([{"invoice_date": self.today, "date": self.today}], cancel=True)
        self.assertEqual(second.payment_state, "reversed")
        self.assertAlmostEqual(self.cost(self.salmon), 0.020)

    def test_taxes_included_price_net_of_tax(self):
        tax = self.env["account.tax"].create({
            "name": "Taxe incluse 15 %", "amount": 15.0, "type_tax_use": "purchase",
            "price_include_override": "tax_included", "company_id": self.company.id})
        self.bill([(self.salmon, 1000.0, 0.023, {"tax_ids": [Command.set(tax.ids)]})])
        self.assertAlmostEqual(self.cost(self.salmon), 0.02, places=4)
        self.company.bf_kitchen_tax_nonrecoverable_pct = 100.0
        self.salmon._bf_kitchen_refresh_costs(self.company)
        self.assertAlmostEqual(self.cost(self.salmon), 0.023, places=4)

    def test_discount_and_manual_cost(self):
        self.salt_g.with_company(self.company).bf_cost_manual = 0.002
        self.assertAlmostEqual(self.cost(self.salt_g), 0.002)
        self.assertEqual(self.salt_g.with_company(self.company).bf_cost_source, "manual")
        self.bill([(self.salt_g, 1000.0, 0.004, {"discount": 50.0})])
        self.assertAlmostEqual(self.cost(self.salt_g), 0.002)
        self.assertEqual(self.salt_g.with_company(self.company).bf_cost_source, "bill")

    def test_other_category_line_does_not_block(self):
        self.bill([(self.salmon, 1000.0, 0.020, {})], invoice_date=self.today - timedelta(days=5))
        move = self.bill([(None, 2.0, 25.0, {"name": "Saumon, pièce", "product_uom_id": self.unit.id})],
                         post=False)
        line = move.invoice_line_ids
        line.sudo().bf_ingredient_id = self.salmon  # kitchen fields: superuser only (R2 code 3)
        move.action_post()
        self.assertEqual(move.state, "posted", "the validation goes through")
        self.assertTrue(line.bf_cost_issue)
        self.assertAlmostEqual(self.cost(self.salmon), 0.020, msg="the unreadable line gives no price")
        # attaching it with its format solves it
        wizard = self.env["bf.kitchen.attach.line"].with_context(default_line_id=line.id).create({
            "ingredient_id": self.salmon.id, "pack_qty": 2000.0})
        with self.assertRaises(UserError):
            # the line has no product: Attach works, but not on a line that has one
            self.env["bf.kitchen.attach.line"].create({
                "line_id": self.bill([(self.salmon, 1.0, 1.0, {})], post=True,
                                     invoice_date=self.today - timedelta(days=30)).invoice_line_ids.id,
                "ingredient_id": self.salmon.id, "pack_qty": 1.0}).action_attach()
        wizard.action_attach()
        self.assertFalse(line.bf_cost_issue)
        self.assertAlmostEqual(self.cost(self.salmon), 25.0 / 2000.0)

    def test_attach_line_remembers_vendor(self):
        move = self.bill([(None, 3.0, 12.0, {"name": "CRM35 4L"})])
        line = move.invoice_line_ids
        domain = safe_eval(self.env.ref("bf_kitchen.bf_kitchen_lines_to_attach_action").domain)
        self.assertIn(line, self.env["account.move.line"].search(domain))
        self.assertEqual(move.state, "posted")
        self.assertFalse(line.product_id)
        self.env["bf.kitchen.attach.line"].create({
            "line_id": line.id, "ingredient_id": self.cream.id, "pack_qty": 4000.0,
            "vendor_code": "CRM35-4L"}).action_attach()
        self.assertFalse(line.product_id, "the posted line itself is not rewritten")
        self.assertEqual(line.bf_ingredient_id, self.cream)
        self.assertAlmostEqual(self.cost(self.cream), 12.0 / 4000.0)
        info = self.cream.seller_ids.filtered(lambda s: s.partner_id == self.vendor)
        self.assertEqual(info.product_code, "CRM35-4L")
        self.assertEqual(info.bf_pack_qty, 4000.0)
        self.assertFalse(info.bf_pack_uom_id, "the attached line had no unit")
        self.assertAlmostEqual(info.price, 12.0 / 4000.0, msg="price per purchase unit (g), not per case")
        # the next bill carries the product in g: the case format does not apply to grams
        self.bill([(self.cream, 12000.0, 0.005, {})])
        self.assertAlmostEqual(self.cost(self.cream), 0.005)

    # ── recipes, propagation, alert ───────────────────────────────────────────────────

    def test_propagation_alert_and_price(self):
        activity_type = self.env.ref("bf_kitchen.mail_activity_plate_cost")
        self.bill([(self.cream, 1000.0, 0.010, {}), (self.salt_g, 1000.0, 0.001, {}),
                   (self.salmon, 1000.0, 0.020, {})], invoice_date=self.today - timedelta(days=3))
        self.assertAlmostEqual(self.cost(self.sauce), 0.010, msg="preparation cost per g from its recipe")
        expected = 240 * 0.020 + 30 * 0.010 + 3 * 0.001  # 5.103
        self.assertAlmostEqual(self.cost(self.dish), expected, places=4)
        bom = self.dish_bom.with_company(self.company)
        self.assertAlmostEqual(bom.bf_cost_portion, expected, places=2, msg="shown to the cent")
        self.assertAlmostEqual(bom.bf_food_cost_pct, expected / 20.0 * 100.0, places=2)
        self.assertEqual(bom.bf_price_suggested, 17.25, "5.103 / 30 % = 17.01, next 0.25")
        self.assertFalse(self.dish.product_tmpl_id.activity_ids.filtered(lambda a: a.activity_type_id == activity_type))
        # salmon goes up: 240 g at 0.030 = 7.20 + 0.303 = 7.503, 37.5 % > 30 %
        self.bill([(self.salmon, 1000.0, 0.030, {})], invoice_date=self.today - timedelta(days=1))
        acts = self.dish.product_tmpl_id.activity_ids.filtered(lambda a: a.activity_type_id == activity_type)
        self.assertEqual(len(acts), 1)
        self.assertIn("Saumon entier", acts.note)
        self.assertNotIn("%", acts.note, "no food cost figure in an activity the cook can read")
        self.assertNotRegex(acts.note, r"\d[.,]\d")
        self.bill([(self.salmon, 1000.0, 0.035, {})], invoice_date=self.today)
        acts = self.dish.product_tmpl_id.activity_ids.filtered(lambda a: a.activity_type_id == activity_type)
        self.assertEqual(len(acts), 1, "one open alert per dish")
        bom.action_bf_apply_suggested_price()
        self.assertEqual(self.dish.list_price, bom.bf_price_suggested)
        self.assertFalse(bom.bf_over_target)

    def test_rounding_rules(self):
        Bom = self.env["mrp.bom"]
        c = self.company
        for rule, price, expected in [("0.25", 17.01, 17.25), ("0.05", 17.01, 17.05), ("1", 17.01, 18.0),
                                      ("0.95", 17.01, 17.95), ("0.95", 16.90, 16.95), ("none", 17.014, 17.01)]:
            c.bf_kitchen_price_rounding = rule
            self.assertAlmostEqual(Bom._bf_round_price(price, c), expected, msg=rule)

    def test_allergens_carried_up(self):
        milk = self.env.ref("bf_food_allergen.allergen_milk")
        self.env.invalidate_all()
        self.assertIn(milk, self.dish.product_tmpl_id.bf_allergen_ids, "cream > sauce > dish")
        self.assertNotIn(milk, self.salmon.product_tmpl_id.bf_allergen_ids)

    def test_stale_cost_flag(self):
        self.bill([(self.salmon, 1000.0, 0.020, {})], invoice_date=self.today - timedelta(days=90))
        self.assertTrue(self.dish_bom.with_company(self.company).bf_cost_stale)
        self.company.bf_kitchen_cost_max_age = 120
        self.dish_bom.invalidate_recordset()
        self.assertFalse(self.dish_bom.with_company(self.company).bf_cost_stale)

    # ── "Received" ────────────────────────────────────────────────────────────────────

    def test_received_once(self):
        move = self.bill([(self.salmon, 2.0, 30.0, {"product_uom_id": self.kg.id})], post=False)
        self.assertTrue(move.bf_kitchen_can_receive)
        move.action_bf_kitchen_receive()
        line = move.invoice_line_ids
        self.assertEqual(line.bf_receipt_move_id.state, "done")
        self.assertAlmostEqual(line.bf_receipt_move_id.quantity, 2000.0)
        self.assertAlmostEqual(line.bf_receipt_move_id.price_unit, 0.03)
        on_hand = self.salmon.with_company(self.company).qty_available
        self.assertAlmostEqual(on_hand, 2000.0)
        self.assertFalse(move.bf_kitchen_can_receive)
        with self.assertRaises(UserError):
            move.action_bf_kitchen_receive()
        move.action_post()
        move.button_draft()
        self.assertFalse(move.bf_kitchen_can_receive, "back to draft: nothing is received twice")
        self.assertAlmostEqual(self.salmon.with_company(self.company).qty_available, 2000.0)
        move.button_cancel()
        self.salmon.invalidate_recordset()
        self.assertAlmostEqual(self.salmon.with_company(self.company).qty_available, 0.0,
                               msg="a cancelled bill sends the goods back")

    def test_received_skips_purchase_lines(self):
        po = self.env["purchase.order"].create({
            "partner_id": self.vendor.id,
            "order_line": [Command.create({"product_id": self.salmon.id, "product_qty": 500.0, "price_unit": 0.02})]})
        po.button_confirm()
        move = self.bill([(self.salmon, 500.0, 0.02, {"purchase_line_id": po.order_line.id})], post=False)
        self.assertFalse(move.bf_kitchen_can_receive)

    # ── rights ────────────────────────────────────────────────────────────────────────

    def test_cook_opens_recipe_without_costs(self):
        cook = new_test_user(self.env, login="kitchen_cook", groups="base.group_user,bf_kitchen.group_kitchen_cook",
                             company_id=self.company.id)
        bom = self.dish_bom.with_user(cook)
        form = Form(bom, view="bf_kitchen.bf_kitchen_bom_view_form")
        self.assertEqual(len(form.bom_line_ids), 3)
        arch = self.env["mrp.bom"].with_user(cook).get_views([(False, "form")])  # default view
        arch_kitchen = self.env["mrp.bom"].with_user(cook).get_views(
            [(self.env.ref("bf_kitchen.bf_kitchen_bom_view_form").id, "form")])
        self.assertNotIn("bf_cost_portion", arch_kitchen["views"]["form"]["arch"])
        self.assertNotIn("bf_cost_line", str(arch_kitchen["models"]))
        self.assertTrue(arch)
        # the native manufacturing form opens too (operations are read through the line)
        Form(bom, view="mrp.mrp_bom_form_view")
        with self.assertRaises(AccessError):
            bom.bf_cost_portion  # noqa: B018
        with self.assertRaises(AccessError):
            bom.write({"product_qty": 2.0})
        self.env["bf.food.allergen"].with_user(cook).search([])

    def test_kitchen_manager_scope(self):
        other = self.env["product.product"].create({"name": "Papier imprimante", "type": "consu"})
        chef = new_test_user(self.env, login="kitchen_chef",
                             groups="base.group_user,bf_kitchen.group_kitchen_manager",
                             company_id=self.company.id)
        both = new_test_user(self.env, login="kitchen_stock",
                             groups="base.group_user,stock.group_stock_manager,bf_kitchen.group_kitchen_manager",
                             company_id=self.company.id)
        self.salmon.product_tmpl_id.with_user(chef).name = "Saumon atlantique"
        self.dish_bom.with_user(chef).product_qty = 2.0
        with self.assertRaises(AccessError):
            other.product_tmpl_id.with_user(chef).name = "Papier"
        with self.assertRaises(AccessError):
            self.env["product.template"].with_user(chef).create({"name": "Pas de cuisine", "type": "consu"})
        with self.assertRaises(AccessError):
            self.salmon.product_tmpl_id.with_user(chef).bf_kitchen_type = False
        new = self.env["product.template"].with_user(chef).create(
            {"name": "Persil", "type": "consu", "bf_kitchen_type": "ingredient"})
        self.assertTrue(new)
        # a stock manager who is also in the kitchen keeps the whole product access
        other.product_tmpl_id.with_user(both).name = "Papier"
        self.assertEqual(other.name, "Papier")
        # the native manufacturing form opens for the chef as well
        Form(self.dish_bom.with_user(chef), view="bf_kitchen.bf_kitchen_bom_view_form")

    def test_report_renders(self):
        for xmlid in ("bf_kitchen.action_report_technical_sheet", "bf_kitchen.action_report_technical_sheet_cost",
                      "bf_kitchen.action_report_allergen_sheet"):
            html, _fmt = self.env["ir.actions.report"]._render_qweb_html(xmlid, self.dish_bom.ids)
            self.assertIn(b"Tartare de saumon", html)

    def test_every_kitchen_screen_opens_for_each_role(self):
        cook = new_test_user(self.env, login="kitchen_cook2", groups="base.group_user,bf_kitchen.group_kitchen_cook",
                             company_id=self.company.id)
        chef = new_test_user(self.env, login="kitchen_chef2",
                             groups="base.group_user,bf_kitchen.group_kitchen_manager", company_id=self.company.id)
        storekeeper = new_test_user(self.env, login="kitchen_store", groups="base.group_user,stock.group_stock_user",
                                    company_id=self.company.id)
        actions = ["bf_kitchen.bf_kitchen_dish_action", "bf_kitchen.bf_kitchen_preparation_action",
                   "bf_kitchen.bf_kitchen_ingredient_action"]
        for user in (cook, chef):
            for xmlid in actions:
                action = self.env["ir.actions.act_window"].with_user(user)._for_xml_id(xmlid)
                Model = self.env[action["res_model"]].with_user(user)
                views = [(v, m) for v, m in action["views"]]
                result = Model.get_views(views)
                fields = list(result["models"][action["res_model"]]["fields"])
                Model.search_read(safe_eval(action.get("domain") or "[]"), [f for f in fields if f in Model._fields],
                                  limit=5)
        # the product form, with its Kitchen tab, still opens for a storekeeper outside the kitchen
        Form(self.salmon.product_tmpl_id.with_user(storekeeper), view="product.product_template_only_form_view")
        Form(self.salmon.product_tmpl_id.with_user(cook), view="product.product_template_only_form_view")

    # ── access, bill lines and receipts ──────────────────────────────────────────────

    def mkuser(self, login, groups):
        return new_test_user(self.env, login=login, groups="base.group_user," + groups,
                             company_id=self.company.id)

    def receive(self, qty_kg=2.0):
        move = self.bill([(self.salmon, qty_kg, 30.0, {"product_uom_id": self.kg.id})], post=False)
        move.action_bf_kitchen_receive()
        return move, move.invoice_line_ids

    def on_hand(self, product):
        product.invalidate_recordset()
        return product.with_company(self.company).qty_available

    def test_r1_cost_sheet_refused_to_cook(self):
        """[R1 code 1] The cost sheet renders for the kitchen manager only, html and pdf."""
        cook = self.mkuser("r1_cook", "bf_kitchen.group_kitchen_cook")
        chef = self.mkuser("r1_chef", "bf_kitchen.group_kitchen_manager")
        Report = self.env["ir.actions.report"]
        cost_sheet = "bf_kitchen.action_report_technical_sheet_cost"
        with self.assertRaises(AccessError):
            Report.with_user(cook)._render_qweb_html(cost_sheet, self.dish_bom.ids)
        with self.assertRaises(AccessError):
            Report.with_user(cook)._render_qweb_pdf(cost_sheet, self.dish_bom.ids)
        with self.assertRaises(AccessError):
            Report.with_user(cook).with_context(force_report_rendering=True)._render_qweb_pdf(
                cost_sheet, self.dish_bom.ids)
        for xmlid in ("bf_kitchen.action_report_technical_sheet", "bf_kitchen.action_report_allergen_sheet"):
            html, _fmt = Report.with_user(cook)._render_qweb_html(xmlid, self.dish_bom.ids)
            self.assertIn(b"Tartare de saumon", html)
            self.assertNotIn(b"Plate cost", html, xmlid)
            self.assertNotIn(b"Suggested price", html, xmlid)
        html, _fmt = Report.with_user(chef)._render_qweb_html(cost_sheet, self.dish_bom.ids)
        self.assertIn(b"Plate cost", html)

    def test_r1_supplierinfo_scope(self):
        """[R1 code 2] A kitchen manager writes the vendor lines of kitchen products only."""
        chef = self.mkuser("r1_chef_si", "bf_kitchen.group_kitchen_manager")
        other = self.env["product.product"].create({"name": "Abonnement antivirus", "type": "service"})
        theirs = self.env["product.supplierinfo"].create({
            "partner_id": self.vendor.id, "product_tmpl_id": other.product_tmpl_id.id, "price": 10.0})
        Info = self.env["product.supplierinfo"].with_user(chef)
        with self.assertRaises(AccessError):
            theirs.with_user(chef).price = 0.01
        with self.assertRaises(AccessError):
            Info.create({"partner_id": self.vendor.id, "product_tmpl_id": other.product_tmpl_id.id, "price": 1.0})
        with self.assertRaises(AccessError):
            theirs.with_user(chef).unlink()
        mine = Info.create({"partner_id": self.vendor.id, "product_tmpl_id": self.cream.product_tmpl_id.id,
                            "price": 0.004})
        mine.price = 0.005
        with self.assertRaises(AccessError):
            mine.product_tmpl_id = other.product_tmpl_id
        self.assertEqual(theirs.price, 10.0)

    def test_r1_format_applies_to_its_billed_unit_only(self):
        """[R1 code 3] The vendor format applies to its billed unit; the vendor price is per
        purchase unit, in the bill currency."""
        move = self.bill([(None, 1.0, 60.0, {"name": "Crème 35 % caisse 12 x 1 L"})])
        line = move.invoice_line_ids
        self.env["bf.kitchen.attach.line"].create({
            "line_id": line.id, "ingredient_id": self.cream.id, "pack_qty": 12000.0}).action_attach()
        self.assertAlmostEqual(self.cost(self.cream), 0.005)
        info = self.cream.seller_ids.filtered(lambda s: s.partner_id == self.vendor)
        self.assertAlmostEqual(info.price, 0.005, msg="per g, the purchase unit")
        self.assertEqual(info.currency_id, line.currency_id)
        # a later bill typed in g is read in g, not as cases
        self.bill([(self.cream, 12000.0, 0.006, {})])
        self.assertAlmostEqual(self.cost(self.cream), 0.006, msg="not 0.000000: the case format is not applied to g")
        # a purchase order of 1000 g proposes 5 $, not 60 000 $
        po = self.env["purchase.order"].create({
            "partner_id": self.vendor.id,
            "order_line": [Command.create({"product_id": self.cream.id, "product_qty": 1000.0})]})
        self.assertAlmostEqual(po.order_line.price_unit, 0.005)
        self.assertAlmostEqual(po.amount_untaxed, 5.0)
        # a format given for one unit applies to that unit only
        info.write({"bf_pack_qty": 4000.0, "bf_pack_uom_id": self.g.id})
        line_g = self.bill([(self.cream, 2.0, 16.0, {})]).invoice_line_ids
        self.assertEqual(line_g._bf_kitchen_stock_qty(self.cream, self.company)[0], 8000.0)
        line_kg = self.bill([(self.cream, 2.0, 16.0, {"product_uom_id": self.kg.id})],
                            invoice_date=self.today - timedelta(days=40)).invoice_line_ids
        self.assertEqual(line_kg._bf_kitchen_stock_qty(self.cream, self.company)[0], 2000.0)
        # a bill in a foreign currency gives a vendor price in that currency
        eur = self.setup_other_currency("EUR", rates=[("2000-01-01", 0.5)])
        foreign = self.env["account.move"].create({
            "move_type": "in_invoice", "partner_id": self.vendor.id, "invoice_date": self.today,
            "currency_id": eur.id, "invoice_line_ids": [Command.create({
                "name": "Crème caisse", "quantity": 1.0, "price_unit": 24.0, "tax_ids": [Command.clear()],
                "account_id": self.company_data["default_account_expense"].id})]})
        foreign.action_post()
        self.env["bf.kitchen.attach.line"].create({
            "line_id": foreign.invoice_line_ids.id, "ingredient_id": self.cream.id,
            "pack_qty": 12000.0}).action_attach()
        no_unit = self.cream.seller_ids.filtered(lambda s: s.partner_id == self.vendor and not s.bf_pack_uom_id)
        self.assertEqual(len(no_unit), 1, "one vendor line per billed unit")
        self.assertAlmostEqual(no_unit.price, 0.002, msg="24 EUR / 12000 g")
        self.assertEqual(no_unit.currency_id, eur)
        self.assertAlmostEqual(self.cost(self.cream), 48.0 / 12000.0, msg="the cost is in company currency")

    def test_r1_received_bill_deleted(self):
        """[R1 code 4a] A received draft bill or line cannot be deleted while its goods are in."""
        move, line = self.receive()
        with self.assertRaises(UserError):
            move.unlink()
        with self.assertRaises(UserError):
            move.write({"invoice_line_ids": [Command.delete(line.id)]})
        self.assertAlmostEqual(self.on_hand(self.salmon), 2000.0)
        move.button_cancel()
        self.assertAlmostEqual(self.on_hand(self.salmon), 0.0)
        move.unlink()

    def test_r1_received_line_frozen(self):
        """[R1 code 4b] Product, unit and quantity of a received line cannot change."""
        move, line = self.receive()
        with self.assertRaises(UserError):
            line.quantity = 5.0
        with self.assertRaises(UserError):
            line.product_id = self.cream
        with self.assertRaises(UserError):
            line.product_uom_id = self.g
        line.write({"product_uom_id": self.g.id, "quantity": 2000.0})  # same 2000 g: accepted
        line.price_unit = 0.031  # the price is not the receipt's business
        self.assertAlmostEqual(self.on_hand(self.salmon), 2000.0)

    def test_r1_cancel_draft_receive_again(self):
        """[R1 code 4c] Cancel, back to draft, Received again: the stock follows."""
        move, line = self.receive()
        move.button_cancel()
        self.assertAlmostEqual(self.on_hand(self.salmon), 0.0)
        move.button_draft()
        self.assertTrue(move.bf_kitchen_can_receive, "the goods went back: the line can be received again")
        move.action_bf_kitchen_receive()
        self.assertAlmostEqual(self.on_hand(self.salmon), 2000.0)
        move.action_post()
        self.assertAlmostEqual(self.on_hand(self.salmon), 2000.0)

    def _native_return(self, line, qty):
        picking = line.bf_receipt_move_id.picking_id
        wizard = self.env["stock.return.picking"].with_context(
            active_id=picking.id, active_model="stock.picking").create({"picking_id": picking.id})
        wizard.product_return_moves.quantity = qty
        ret = wizard._create_return()
        ret.move_ids.write({"quantity": qty, "picked": True})
        ret.button_validate()

    def test_r1_native_return_then_cancel(self):
        """[R1 code 4d] A return made from Inventory is deducted from the return at cancellation."""
        move, line = self.receive()
        self._native_return(line, 500.0)
        self.assertAlmostEqual(self.on_hand(self.salmon), 1500.0)
        move.button_cancel()
        self.assertAlmostEqual(self.on_hand(self.salmon), 0.0, msg="1500 sent back, not 2000")
        # a full return from Inventory: nothing is sent back twice, and the line can be received again
        move2, line2 = self.receive(1.0)
        self._native_return(line2, 1000.0)
        self.assertTrue(move2.bf_kitchen_can_receive)
        move2.button_cancel()
        self.assertAlmostEqual(self.on_hand(self.salmon), 0.0, msg="not -1000")

    def test_r1_standard_credit_note(self):
        """[R1 code 5] A full credit note, posted without reconciliation, drops the bill's price."""
        self.bill([(self.salmon, 1000.0, 0.020, {})], invoice_date=self.today - timedelta(days=10))
        second = self.bill([(self.salmon, 1000.0, 0.050, {})], invoice_date=self.today - timedelta(days=1))
        reversal = self.env["account.move.reversal"].with_context(
            active_model="account.move", active_ids=second.ids).create({
                "journal_id": second.journal_id.id, "date": self.today, "reason": "erreur"})
        refund = self.env["account.move"].browse(reversal.refund_moves()["res_id"])
        self.assertAlmostEqual(self.cost(self.salmon), 0.050, msg="a draft credit note changes nothing")
        refund.action_post()
        self.assertAlmostEqual(self.cost(self.salmon), 0.020)
        refund.button_draft()
        self.assertAlmostEqual(self.cost(self.salmon), 0.050)
        # a partial credit note (a discount after the fact) leaves the bill's price
        refund.invoice_line_ids.price_unit = 0.010
        refund.action_post()
        self.assertAlmostEqual(self.cost(self.salmon), 0.050)

    def test_r1_attach_needs_billing_rights(self):
        """[R1 code 7] Attaching or ignoring a line takes Kitchen manager and Accounting / Billing."""
        chef = self.mkuser("r1_chef_nobill", "bf_kitchen.group_kitchen_manager")
        clerk = self.mkuser("r1_chef_bill", "bf_kitchen.group_kitchen_manager,account.group_account_invoice")
        line = self.bill([(None, 3.0, 12.0, {"name": "CRM35 4L"})]).invoice_line_ids
        Wizard = self.env["bf.kitchen.attach.line"]
        with self.assertRaises(AccessError):
            Wizard.with_user(chef).create({"line_id": line.id, "ingredient_id": self.cream.id, "pack_qty": 4000.0})
        with self.assertRaises(AccessError):
            Wizard.with_user(chef).with_context(default_line_id=line.id).default_get(["line_id", "pack_qty"])
        with self.assertRaises(AccessError):
            line.with_user(chef).action_bf_kitchen_ignore()
        with self.assertRaises(AccessError):
            line.with_user(chef).action_bf_kitchen_attach()
        # an auditor (read-only accounting) reads the lines but is not Billing: refused as well
        auditor = self.mkuser("r1_chef_audit", "bf_kitchen.group_kitchen_manager,account.group_account_readonly")
        line.with_user(auditor).check_access("read")
        with self.assertRaises(AccessError):
            Wizard.with_user(auditor).create({"line_id": line.id, "ingredient_id": self.cream.id, "pack_qty": 4000.0})
        with self.assertRaises(AccessError):
            line.with_user(auditor).action_bf_kitchen_ignore()
        self.assertFalse(line.bf_kitchen_ignore)
        # a purchase user writes bill lines without Billing: refused by the group check alone
        buyer = self.mkuser("r1_chef_buyer", "bf_kitchen.group_kitchen_manager,purchase.group_purchase_user")
        line.with_user(buyer).check_access("write")
        with self.assertRaises(AccessError):
            Wizard.with_user(buyer).create({"line_id": line.id, "ingredient_id": self.cream.id, "pack_qty": 4000.0})
        with self.assertRaises(AccessError):
            line.with_user(buyer).action_bf_kitchen_ignore()
        Wizard.with_user(clerk).create(
            {"line_id": line.id, "ingredient_id": self.cream.id, "pack_qty": 4000.0}).action_attach()
        self.assertEqual(line.bf_ingredient_id, self.cream)
        other = self.bill([(None, 1.0, 5.0, {"name": "Sacs"})]).invoice_line_ids
        other.with_user(clerk).action_bf_kitchen_ignore()
        self.assertTrue(other.bf_kitchen_ignore)

    def test_r1_portions_change_checked(self):
        """[R1 code 8] More portions, or a bigger recipe unit, re-check the quantity per portion.

        Since R2 code 7, one portion is what Odoo explodes: 3 g of salt stocked in g for 1000
        portions is rounded up to 0.01 g a portion and does leave the stock; stocked in kg it
        does not."""
        self.dish_bom.product_qty = 1000.0  # 0.003 g a portion, rounded up to 0.01 g: it leaves the stock
        self.dish_bom.product_qty = 1.0
        salt = self.dish_bom.bom_line_ids.filtered(lambda l: l.product_id == self.salt_g)
        salt.product_qty = 30.0
        salt.product_id = self.salt_kg  # 30 g a portion: 0.03 kg, fine
        with self.assertRaises(ValidationError):
            self.dish_bom.product_qty = 1000.0  # 30 g of salt in kg for 1000 portions: 0.00 kg
        bom = self.dish_bom.copy({"bom_line_ids": [Command.create({
            "product_id": self.salt_kg.id, "product_qty": 30.0, "product_uom_id": self.g.id})]})
        with self.assertRaises(ValidationError):
            bom.product_uom_id = self.env.ref("uom.product_uom_dozen")  # 2.5 g a portion, 0.00 kg

    def test_r1_protected_product_fields(self):
        """[R1 code 9] The kitchen scope writes kitchen fields, not taxes, accounts, cost, category."""
        chef = self.mkuser("r1_chef_fields", "bf_kitchen.group_kitchen_manager")
        both = self.mkuser("r1_chef_stock", "stock.group_stock_manager,bf_kitchen.group_kitchen_manager")
        tax = self.env["account.tax"].create({"name": "TVQ essai", "amount": 9.975, "type_tax_use": "purchase"})
        self.cream.supplier_taxes_id = tax
        tmpl = self.cream.product_tmpl_id.with_user(chef)
        for vals in ({"supplier_taxes_id": [Command.clear()]}, {"taxes_id": [Command.set(tax.ids)]},
                     {"standard_price": 123.0}, {"categ_id": self.env["product.category"].create({"name": "X"}).id},
                     {"property_account_expense_id": self.company_data["default_account_revenue"].id},
                     {"is_storable": False}):
            with self.assertRaises(AccessError, msg=str(vals)):
                tmpl.write(vals)
            self.env.invalidate_all()  # the savepoint is rolled back, not the record cache
        with self.assertRaises(AccessError):
            self.cream.with_user(chef).standard_price = 123.0
        with self.assertRaises(AccessError):
            self.env["product.template"].with_user(chef).create(
                {"name": "Ciboulette", "bf_kitchen_type": "ingredient", "standard_price": 5.0})
        # a write that leaves the taxes as they are passes (a "set" from this company would also
        # drop the other companies' taxes: refused above)
        tmpl.write({"name": "Crème 35 % (essai)", "bf_yield_pct": 95.0, "supplier_taxes_id": [Command.link(tax.id)]})
        self.assertIn(tax, self.cream.supplier_taxes_id)
        self.assertEqual(self.cream.standard_price, 0.0)
        self.cream.product_tmpl_id.with_user(both).standard_price = 0.5
        self.assertEqual(self.cream.standard_price, 0.5)

    def test_r1_levels_dish_waits_for_its_sauce(self):
        """[R1 code 10] A dish made of an ingredient and of a sauce of that ingredient is
        computed once, after the sauce."""
        Bom = self.env["mrp.bom"]
        dish = self.env["product.product"].create({"name": "Crème brûlée", "type": "consu", "bf_kitchen_type": "dish",
                                                   "list_price": 10.0, "uom_id": self.unit.id})
        Bom.create({"product_tmpl_id": dish.product_tmpl_id.id, "type": "phantom", "product_qty": 1.0,
                    "bom_line_ids": [
                        Command.create({"product_id": self.cream.id, "product_qty": 50.0, "product_uom_id": self.g.id}),
                        Command.create({"product_id": self.sauce.id, "product_qty": 20.0, "product_uom_id": self.g.id})]})
        Product = type(self.env["product.product"])
        with patch.object(Product, "_bf_kitchen_ingredient_cost", autospec=True,
                          side_effect=Product._bf_kitchen_ingredient_cost) as spy:
            self.bill([(self.cream, 1000.0, 0.010, {})])
        self.assertEqual(spy.call_count, 1)
        self.assertAlmostEqual(self.cost(dish), 50 * 0.010 + 20 * 0.010)

    def test_r1_cost_hooks_never_block(self):
        """[R1 code 11] The search of the products touched runs in the savepoint too."""
        with patch.object(AccountMove, "_bf_kitchen_products", side_effect=ValueError("boum")), \
                mute_logger("odoo.addons.bf_kitchen.models.account_move"):
            move = self.bill([(self.salmon, 1000.0, 0.020, {})])
        self.assertEqual(move.state, "posted")
        # the return at cancellation fails loudly, and nothing is half done
        move2, line = self.receive()
        with patch.object(type(self.env["stock.return.picking"]), "_create_return", side_effect=UserError("boum")):
            with self.assertRaises(UserError):
                move2.button_cancel()
        self.assertEqual(move2.state, "draft")

    # ── access to bill lines, variants, valuation ───────────────────────────────────

    def test_r2_receipt_link_written_by_received_only(self):
        """[R2 code 1] Billing alone cannot point a line at a receipt nor lift the freeze; only a
        receipt made by Received for this very line is ever sent back."""
        clerk = self.mkuser("r2_billing", "account.group_account_invoice")
        storekeeper = self.mkuser("r2_stock", "stock.group_stock_manager")
        po = self.env["purchase.order"].create({
            "partner_id": self.vendor.id,
            "order_line": [Command.create({"product_id": self.cream.id, "product_qty": 5000.0, "price_unit": 0.01})]})
        po.button_confirm()
        po.picking_ids.move_ids.write({"quantity": 5000.0, "picked": True})
        po.picking_ids.button_validate()
        native = po.picking_ids.move_ids
        self.assertAlmostEqual(self.on_hand(self.cream), 5000.0)
        Move = self.env["account.move"].with_user(clerk)
        bill = Move.create({
            "move_type": "in_invoice", "partner_id": self.vendor.id, "invoice_date": self.today,
            "invoice_line_ids": [Command.create({"product_id": self.cream.id, "quantity": 1.0, "price_unit": 1.0,
                                                 "tax_ids": [Command.clear()]})]})
        line = bill.invoice_line_ids
        with self.assertRaises(AccessError):
            line.write({"bf_receipt_move_id": native.id})
        with self.assertRaises(AccessError):
            self.env["account.move.line"].with_user(clerk).with_context(
                default_bf_receipt_move_id=native.id).create({
                    "move_id": bill.id, "product_id": self.cream.id, "quantity": 1.0, "price_unit": 1.0})
        with self.assertRaises(AccessError):
            native.with_user(storekeeper).write({"bf_kitchen_bill_line_id": line.id})
        # a link that is not a receipt of this line (old data, a bug) is never sent back
        line.sudo().bf_receipt_move_id = native
        self.assertFalse(line._bf_kitchen_is_received())
        bill.button_cancel()
        self.assertEqual(bill.state, "cancel")
        self.assertAlmostEqual(self.on_hand(self.cream), 5000.0, msg="the purchase order's receipt stays in")
        self.assertFalse(native.returned_move_ids)
        # a received line stays frozen for Billing, whatever the context says
        move, received = self.receive()
        received = received.with_user(clerk)
        with self.assertRaises(UserError):
            received.with_context(bf_kitchen_receiving=True).write({"quantity": 9.0})
        with self.assertRaises(AccessError):
            received.write({"bf_receipt_move_id": False})
        with self.assertRaises(UserError):
            received.unlink()
        self.assertAlmostEqual(self.on_hand(self.salmon), 2000.0)
        self.assertEqual(received.bf_receipt_move_id.bf_kitchen_bill_line_id, received)

    def test_r2_attach_kitchen_ingredient_only(self):
        """[R2 code 2] Attach takes a kitchen ingredient only, and the vendor lines of the bill's
        company only."""
        clerk = self.mkuser("r2_chef_bill", "bf_kitchen.group_kitchen_manager,account.group_account_invoice")
        other = self.env["product.product"].create({"name": "Cartouche d'encre", "type": "consu"})
        theirs = self.env["product.supplierinfo"].create({
            "partner_id": self.vendor.id, "product_tmpl_id": other.product_tmpl_id.id, "price": 100.0})
        line = self.bill([(None, 1.0, 0.5, {"name": "Ligne libre"})]).invoice_line_ids
        Wizard = self.env["bf.kitchen.attach.line"]
        wizard = Wizard.with_user(clerk).create({"line_id": line.id, "ingredient_id": other.id, "pack_qty": 1.0})
        with self.assertRaises(UserError):
            wizard.action_attach()
        self.assertEqual(theirs.price, 100.0)
        self.assertEqual(len(other.seller_ids), 1)
        self.assertFalse(line.bf_ingredient_id)
        # as superuser too, where the kitchen scope does not apply: only this check stops it
        with self.assertRaises(UserError):
            Wizard.sudo().create({"line_id": line.id, "ingredient_id": other.id, "pack_qty": 1.0}).action_attach()
        self.assertEqual(len(other.seller_ids), 1)
        self.assertFalse(line.bf_ingredient_id)
        # the vendor line of another company is neither read nor written, even as superuser
        company_b = self.env["res.company"].create({"name": "Traiteur B (essai)"})
        info_b = self.env["product.supplierinfo"].sudo().create({
            "partner_id": self.vendor.id, "product_tmpl_id": self.cream.product_tmpl_id.id,
            "company_id": company_b.id, "price": 7.0, "product_name": "Société B"})
        Wizard.sudo().create({"line_id": line.id, "ingredient_id": self.cream.id, "pack_qty": 4000.0}).action_attach()
        self.assertEqual(info_b.price, 7.0)
        self.assertEqual(info_b.product_name, "Société B")
        mine = self.cream.sudo().seller_ids.filtered(lambda s: s.company_id == self.company and s.partner_id == self.vendor)
        self.assertEqual(len(mine), 1)
        self.assertAlmostEqual(mine.price, 0.5 / 4000.0)

    def test_r2_kitchen_line_fields_superuser_only(self):
        """[R2 code 3] Ingredient, format and "not an ingredient" of a bill line are written by the
        kitchen's buttons only, never by Billing directly."""
        clerk = self.mkuser("r2_billing3", "account.group_account_invoice")
        self.bill([(self.salmon, 1000.0, 0.020, {})])
        line = self.bill([(None, 1.0, 50.0, {"name": "Article libre"})]).invoice_line_ids
        for vals in ({"bf_ingredient_id": self.salmon.id}, {"bf_pack_qty": 1.0}, {"bf_kitchen_ignore": True},
                     {"bf_cost_issue": "x"}):
            with self.assertRaises(AccessError, msg=str(vals)):
                line.with_user(clerk).write(vals)
        draft = self.bill([(None, 1.0, 50.0, {"name": "Brouillon"})], post=False)
        with self.assertRaises(AccessError):
            self.env["account.move.line"].with_user(clerk).with_context(
                default_bf_ingredient_id=self.salmon.id, default_bf_pack_qty=1.0).create({
                    "move_id": draft.id, "name": "Pirate", "quantity": 1.0, "price_unit": 50.0,
                    "account_id": self.company_data["default_account_expense"].id})
        self.env["product.product"]._cron_bf_kitchen_refresh_all()
        self.assertAlmostEqual(self.cost(self.salmon), 0.020, msg="not 50 $/g")
        # "Not an ingredient" is for the lines without a product of a vendor bill
        chef = self.mkuser("r2_chef_bill3", "bf_kitchen.group_kitchen_manager,account.group_account_invoice")
        with_product = self.bill([(self.salmon, 1.0, 1.0, {})]).invoice_line_ids
        with self.assertRaises(UserError):
            with_product.with_user(chef).action_bf_kitchen_ignore()
        self.assertFalse(with_product.bf_kitchen_ignore)

    def test_r2_recipe_edits_refresh_stored_costs(self):
        """[R2 code 4] Editing a recipe refreshes the stored costs; the nightly run computes every
        level again."""
        chef = self.mkuser("r2_chef_rec", "bf_kitchen.group_kitchen_manager")
        self.bill([(self.cream, 1000.0, 0.010, {}), (self.salt_g, 1000.0, 0.001, {}),
                   (self.salmon, 1000.0, 0.020, {})])
        self.assertAlmostEqual(self.cost(self.dish), 5.103, places=6)
        self.sauce_bom.with_user(chef).bom_line_ids.write({"product_qty": 200.0})
        self.assertAlmostEqual(self.cost(self.sauce), 0.020)
        self.assertAlmostEqual(self.cost(self.dish), 240 * 0.020 + 30 * 0.020 + 3 * 0.001, places=6)
        # a dish made of a preparation only has its cost at once, and follows its edits
        plate = self.env["product.template"].with_user(chef).create({
            "name": "Assiette de sauce", "bf_kitchen_type": "dish", "type": "consu", "list_price": 10.0,
            "uom_id": self.unit.id, "uom_po_id": self.unit.id}).product_variant_id
        plate_bom = self.env["mrp.bom"].with_user(chef).create({
            "product_tmpl_id": plate.product_tmpl_id.id, "type": "phantom", "product_qty": 1.0,
            "bom_line_ids": [Command.create({"product_id": self.sauce.id, "product_qty": 50.0,
                                             "product_uom_id": self.g.id})]})
        self.assertAlmostEqual(self.cost(plate), 1.0)
        plate_bom.bom_line_ids.product_qty = 100.0
        self.assertAlmostEqual(self.cost(plate), 2.0)
        # a context key given by the caller skips nothing
        plate_bom.bom_line_ids.with_context(bf_kitchen_recipe_saving=True).product_qty = 75.0
        self.assertAlmostEqual(self.cost(plate), 1.5)
        self.lemon.with_user(chef).with_context(bf_kitchen_refreshing=True).bf_cost_manual = 0.5
        self.assertAlmostEqual(self.cost(self.lemon), 0.5)
        # the nightly run puts a wrong stored value right: a dish alone, then every level
        plate.sudo().with_company(self.company).bf_cost_unit = 99.0
        self.env["product.product"]._cron_bf_kitchen_refresh_all()
        self.assertAlmostEqual(self.cost(plate), 1.5)
        for product in (plate, self.dish, self.sauce):
            product.sudo().with_company(self.company).bf_cost_unit = 99.0
        self.env["product.product"]._cron_bf_kitchen_refresh_all()
        self.assertAlmostEqual(self.cost(self.sauce), 0.020)
        self.assertAlmostEqual(self.cost(plate), 1.5)
        self.assertAlmostEqual(self.cost(self.dish), 5.403, places=6)
        # a recipe deleted: no recipe, no cost, and the dishes follow
        self.sauce_bom.with_user(chef).unlink()
        self.assertEqual(self.cost(self.sauce), 0.0)
        self.assertEqual(self.cost(plate), 0.0)
        self.assertAlmostEqual(self.cost(self.dish), 240 * 0.020 + 3 * 0.001, places=6)

    def test_r2_entered_cost_kept_on_create_and_edit(self):
        """[R2 code 5] An entered or typed kitchen cost survives the creation and the edits."""
        chef = self.mkuser("r2_chef_cost", "bf_kitchen.group_kitchen_manager")
        Tmpl = self.env["product.template"].with_user(chef).with_context(
            default_bf_kitchen_type="ingredient", default_is_storable=True, default_sale_ok=False)
        view = "bf_kitchen.bf_kitchen_product_view_form"
        with Form(Tmpl, view=view) as form:
            form.name = "Ciboulette (essai)"
            form.bf_cost_manual = 0.0321
        first = form.record
        variant = first.product_variant_id.with_company(self.company)
        self.assertAlmostEqual(variant.bf_cost_manual, 0.0321)
        self.assertAlmostEqual(variant.bf_cost_unit, 0.0321)
        with Form(Tmpl, view=view) as form:
            form.name = "Persil (essai)"
            form.bf_cost_unit = 0.0123
        second = form.record.product_variant_id.with_company(self.company)
        self.assertAlmostEqual(second.bf_cost_unit, 0.0123)
        self.assertEqual(second.bf_cost_source, "manual")
        with Form(first.with_user(chef), view=view) as form:
            form.name = "Ciboulette fraîche (essai)"
        self.assertAlmostEqual(variant.bf_cost_manual, 0.0321, msg="another edit keeps the entered cost")
        with Form(first.with_user(chef), view=view) as form:
            form.bf_cost_unit = 0.0789
        self.assertAlmostEqual(variant.bf_cost_unit, 0.0789)
        self.assertAlmostEqual(variant.bf_cost_manual, 0.0789, msg="the typed cost is the entered cost")
        # both written at once: the entered cost cleared and a kitchen cost typed keeps the typed one
        first.with_user(chef).write({"bf_cost_unit": 0.07, "bf_cost_manual": 0.0})
        self.assertAlmostEqual(variant.bf_cost_manual, 0.07)
        first.with_user(chef).write({"bf_cost_manual": 0.06, "bf_cost_unit": 0.08})
        self.assertAlmostEqual(variant.bf_cost_manual, 0.06, msg="an entered cost given wins")
        # the engine's own fields are not written by hand
        with self.assertRaises(AccessError):
            variant.with_user(chef).bf_cost_unit = 0.01
        with self.assertRaises(AccessError):
            variant.with_user(chef).write({"bf_cost_source": "bill"})
        # a kitchen cost that comes from a bill is not typed over
        self.bill([(self.salmon, 1000.0, 0.020, {})])
        with self.assertRaises(UserError):
            self.salmon.product_tmpl_id.with_user(chef).bf_cost_unit = 0.01

    def test_r2_received_valued_at_bill_cost(self):
        """[R2 code 6] Received values the receipt at the bill line's cost, unrounded: in perpetual
        average cost, the stock interim account is settled. A line without product is not received."""
        self.company.anglo_saxon_accounting = True
        Account = self.env["account.account"]
        accounts = {key: Account.create({"name": "Stock %s (essai)" % key, "code": "12199%d" % i,
                                         "account_type": "asset_current", "reconcile": key == "in"})
                    for i, key in enumerate(("in", "out", "val"))}
        journal = self.env["account.journal"].create({"name": "Stock (essai)", "code": "STKE", "type": "general"})
        categ = self.env["product.category"].create({
            "name": "Cuisine AVCO (essai)", "property_cost_method": "average", "property_valuation": "real_time",
            "property_stock_account_input_categ_id": accounts["in"].id,
            "property_stock_account_output_categ_id": accounts["out"].id,
            "property_stock_valuation_account_id": accounts["val"].id,
            "property_stock_journal": journal.id})
        flour = self.ingredient("Farine (essai)", self.g, categ_id=categ.id)
        bill = self.bill([(flour, 1000.0, 0.002, {})], post=False)
        bill.action_bf_kitchen_receive()
        layers = self.env["stock.valuation.layer"].search([("product_id", "=", flour.id)])
        self.assertAlmostEqual(sum(layers.mapped("value")), 2.0, msg="not 0: the move price keeps 2 decimals")
        bill.action_post()
        interim = self.env["account.move.line"].search([
            ("account_id", "=", accounts["in"].id), ("parent_state", "=", "posted")])
        self.assertTrue(interim)
        self.assertAlmostEqual(sum(interim.mapped("balance")), 0.0, msg="the interim account is settled")
        # a line with no product is booked to an expense account: it is not received
        draft = self.bill([(None, 1.0, 40.0, {"name": "Sac de farine 20 kg"})], post=False)
        draft.invoice_line_ids.sudo().write({"bf_ingredient_id": flour.id, "bf_pack_qty": 20000.0})
        self.assertFalse(draft.bf_kitchen_can_receive)
        with self.assertRaises(UserError):
            draft.action_bf_kitchen_receive()

    def test_r2_portions_both_ways(self):
        """[R2 code 7] A preparation is checked line by line as a whole; a dish by the ingredients
        of one portion, sub-recipes exploded; a preparation that changes checks its dishes again."""
        chef = self.mkuser("r2_chef_portion", "bf_kitchen.group_kitchen_manager")
        Tmpl = self.env["product.template"].with_user(chef)
        Bom = self.env["mrp.bom"].with_user(chef)

        def product(name, kind, uom):
            return Tmpl.create({"name": name, "bf_kitchen_type": kind, "type": "consu", "list_price": 10.0,
                                "uom_id": uom.id, "uom_po_id": uom.id}).product_variant_id

        def recipe(prod, qty, uom, lines):
            return Bom.create({"product_tmpl_id": prod.product_tmpl_id.id, "type": "phantom", "product_qty": qty,
                               "product_uom_id": uom.id, "bom_line_ids": [
                                   Command.create({"product_id": p.id, "product_qty": q, "product_uom_id": self.g.id})
                                   for p, q in lines]})

        # 3 g of salt stocked in g in a sauce of 1000 g: accepted (it was refused)
        sauce_g = product("Sauce au sel (essai)", "preparation", self.g)
        sauce_g_bom = recipe(sauce_g, 1000.0, self.g, [(self.cream, 997.0), (self.salt_g, 3.0)])
        # 10 g of salt stocked in kg in a sauce of 1 kg: accepted as a sauce (0.01 kg)...
        sauce_kg = product("Sauce sel en kg (essai)", "preparation", self.g)
        recipe(sauce_kg, 1.0, self.kg, [(self.salt_kg, 10.0), (self.cream, 990.0)])
        # ...but a dish taking 30 g of it takes 0.3 g of salt, 0.00 kg: refused
        dish = product("Assiette 30 g (essai)", "dish", self.unit)
        with self.assertRaises(ValidationError) as caught:
            recipe(dish, 1.0, self.unit, [(sauce_kg, 30.0)])
        message = str(caught.exception)  # English or French, after the user's language
        self.assertIn("portion", message)
        self.assertIn("0.3 g", message)
        self.assertNotIn("rounding", message)
        self.assertNotIn("arrondi", message)
        # the same dish on the sauce in g is fine (0.09 g of salt); the sauce's salt turned into
        # 10 g of the kg one is fine for the sauce, refused for the dish
        recipe(dish, 1.0, self.unit, [(sauce_g, 30.0)])
        salt_line = sauce_g_bom.bom_line_ids.filtered(lambda l: l.product_id == self.salt_g)
        with self.assertRaises(ValidationError):
            salt_line.write({"product_id": self.salt_kg.id, "product_qty": 10.0})
        self.assertEqual(salt_line.product_id, self.salt_g)

    def test_r2_cancel_returns_what_is_left(self):
        """[R2 code 8] Cancelling sends back what is still on hand, never what was used."""
        move, line = self.receive()
        location = line.bf_receipt_move_id.location_dest_id

        def scrap(qty):
            self.env["stock.scrap"].create({"product_id": self.salmon.id, "scrap_qty": qty,
                                            "product_uom_id": self.g.id, "location_id": location.id}).action_validate()

        scrap(500.0)
        move.button_cancel()
        self.assertAlmostEqual(self.on_hand(self.salmon), 0.0, msg="1500 sent back, not 2000")
        self.assertTrue(any("500.0 g Saumon entier" in str(m.body) for m in move.message_ids),
                        "what was used is noted on the bill")
        move2, _line2 = self.receive(1.0)
        scrap(1000.0)
        move2.button_cancel()
        self.assertEqual(move2.state, "cancel")
        self.assertAlmostEqual(self.on_hand(self.salmon), 0.0, msg="not -1000")
        # a received draft bill switched into a credit note still sends its goods back
        move3, _line3 = self.receive(1.0)
        move3.action_switch_move_type()
        self.assertEqual(move3.move_type, "in_refund")
        move3.button_cancel()
        self.assertAlmostEqual(self.on_hand(self.salmon), 0.0, msg="the 1000 g went back")

    def test_r2_recipe_kitchen_type_follows_product(self):
        """[R2 code 9] The kitchen type of a recipe is its product's: not written on the recipe.
        A kit on an ingredient is not a recipe for the kitchen scope."""
        chef = self.mkuser("r2_chef_type", "bf_kitchen.group_kitchen_manager")
        Bom = self.env["mrp.bom"].with_user(chef)
        with self.assertRaises(AccessError):
            self.dish_bom.with_user(chef).bf_kitchen_type = False
        self.assertEqual(self.dish_bom.bf_kitchen_type, "dish")
        other = self.env["product.product"].create({"name": "Boîte à lunch", "type": "consu"})
        with self.assertRaises(AccessError):
            Bom.create({"product_tmpl_id": other.product_tmpl_id.id, "type": "phantom", "product_qty": 1.0,
                        "bf_kitchen_type": "dish"})
        self.assertFalse(other.product_tmpl_id.bf_kitchen_type)
        with self.assertRaises(AccessError):
            Bom.create({"product_tmpl_id": self.lemon.product_tmpl_id.id, "type": "phantom", "product_qty": 1.0})
        prep = self.env["product.template"].with_user(chef).create({
            "name": "Fond brun (essai)", "bf_kitchen_type": "preparation", "type": "consu",
            "uom_id": self.g.id, "uom_po_id": self.g.id})
        bom = Bom.with_context(default_bf_kitchen_type="ingredient").create({
            "product_tmpl_id": prep.id, "type": "phantom", "product_qty": 1000.0, "product_uom_id": self.g.id})
        # Odoo itself ignores a default for this related field: kept as a guard against a change
        self.assertEqual(bom.bf_kitchen_type, "preparation", "a default of the context does not stick")

    # ── forms, lists and printed sheets (18.0.1.0.2) ────────────────────────────────

    def _fr(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"]._load_module_terms(["bf_kitchen", "bf_food_allergen"], ["fr_CA"])
        return self.env(context=dict(self.env.context, lang="fr_CA"))

    def test_v2_attach_wizard_has_a_title(self):
        """[V 1] The Attach wizard opens under its own title, not « Odoo »."""
        move = self.bill([(None, 2.0, 10.0, {"name": "Crevettes 2 kg"})])
        action = move.invoice_line_ids.action_bf_kitchen_attach()
        self.assertTrue(action.get("name"), "the action carries a title")
        arch = self.env["bf.kitchen.attach.line"].get_views([(False, "form")])["views"]["form"]["arch"]
        self.assertIn('string="Attach to an ingredient"', arch)

    def test_v2_report_header_is_the_recipe_company(self):
        """[V 2, 7] The sheets carry the company of the recipe, not the one selected by the reader."""
        other = self.env["res.company"].create({"name": "Société de lecture"})
        self.env.user.company_ids = [Command.link(other.id)]
        Report = self.env["ir.actions.report"].with_context(allowed_company_ids=[other.id, self.company.id])
        self.assertEqual(Report.env.company, other)
        for xmlid in ("bf_kitchen.action_report_technical_sheet", "bf_kitchen.action_report_technical_sheet_cost",
                      "bf_kitchen.action_report_allergen_sheet"):
            html, _fmt = Report._render_qweb_html(xmlid, self.dish_bom.ids)
            html = html.decode()
            self.assertIn(self.company.name, html, xmlid)
            self.assertNotIn("Société de lecture", html, xmlid)
        html = Report._render_qweb_html("bf_kitchen.action_report_allergen_sheet", self.dish_bom.ids)[0].decode()
        self.assertRegex(html, r'<td[^>]*class="text-center"[^>]*style="text-align: center;"',
                         "the marks sit under their centred heading")

    def test_v2_report_numbers_in_the_reader_language(self):
        """[V 3] Quantities and percentages follow the language: 2,5 and 50 in French, not 2.5 and 50.0."""
        env = self._fr()
        bom = self.env["mrp.bom"].create({
            "product_tmpl_id": self.dish.product_tmpl_id.id, "type": "phantom", "product_qty": 1.0,
            "product_uom_id": self.unit.id,
            "bom_line_ids": [Command.create({"product_id": self.salt_g.id, "product_qty": 2.5,
                                             "product_uom_id": self.g.id}),
                             Command.create({"product_id": self.salmon.id, "bf_net_qty": 120.0,
                                             "product_uom_id": self.g.id})]})
        html = env["ir.actions.report"]._render_qweb_html(
            "bf_kitchen.action_report_technical_sheet_cost", bom.ids)[0].decode()
        self.assertRegex(html, r">\s*2,5\s*<")
        self.assertRegex(html, r">\s*120\s*<")
        self.assertRegex(html, r">\s*240\s*<")
        self.assertRegex(html, r">\s*50\s*</span>")
        self.assertNotRegex(html, r">\s*(2\.5|120\.0|240\.0|50\.0)\s*<")
        self.assertTrue(bom.with_env(env)._bf_format_number(1234.5).endswith("234,5"))
        self.assertEqual(bom.with_context(lang="en_CA")._bf_format_number(33.44, 1), "33.4")

    def test_v2_preparation_list_has_no_dish_columns(self):
        """[V 4] The preparation list shows neither a selling price nor a suggested price."""
        action = self.env.ref("bf_kitchen.bf_kitchen_preparation_action")
        list_view = action.view_ids.filtered(lambda v: v.view_mode == "list").view_id
        arch = self.env["mrp.bom"].get_views([(list_view.id, "list")])["views"]["list"]["arch"]
        for name in ("bf_price", "bf_price_suggested", "bf_food_cost_pct"):
            self.assertNotIn('name="%s"' % name, arch)
        self.assertIn('name="bf_cost_unit_display"', arch)

    def test_v2_cost_shown_per_kg_with_currency(self):
        """[V 8] 0.0275 $/g reads 27.50 $ per kg; a unit stays per unit."""
        self.bill([(self.salmon, 1000.0, 0.0275, {})])
        tmpl = self.salmon.product_tmpl_id.with_company(self.company)
        self.assertEqual(tmpl.bf_cost_display_uom_id, self.kg)
        self.assertAlmostEqual(tmpl.bf_cost_display, 27.5)
        self.bill([(self.lemon, 10.0, 0.55, {})])
        lemon = self.lemon.product_tmpl_id.with_company(self.company)
        self.assertEqual(lemon.bf_cost_display_uom_id, self.unit)
        self.assertAlmostEqual(lemon.bf_cost_display, 0.55)
        self.assertEqual(self.salt_kg.product_tmpl_id.bf_cost_display_uom_id, self.kg)
        arch = self.env["product.template"].get_views(
            [(self.env.ref("bf_kitchen.bf_kitchen_product_view_form").id, "form")])["views"]["form"]["arch"]
        self.assertRegex(arch, r'name="bf_cost_manual"[^>]*invisible="[^"]*bf_cost_source == \'bill\'')

    def test_v2_vendor_format_above_zero(self):
        """[V 6] A vendor format is above 0, or empty with no billed unit."""
        Info = self.env["product.supplierinfo"]
        base = {"partner_id": self.vendor.id, "product_tmpl_id": self.cream.product_tmpl_id.id, "price": 1.0}
        Info.create(base)
        with self.assertRaises(ValidationError):
            Info.create(dict(base, bf_pack_qty=0.0, bf_pack_uom_id=self.unit.id))
        with self.assertRaises(ValidationError):
            Info.create(dict(base, bf_pack_qty=-1.0))
        Info.create(dict(base, bf_pack_qty=2000.0, bf_pack_uom_id=self.unit.id))

    def test_v2_alert_in_the_manager_language(self):
        """[V 9] The plate cost activity is written in the language of the manager who receives it."""
        self._fr()
        manager = self.mkuser("v2_chef_fr", "bf_kitchen.group_kitchen_manager")
        manager.lang = "fr_CA"
        self.company.bf_kitchen_manager_id = manager
        # The bills are validated by someone working in English.
        self.env.user.lang = "en_US"
        self.env = self.env(context=dict(self.env.context, lang="en_US"))
        activity_type = self.env.ref("bf_kitchen.mail_activity_plate_cost")
        self.bill([(self.cream, 1000.0, 0.010, {}), (self.salt_g, 1000.0, 0.001, {}),
                   (self.salmon, 1000.0, 0.020, {})], invoice_date=self.today - timedelta(days=3))
        self.bill([(self.salmon, 1000.0, 0.030, {})], invoice_date=self.today - timedelta(days=1))
        acts = self.dish.product_tmpl_id.activity_ids.filtered(lambda a: a.activity_type_id == activity_type)
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts.user_id, manager)
        self.assertIn("Coût de l'assiette au-dessus de la cible", acts.summary)
        self.assertIn("Ingrédients", acts.note)

    # ── readable costs (18.0.1.0.3) ─────────────────────────────────────────────────

    def test_v3_preparation_cost_per_kg(self):
        """[V3 1] A preparation counted in g reads its cost per kg: 0.012 $/g reads 12.00 $ / kg,
        not 0.01 $ (the cent rounding of the cost per gram)."""
        self.bill([(self.cream, 1000.0, 0.012, {})])
        bom = self.sauce_bom.with_company(self.company)
        self.assertEqual(bom.bf_cost_unit_display_uom_id, self.kg)
        self.assertAlmostEqual(bom.bf_cost_unit_display, 12.0)
        self.assertTrue(bom.bf_cost_unit_display_text.endswith(" / kg"), bom.bf_cost_unit_display_text)
        self.assertIn("12", bom.bf_cost_unit_display_text)
        self.assertEqual(self.dish_bom.bf_cost_unit_display_uom_id, self.unit, "a dish stays per unit")
        action = self.env.ref("bf_kitchen.bf_kitchen_preparation_action")
        list_view = action.view_ids.filtered(lambda v: v.view_mode == "list").view_id
        arch = self.env["mrp.bom"].get_views([(list_view.id, "list")])["views"]["list"]["arch"]
        self.assertNotIn('name="bf_cost_portion"', arch)
        self.assertRegex(arch, r'name="bf_cost_unit_display"[^>]*width=')
        form = self.env["mrp.bom"].get_views(
            [(self.env.ref("bf_kitchen.bf_kitchen_bom_view_form").id, "form")])["views"]["form"]["arch"]
        self.assertIn('name="bf_cost_unit_display_text"', form)

    def test_v3_ingredient_cost_in_one_piece(self):
        """[V3 3] The ingredient form shows its readable cost as one text (no gap between the
        amount and its unit)."""
        self.bill([(self.salmon, 1000.0, 0.0275, {})])
        tmpl = self.salmon.product_tmpl_id.with_company(self.company)
        text = tmpl.bf_cost_display_text
        self.assertTrue(text.endswith(" / kg"), text)
        self.assertIn("27", text)
        self.assertIn(tmpl.currency_id.symbol, text)
        for xmlid in ("bf_kitchen.bf_kitchen_product_view_form", "bf_kitchen.product_template_form_view_kitchen"):
            arch = self.env.ref(xmlid).arch_db
            self.assertIn('name="bf_cost_display_text"', arch, xmlid)
            self.assertNotIn('name="bf_cost_display"', arch, xmlid)
