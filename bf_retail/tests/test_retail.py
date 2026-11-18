from datetime import date, timedelta

from psycopg2.errors import CheckViolation, UniqueViolation

from odoo import Command, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestRetail(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.company = env.company
        env.user.groups_id = [Command.link(env.ref("bf_retail.group_retail_manager").id)]
        cls.unit = env.ref("uom.product_uom_unit")
        cls.dozen = env.ref("uom.product_uom_dozen")
        cls.vendor = env["res.partner"].create({"name": "Grossiste Essai"})

        Categ = env["product.category"]
        cls.categ_store = Categ.create({
            "name": "Store", "bf_retail_managed": True,
            "bf_retail_margin_target": 30.0, "bf_retail_price_rounding": "0.99"})
        # Below the store: inherits "followed", sets its own target, inherits the rounding.
        cls.categ_milk = Categ.create({
            "name": "Milk", "parent_id": cls.categ_store.id, "bf_retail_margin_target": 20.0})
        cls.categ_other = Categ.create({"name": "Not a store category"})

        cls.company.write({
            "bf_retail_margin_target": 35.0,
            "bf_retail_price_rounding": "cent",
            "bf_retail_cost_max_age": 90,
            "bf_retail_price_gap_min": 0.02,
        })

        Product = env["product.product"]

        def product(name, categ, **kw):
            return Product.create(dict({
                "name": name, "type": "consu", "categ_id": categ.id,
                "uom_id": cls.unit.id, "uom_po_id": cls.unit.id,
                "taxes_id": [Command.clear()], "supplier_taxes_id": [Command.clear()],
                "property_account_expense_id": cls.company_data["default_account_expense"].id,
            }, **kw))

        cls.product = staticmethod(product)
        cls.chips = product("Chips", cls.categ_store, list_price=2.49)
        cls.milk = product("Milk 2 L", cls.categ_milk, list_price=4.99)
        cls.magazine = product("Magazine", cls.categ_other, list_price=9.99)
        cls.today = date.today()
        cls.Cost = env["bf.retail.cost"]
        cls.Change = env["bf.retail.price.change"]

    # ── helpers ──────────────────────────────────────────────────────────────────────
    def add_cost(self, product, cost, day=None, **kw):
        return self.Cost.create(dict({
            "product_id": product.id, "company_id": self.company.id,
            "date": day or self.today, "cost": cost, "source": "manual"}, **kw))

    def applied_change(self, product, day, cost, old, new):
        """Un lot appliqué d'une ligne, pour poser l'historique des prix d'un produit."""
        change = self.Change.create({"company_id": self.company.id, "date": day})
        change.write({"line_ids": [Command.create({
            "product_id": product.id, "cost": cost, "price_old": old, "price_new": new})]})
        change.action_apply()
        return change

    def bill(self, lines, invoice_date=None, post=True, move_type="in_invoice"):
        vals = []
        for product, qty, price, extra in lines:
            line = {"quantity": qty, "price_unit": price, "tax_ids": [Command.clear()]}
            if product:
                line["product_id"] = product.id
            else:
                line["name"] = extra.pop("name", "No product")
                line["account_id"] = self.company_data["default_account_expense"].id
            line.update(extra)
            vals.append(Command.create(line))
        move = self.env["account.move"].create({
            "move_type": move_type, "partner_id": self.vendor.id,
            "invoice_date": invoice_date or self.today, "invoice_line_ids": vals,
        })
        if post:
            move.action_post()
        return move

    def tax(self, amount, price_include=False):
        vals = {"name": "GST %s%s" % (amount, " incl" if price_include else ""),
                "amount_type": "percent", "amount": amount, "type_tax_use": "sale",
                "company_id": self.company.id,
                "country_id": self.company.account_fiscal_country_id.id}
        tax = self.env["account.tax"].create(vals)
        if price_include:
            # In Odoo 18 `price_include` is computed from `price_include_override`: the
            # override is what a test sets, and the flag follows.
            if "price_include_override" in tax._fields:
                tax.price_include_override = "tax_included"
            elif not tax._fields["price_include"].compute:
                tax.price_include = True
            else:
                self.skipTest("no writable price-included flag on account.tax here")
            self.assertTrue(tax.price_include, "the tax now says it is in the price")
        return tax

    def grid_template(self, price=1.00):
        """Un modèle à deux attributs croisés : aucune valeur n'appartient à une seule
        variante, donc aucune variante ne peut bouger de prix sans déplacer ses sœurs."""
        attributes = self.env["product.attribute"].browse()
        for name, values in (("Format", ("250 g", "1 kg")), ("Parfum", ("Nature", "Fraise"))):
            attributes |= self.env["product.attribute"].create({
                "name": name, "create_variant": "always",
                "value_ids": [Command.create({"name": value}) for value in values]})
        template = self.env["product.template"].create({
            "name": "Yogourt", "type": "consu", "categ_id": self.categ_store.id,
            "uom_id": self.unit.id, "uom_po_id": self.unit.id, "list_price": price,
            "company_id": self.company.id,
            "taxes_id": [Command.clear()], "supplier_taxes_id": [Command.clear()],
            "property_account_expense_id": self.company_data["default_account_expense"].id,
            "attribute_line_ids": [Command.create({
                "attribute_id": attribute.id,
                "value_ids": [Command.set(attribute.value_ids.ids)]})
                for attribute in attributes],
        })
        self.assertEqual(len(template.product_variant_ids), 4)
        return template

    # ── the category tree ────────────────────────────────────────────────────────────
    def test_managed_follows_the_category_tree(self):
        self.assertTrue(self.chips.bf_retail_managed, "its own category is followed")
        self.assertTrue(self.milk.bf_retail_managed, "the category above it is followed")
        self.assertFalse(self.magazine.bf_retail_managed)
        self.assertTrue(self.chips.product_tmpl_id.bf_retail_managed,
                        "the template says the same thing as its variant")

    def test_managed_recomputed_when_a_parent_changes(self):
        self.categ_store.bf_retail_managed = False
        self.assertFalse(self.milk.bf_retail_managed,
                         "a product under a category that stopped being followed is let go")
        self.categ_store.bf_retail_managed = True
        self.assertTrue(self.milk.bf_retail_managed)
        self.categ_milk.parent_id = self.categ_other
        self.assertFalse(self.milk.bf_retail_managed, "moved out from under the store")

    def test_target_and_rounding_are_inherited(self):
        self.assertAlmostEqual(self.categ_milk._bf_retail_target(self.company), 0.20,
                               msg="its own target wins")
        self.assertEqual(self.categ_milk._bf_retail_rounding(self.company), "0.99",
                         "no rounding of its own: the category above")
        self.assertAlmostEqual(self.categ_other._bf_retail_target(self.company), 0.35,
                               msg="no category target: the company's")
        self.assertEqual(self.categ_other._bf_retail_rounding(self.company), "cent")

    # ── the series ───────────────────────────────────────────────────────────────────
    def test_chain_is_rewritten_in_date_order(self):
        first = self.add_cost(self.chips, 1.00, self.today - timedelta(days=20))
        third = self.add_cost(self.chips, 1.20, self.today)
        self.assertFalse(first.has_previous, "the first cost has nothing before it")
        self.assertAlmostEqual(third.previous_cost, 1.00)
        self.assertAlmostEqual(third.delta, 0.20)
        self.assertAlmostEqual(third.delta_pct, 20.0)
        # A bill that arrives late lands between the two: the series is rewritten, not patched.
        second = self.add_cost(self.chips, 1.10, self.today - timedelta(days=10))
        self.assertAlmostEqual(second.previous_cost, 1.00)
        self.assertAlmostEqual(third.previous_cost, 1.10, msg="the row after it moved too")
        self.assertAlmostEqual(third.delta_pct, 9.09, places=2,
                               msg="le champ garde deux décimales")
        second.unlink()
        self.assertAlmostEqual(third.previous_cost, 1.00, msg="removing the middle re-chains")

    def test_chain_is_per_company_and_per_product(self):
        other = self.env["res.company"].create({"name": "Other store"})
        self.add_cost(self.chips, 1.00, self.today - timedelta(days=5))
        elsewhere = self.Cost.create({
            "product_id": self.chips.id, "company_id": other.id,
            "date": self.today, "cost": 9.00, "source": "manual"})
        self.assertFalse(elsewhere.has_previous,
                         "another company's series does not see this one")
        mine = self.add_cost(self.chips, 1.05)
        self.assertAlmostEqual(mine.previous_cost, 1.00)

    def test_the_current_cost_ignores_another_company(self):
        """Le survivant du mutant 3 : sans le filtre de société, le coût d'un autre commerce
        devient celui de celui-ci, et il est plus récent par son identifiant."""
        other = self.env["res.company"].create({"name": "Another store"})
        self.add_cost(self.chips, 1.00)
        self.Cost.create({"product_id": self.chips.id, "company_id": other.id,
                          "date": self.today, "cost": 9.00, "source": "manual"})
        product = self.chips.with_company(self.company)
        self.assertAlmostEqual(product.bf_retail_cost, 1.00,
                               msg="le coût d'une autre société n'est pas celui de ce commerce")
        self.assertEqual(product.bf_retail_cost_count, 1)
        self.assertAlmostEqual(self.chips.with_company(other).bf_retail_cost, 9.00)

    def test_the_change_cannot_be_written_by_hand(self):
        row = self.add_cost(self.chips, 1.00)
        with self.assertRaises(AccessError):
            row.delta_pct = 999.0

    def test_a_negative_cost_is_refused(self):
        # La contrainte visée est connue : c'est le CHECK de `cost_positive`, et non
        # n'importe quelle levée du chemin.
        with self.assertRaises(CheckViolation):
            with mute_logger("odoo.sql_db"):
                self.add_cost(self.chips, -1.0)

    def test_cost_on_the_product_is_the_last_one(self):
        self.add_cost(self.chips, 1.00, self.today - timedelta(days=2))
        self.add_cost(self.chips, 1.30, self.today)
        product = self.chips.with_company(self.company)
        self.assertAlmostEqual(product.bf_retail_cost, 1.30)
        self.assertEqual(product.bf_retail_cost_date, self.today)
        self.assertEqual(product.bf_retail_cost_count, 2)
        self.assertFalse(product.bf_retail_cost_stale)

    def test_an_old_cost_says_so(self):
        self.add_cost(self.chips, 1.00, self.today - timedelta(days=200))
        self.assertTrue(self.chips.with_company(self.company).bf_retail_cost_stale)

    # ── the bill writes the series ───────────────────────────────────────────────────
    def test_a_posted_bill_writes_the_cost_net_of_the_discount(self):
        move = self.bill([(self.chips, 10.0, 1.00, {"discount": 10.0})])
        rows = self.Cost.search([("product_id", "=", self.chips.id)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.source, "bill")
        self.assertAlmostEqual(rows.cost, 0.90, msg="net of the 10 % discount")
        self.assertEqual(rows.move_id, move)
        self.assertEqual(rows.partner_id, self.vendor)

    def test_a_bill_posted_again_does_not_double_the_series(self):
        move = self.bill([(self.chips, 10.0, 1.00, {})])
        move.button_draft()
        move.action_post()
        self.assertEqual(self.Cost.search_count([("product_id", "=", self.chips.id)]), 1)

    def test_a_case_bought_becomes_a_cost_per_unit(self):
        self.bill([(self.chips, 2.0, 12.00, {"product_uom_id": self.dozen.id})])
        row = self.Cost.search([("product_id", "=", self.chips.id)])
        self.assertAlmostEqual(row.cost, 1.00, msg="24.00 for 24 units")

    def test_a_bill_leaves_alone_what_the_store_does_not_follow(self):
        self.bill([(self.magazine, 5.0, 2.00, {}), (None, 1.0, 30.0, {"name": "Delivery"})])
        self.assertFalse(self.Cost.search([("product_id", "=", self.magazine.id)]))

    def test_a_credit_note_is_not_a_cost(self):
        self.bill([(self.chips, 10.0, 1.00, {})], move_type="in_refund")
        self.assertFalse(self.Cost.search([("product_id", "=", self.chips.id)]))

    # ── margin and suggested price ───────────────────────────────────────────────────
    def test_margin_is_computed_excluding_tax_either_way(self):
        self.add_cost(self.chips, 1.00)
        self.add_cost(self.milk, 1.00)
        self.chips.taxes_id = self.tax(15.0)                      # shelf price excludes it
        self.milk.taxes_id = self.tax(15.0, price_include=True)   # shelf price includes it
        self.chips.list_price = 2.00
        self.milk.list_price = 2.30
        chips = self.chips.with_company(self.company)
        milk = self.milk.with_company(self.company)
        self.assertAlmostEqual(chips.bf_retail_price_untaxed, 2.00, places=2)
        self.assertAlmostEqual(milk.bf_retail_price_untaxed, 2.00, places=2)
        self.assertAlmostEqual(chips.bf_retail_margin_pct, 50.0, places=1)
        self.assertAlmostEqual(milk.bf_retail_margin_pct, 50.0, places=1,
                               msg="the same margin on the same cost and the same money kept")

    def test_suggested_price_reaches_the_target_after_rounding(self):
        self.add_cost(self.chips, 1.00)
        product = self.chips.with_company(self.company)
        # Target 30 %: 1.00 / 0.70 = 1.4286, up to the next price ending in 0.99.
        self.assertAlmostEqual(product.bf_retail_price_suggested, 1.99)
        self.assertAlmostEqual(product.bf_retail_price_gap, 1.99 - 2.49)
        untaxed = product._bf_retail_untaxed(1.99, self.company)
        self.assertGreaterEqual((untaxed - 1.00) / untaxed, 0.30,
                                "rounding up never lands below the target")

    def test_suggested_price_with_a_price_included_tax(self):
        self.add_cost(self.milk, 2.00)
        self.milk.taxes_id = self.tax(15.0, price_include=True)
        product = self.milk.with_company(self.company)
        # Target 20 %: untaxed 2.50, shelf 2.875, up to the next 0.99 = 2.99.
        self.assertAlmostEqual(product.bf_retail_price_suggested, 2.99)
        untaxed = product._bf_retail_untaxed(2.99, self.company)
        self.assertGreaterEqual((untaxed - 2.00) / untaxed, 0.20)

    def test_no_cost_means_no_suggestion(self):
        product = self.chips.with_company(self.company)
        self.assertAlmostEqual(product.bf_retail_price_suggested, 0.0)
        self.assertAlmostEqual(product.bf_retail_price_gap, 0.0)

    # ── the batch ────────────────────────────────────────────────────────────────────
    def test_a_batch_proposes_what_drifted_and_skips_the_rest(self):
        self.add_cost(self.chips, 2.00)      # 2.00 / 0.70 = 2.857, up to 2.99: it must rise
        self.add_cost(self.milk, 1.00)
        # Already on its suggested price: left out, a label is not worth a cent.
        self.milk.list_price = self.milk.with_company(self.company).bf_retail_price_suggested
        change = self.Change.create({"company_id": self.company.id})
        change.action_propose()
        self.assertEqual(change.line_ids.product_id, self.chips)
        line = change.line_ids
        self.assertAlmostEqual(line.price_old, 2.49)
        self.assertAlmostEqual(line.price_new, 2.99)
        self.assertAlmostEqual(line.cost, 2.00)
        self.assertGreaterEqual(line.margin_new, line.target_pct - 0.1)

    def test_a_batch_leaves_a_decrease_alone_unless_it_is_asked_for(self):
        self.add_cost(self.chips, 1.00)      # suggested 1.99 against a shelf price of 2.49
        change = self.Change.create({"company_id": self.company.id})
        change.action_propose()
        self.assertFalse(change.line_ids,
                         "giving back a margin above target is a decision, not arithmetic")
        change.follow_down = True
        change.action_propose()
        self.assertEqual(change.line_ids.product_id, self.chips)
        self.assertAlmostEqual(change.line_ids.price_new, 1.99)

    def test_applying_writes_the_price_logs_it_and_queues_the_label(self):
        self.add_cost(self.chips, 2.00)
        change = self.Change.create({"company_id": self.company.id})
        change.action_propose()
        before = len(self.chips.product_tmpl_id.message_ids)
        change.action_apply()
        self.assertEqual(change.state, "applied")
        self.assertTrue(change.applied_at)
        self.assertAlmostEqual(self.chips.with_company(self.company).lst_price, 2.99)
        self.assertGreater(len(self.chips.product_tmpl_id.message_ids), before,
                           "the product says in its own thread that its price moved")
        queue = self.env["bf.retail.label.queue"].search([("product_id", "=", self.chips.id)])
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue.reason, "price")
        self.assertAlmostEqual(queue.price, 2.99)
        self.assertFalse(queue.printed, "a label is printed by a person, not by the write")

    def test_a_batch_whose_prices_do_not_move_still_applies_only_once(self):
        """Le mutant 18 : sans la garde d'état, un lot dont le prix neuf égale le prix en
        place se réappliquait, et remettait une étiquette en file pour rien. La garde de
        péremption ne le voit pas, puisque rien n'a bougé."""
        self.add_cost(self.chips, 2.00)
        change = self.Change.create({"company_id": self.company.id})
        change.write({"line_ids": [Command.create({
            "product_id": self.chips.id, "cost": 2.00,
            "price_old": self.chips.lst_price, "price_new": self.chips.lst_price})]})
        change.action_apply()
        Queue = self.env["bf.retail.label.queue"]
        self.assertEqual(Queue.search_count([("product_id", "=", self.chips.id)]), 1)
        with self.assertRaises(UserError):
            change.action_apply()
        self.assertEqual(Queue.search_count([("product_id", "=", self.chips.id)]), 1,
                         "aucune seconde étiquette")

    def test_applying_twice_is_refused(self):
        self.add_cost(self.chips, 2.00)
        change = self.Change.create({"company_id": self.company.id})
        change.action_propose()
        change.action_apply()
        with self.assertRaises(UserError):
            change.action_apply()

    def test_a_price_moved_under_the_batch_is_refused_by_name(self):
        self.add_cost(self.chips, 2.00)
        change = self.Change.create({"company_id": self.company.id})
        change.action_propose()
        self.chips.list_price = 3.49          # someone else moved it
        with self.assertRaises(UserError) as caught:
            change.action_apply()
        self.assertIn("Chips", str(caught.exception))
        self.assertEqual(change.state, "draft", "nothing is applied when one line is stale")

    def test_a_cost_moved_under_the_batch_is_refused(self):
        self.add_cost(self.chips, 2.00)
        change = self.Change.create({"company_id": self.company.id})
        change.action_propose()
        self.add_cost(self.chips, 2.40)       # a delivery arrived in between
        with self.assertRaises(UserError):
            change.action_apply()

    def test_an_empty_batch_is_refused(self):
        change = self.Change.create({"company_id": self.company.id})
        with self.assertRaises(UserError):
            change.action_apply()

    def test_the_same_product_cannot_be_in_a_batch_twice(self):
        self.add_cost(self.chips, 2.00)
        change = self.Change.create({"company_id": self.company.id})
        change.action_propose()
        with self.assertRaises(UniqueViolation):
            with mute_logger("odoo.sql_db"):
                change.write({"line_ids": [Command.create({
                    "product_id": self.chips.id, "price_new": 1.0})]})

    def test_a_cancelled_batch_goes_back_to_draft_and_no_further(self):
        change = self.Change.create({"company_id": self.company.id})
        change.action_cancel()
        self.assertEqual(change.state, "cancelled")
        change.action_back_to_draft()
        self.assertEqual(change.state, "draft")
        with self.assertRaises(UserError):
            change.action_back_to_draft()

    def test_the_sequence_names_the_batch(self):
        change = self.Change.create({"company_id": self.company.id})
        self.assertTrue(change.name.startswith("PRIX/"), change.name)

    # ── who sees what ────────────────────────────────────────────────────────────────
    def test_a_clerk_reads_the_queue_and_not_the_costs(self):
        clerk = new_test_user(self.env, login="clerk-retail", groups="bf_retail.group_retail_user")
        self.add_cost(self.chips, 1.00)
        queue = self.env["bf.retail.label.queue"].create({
            "product_id": self.chips.id, "company_id": self.company.id, "price": 2.49})
        self.assertTrue(queue.with_user(clerk).read(["product_id", "price"]))
        with self.assertRaises(AccessError):
            self.Cost.with_user(clerk).search([]).read(["cost"])
        with self.assertRaises(AccessError):
            self.Change.with_user(clerk).create({"company_id": self.company.id})

    def test_a_clerk_marks_a_label_printed(self):
        clerk = new_test_user(self.env, login="clerk-retail-2",
                              groups="bf_retail.group_retail_user")
        queue = self.env["bf.retail.label.queue"].create({
            "product_id": self.chips.id, "company_id": self.company.id, "price": 2.49})
        queue.with_user(clerk).action_mark_printed()
        self.assertTrue(queue.printed)
        self.assertEqual(queue.printed_by, clerk)

    def test_only_a_manager_applies_a_price(self):
        clerk = new_test_user(self.env, login="clerk-retail-3",
                              groups="bf_retail.group_retail_user")
        self.add_cost(self.chips, 2.00)
        change = self.Change.create({"company_id": self.company.id})
        change.action_propose()
        with self.assertRaises(AccessError):
            change.with_user(clerk).action_apply()

    # ── the index ────────────────────────────────────────────────────────────────────
    def test_the_index_reads_both_ends_and_the_spread(self):
        # Le coût de départ est AVANT le début de la période : l'indice ne compte qu'un
        # produit qui a un coût aux deux bouts.
        self.add_cost(self.chips, 1.00, self.today - timedelta(days=90))
        self.chips.list_price = 2.00
        change = self.Change.create({"company_id": self.company.id,
                                     "date": self.today - timedelta(days=30)})
        change.write({"line_ids": [Command.create({
            "product_id": self.chips.id, "cost": 1.00, "price_old": 2.00, "price_new": 2.20})]})
        change.action_apply()
        # The next delivery, after the shelf price moved.
        self.add_cost(self.chips, 1.20, self.today)
        index = self.env["bf.retail.index"].create({
            "company_id": self.company.id,
            "date_from": self.today - timedelta(days=60), "date_to": self.today})
        index.action_compute()
        line = index.line_ids.filtered(lambda l: l.categ_id == self.categ_store)
        self.assertEqual(line.product_count, 1)
        self.assertAlmostEqual(line.cost_index, 120.0, places=1)
        self.assertAlmostEqual(line.price_index, 110.0, places=1)
        self.assertAlmostEqual(line.spread, -10.0, places=1)
        self.assertAlmostEqual(index.spread, -10.0, places=1,
                               msg="the store followed the cost by less than the cost moved")

    def test_the_index_leaves_out_a_product_costed_only_once(self):
        self.add_cost(self.chips, 1.00, self.today)      # nothing at the start of the period
        index = self.env["bf.retail.index"].create({
            "company_id": self.company.id,
            "date_from": self.today - timedelta(days=30), "date_to": self.today})
        index.action_compute()
        self.assertEqual(index.product_count, 0,
                         "a product that arrived mid-period would show an infinite rise")

    def test_a_period_that_ends_before_it_starts_is_refused(self):
        with self.assertRaises(ValidationError):
            self.env["bf.retail.index"].create({
                "company_id": self.company.id,
                "date_from": self.today, "date_to": self.today - timedelta(days=1)})

    # ── les 13 constats du tour 1 ────────────────────────────────
    def test_two_variants_of_a_model_keep_their_own_price(self):
        """Constat 1 : la 2e ligne du lot écrivait `list_price` du modèle PARTAGÉ, et la
        variante de la 1re ligne portait un prix que personne n'avait choisi."""
        attribute = self.env["product.attribute"].create({
            "name": "Format", "create_variant": "always",
            "value_ids": [Command.create({"name": "250 g"}), Command.create({"name": "1 kg"})]})
        small, big = attribute.value_ids
        template = self.env["product.template"].create({
            "name": "Fromage", "type": "consu", "categ_id": self.categ_store.id,
            "uom_id": self.unit.id, "uom_po_id": self.unit.id, "list_price": 4.00,
            "taxes_id": [Command.clear()], "supplier_taxes_id": [Command.clear()],
            "property_account_expense_id": self.company_data["default_account_expense"].id,
            "attribute_line_ids": [Command.create({
                "attribute_id": attribute.id, "value_ids": [Command.set((small + big).ids)]})],
        })
        template.attribute_line_ids.product_template_value_ids.filtered(
            lambda value: value.name == "1 kg").price_extra = 6.00
        variants = template.product_variant_ids
        v_small = variants.filtered(
            lambda v: v.product_template_attribute_value_ids.name == "250 g")
        v_big = variants - v_small
        self.assertAlmostEqual(v_small.lst_price, 4.00)
        self.assertAlmostEqual(v_big.lst_price, 10.00)
        change = self.Change.create({"company_id": self.company.id})
        change.write({"line_ids": [
            Command.create({"product_id": v_small.id, "cost": 0.0,
                            "price_old": 4.00, "price_new": 4.99}),
            Command.create({"product_id": v_big.id, "cost": 0.0,
                            "price_old": 10.00, "price_new": 13.99})]})
        change.action_apply()
        self.assertAlmostEqual(v_big.lst_price, 13.99)
        self.assertAlmostEqual(v_small.lst_price, 4.99,
                               msg="la variante sœur ne bouge pas quand l'autre est chiffrée")
        queue = self.env["bf.retail.label.queue"].search([("product_id", "in", variants.ids)])
        self.assertEqual(sorted(round(price, 2) for price in queue.mapped("price")),
                         [4.99, 13.99], "l'étiquette dit le prix que la caisse chargera")

    def test_the_index_reads_the_price_at_the_end_of_the_period(self):
        """Constat 2 : le prix de la fin se lisait à AUJOURD'HUI et non à `date_to`, alors
        que les deux bouts du coût, eux, respectent la période."""
        # Le prix de départ est posé AVANT l'histoire que l'essai raconte : tout mouvement de
        # prix d'un article suivi est journalisé, et un prix de départ
        # écrit aujourd'hui serait POSTÉRIEUR aux lots datés d'il y a 70 jours. Les mesures de
        # l'essai, elles, sont les mêmes.
        self.chips.with_context(
            bf_retail_price_date=self.today - timedelta(days=100)).list_price = 2.00
        self.add_cost(self.chips, 1.00, self.today - timedelta(days=90))
        self.applied_change(self.chips, self.today - timedelta(days=70), 1.00, 2.00, 2.20)
        self.add_cost(self.chips, 1.20, self.today - timedelta(days=60))
        self.applied_change(self.chips, self.today, 1.20, 2.20, 3.00)
        index = self.env["bf.retail.index"].create({
            "company_id": self.company.id,
            "date_from": self.today - timedelta(days=80),
            "date_to": self.today - timedelta(days=50)})
        index.action_compute()
        line = index.line_ids.filtered(lambda l: l.categ_id == self.categ_store)
        self.assertAlmostEqual(line.cost_index, 120.0, places=1)
        self.assertAlmostEqual(line.price_end, 2.20, places=2,
                               msg="le prix de la fin est celui du dernier jour de la période")
        self.assertAlmostEqual(line.price_index, 110.0, places=1)
        self.assertAlmostEqual(line.spread, -10.0, places=1,
                               msg="le commerce a passé 10 points de moins que la hausse")

    def test_a_tax_of_another_company_is_not_this_store_s(self):
        """Constat 3 : le repli `or self.taxes_id` appliquait la taxe d'une AUTRE société,
        et la lire en tant qu'usager levait sur la règle d'enregistrement d'`account.tax`."""
        other = self.env["res.company"].create({"name": "Other store"})
        group = self.env["account.tax.group"].create(
            {"name": "Other store group", "company_id": other.id})
        foreign = self.env["account.tax"].create({
            "name": "GST 15 incl (other company)", "amount_type": "percent", "amount": 15.0,
            "type_tax_use": "sale", "company_id": other.id, "tax_group_id": group.id,
            "country_id": other.account_fiscal_country_id.id
            or self.company.account_fiscal_country_id.id})
        if "price_include_override" in foreign._fields:
            foreign.price_include_override = "tax_included"
        self.add_cost(self.chips, 1.00)
        self.chips.taxes_id = [Command.set(foreign.ids)]
        self.chips.list_price = 2.30
        product = self.chips.with_company(self.company)
        product.invalidate_recordset()
        self.assertAlmostEqual(product.bf_retail_price_untaxed, 2.30, places=2,
                               msg="aucune taxe de CE commerce : le prix affiché est hors taxe")
        self.assertAlmostEqual(product.bf_retail_margin_pct, 56.5, places=1)

    def test_a_corrected_bill_posted_again_corrects_the_series(self):
        """Constat 4 : une facture remise en brouillon, corrigée et repostée laissait la
        série sur un coût que le commerce n'a jamais payé, et rien ne le disait."""
        move = self.bill([(self.chips, 10.0, 1.00, {})])
        self.assertAlmostEqual(self.Cost.search([("product_id", "=", self.chips.id)]).cost, 1.00)
        move.button_draft()
        move.invoice_line_ids.price_unit = 2.00
        move.action_post()
        rows = self.Cost.search([("product_id", "=", self.chips.id)])
        self.assertEqual(len(rows), 1, "une seule ligne par ligne de facture")
        self.assertAlmostEqual(rows.cost, 2.00, msg="la série dit ce que la facture dit")

    def test_a_cost_entered_in_another_unit_is_converted(self):
        """Constat 5 : `uom_id` était décoratif sur le chemin manuel ; 12,00 la douzaine se
        lisait 12,00 l'unité (marge −381,9 %, prix proposé 17,99)."""
        row = self.add_cost(self.chips, 12.00, uom_id=self.dozen.id)
        self.assertAlmostEqual(row.cost, 1.00, msg="12,00 la douzaine, c'est 1,00 l'unité")
        self.assertEqual(row.uom_id, self.unit, "la série se tient dans l'unité du produit")
        product = self.chips.with_company(self.company)
        self.assertAlmostEqual(product.bf_retail_cost, 1.00)
        self.assertAlmostEqual(product.bf_retail_price_suggested, 1.99)
        with self.assertRaises(ValidationError):
            self.add_cost(self.chips, 3.00, uom_id=self.env.ref("uom.product_uom_kgm").id)

    def test_a_product_shared_with_another_store_is_refused_by_name(self):
        """Constat 6 et `list_price` n'est pas par société en Odoo 18. Un produit
        qu'aucun commerce ne possède, alors qu'un autre commerce suit le détail, est refusé
        et nommé, comme le fait la garde de péremption."""
        other = self.env["res.company"].create({"name": "Second store"})
        self.Cost.create({"product_id": self.milk.id, "company_id": other.id,
                          "date": self.today, "cost": 1.00, "source": "manual"})
        self.add_cost(self.chips, 2.00)
        change = self.Change.create({"company_id": self.company.id})
        # La proposition, elle, l'écarte et le nomme (C4 du ) : la ligne entre au lot à la
        # main, qui est le chemin qui reste, et l'application la refuse par son nom.
        change.action_propose()
        self.assertFalse(change.line_ids, "la proposition n'offre pas une ligne irrecevable")
        change.write({"line_ids": [Command.create(
            {"product_id": self.chips.id, "price_new": 2.99})]})
        with self.assertRaises(UserError) as caught:
            change.action_apply()
        self.assertIn("Chips", str(caught.exception))
        self.assertEqual(change.state, "draft")
        self.assertAlmostEqual(self.chips.with_company(self.company).lst_price, 2.49,
                               msg="rien n'est écrit quand une ligne est partagée")
        # Un produit qui appartient à CE commerce se chiffre sans refus.
        self.chips.product_tmpl_id.company_id = self.company.id
        change.action_apply()
        self.assertAlmostEqual(self.chips.with_company(self.company).lst_price, 2.99)

    def test_a_negative_bill_line_is_not_a_cost(self):
        """Constat 7 : la garde « jamais une note de crédit » était au mouvement et non à la
        ligne ; une ristourne de −5,00 devenait un coût de 5,00 venu « d'une facture »."""
        self.bill([(self.chips, 10.0, 1.00, {}), (self.milk, 1.0, -5.00, {})])
        self.assertFalse(self.Cost.search([("product_id", "=", self.milk.id)]),
                         "un crédit porté en ligne n'est pas un coût")
        self.assertAlmostEqual(self.Cost.search([("product_id", "=", self.chips.id)]).cost, 1.00)

    def test_the_engine_fields_are_not_opened_by_a_context_flag(self):
        """Constat 8 : la garde des quatre champs du moteur était un drapeau de contexte,
        donc forgeable depuis le client, et la fausse variation survivait en base."""
        self.add_cost(self.chips, 1.00, self.today - timedelta(days=3))
        row = self.add_cost(self.chips, 1.10)
        self.assertAlmostEqual(row.delta_pct, 10.0)
        with self.assertRaises(AccessError):
            row.with_context(bf_retail_engine=True).write({"delta_pct": 999.0})
        row.invalidate_recordset()
        self.assertAlmostEqual(row.delta_pct, 10.0, msg="la variation reste celle de la série")

    def test_the_price_read_in_another_unit_does_not_poison_the_cache(self):
        """Constat 9 : `uom` manquait à la clé de cache des calculs non stockés, là où
        `lst_price` le porte ; la valeur calculée à la douzaine était servie à l'unité."""
        self.add_cost(self.chips, 1.00)
        product = self.chips.with_company(self.company)
        product.invalidate_recordset()
        in_dozens = product.with_context(uom=self.dozen.id).bf_retail_price_untaxed
        self.assertAlmostEqual(in_dozens, 29.88, places=2)
        self.assertAlmostEqual(product.bf_retail_price_untaxed, 2.49, places=2,
                               msg="le prix à l'unité n'est pas celui calculé à la douzaine")

    def test_the_suggested_price_follows_a_cost_that_changes_company(self):
        """Constat 10 : le calcul du prix ne déclarait pas `bf_retail_cost_ids.company_id`,
        que le calcul du coût, lui, déclare."""
        other = self.env["res.company"].create({"name": "Third store"})
        row = self.Cost.create({"product_id": self.chips.id, "company_id": other.id,
                                "date": self.today, "cost": 1.00, "source": "manual"})
        product = self.chips.with_company(self.company)
        self.assertAlmostEqual(product.bf_retail_cost, 0.0)
        self.assertAlmostEqual(product.bf_retail_price_suggested, 0.0)
        row.company_id = self.company.id
        self.assertAlmostEqual(product.bf_retail_cost, 1.00)
        self.assertAlmostEqual(product.bf_retail_price_suggested, 1.99,
                               msg="le prix proposé est invalidé avec le coût")

    def test_a_line_added_by_hand_carries_what_it_is_applied_against(self):
        """Constat 11 : l'écran offrait une saisie de ligne que la garde de péremption
        refusait toujours, en nommant le produit comme si un tiers avait bougé son prix."""
        self.add_cost(self.chips, 2.00)
        change = self.Change.create({"company_id": self.company.id})
        change.write({"line_ids": [Command.create(
            {"product_id": self.chips.id, "price_new": 3.49})]})
        line = change.line_ids
        self.assertAlmostEqual(line.price_old, 2.49, msg="le prix en place est lu du produit")
        self.assertAlmostEqual(line.cost, 2.00)
        self.assertGreater(line.target_pct, 0.0)
        change.action_apply()
        self.assertEqual(change.state, "applied")
        self.assertAlmostEqual(self.chips.with_company(self.company).lst_price, 3.49)

    def test_an_archived_product_lets_go_of_a_category_that_stopped(self):
        """Constat 12 : le crochet de catégorie cherchait sans `active_test=False`, là où le
        déclencheur d'Odoo atteint les archivés ; le drapeau périmé attendait le réveil."""
        self.milk.active = False
        self.categ_store.bf_retail_managed = False
        self.assertFalse(self.milk.bf_retail_managed,
                         "un produit archivé suit sa catégorie comme les autres")
        self.milk.active = True
        self.assertFalse(self.milk.bf_retail_managed)
        self.assertFalse(self.milk.product_tmpl_id.bf_retail_managed)

    def test_a_clerk_does_not_rewrite_the_label(self):
        """Constat 13 : avec `perm_write=1` et aucune garde de champ, le commis choisissait
        le prix que l'étiquette annonce, le motif, puis le produit de la ligne."""
        clerk = new_test_user(self.env, login="clerk-retail-4",
                              groups="base.group_user,bf_retail.group_retail_user")
        queue = self.env["bf.retail.label.queue"].create({
            "product_id": self.chips.id, "company_id": self.company.id,
            "reason": "price", "price": 2.49})
        with self.assertRaises(AccessError):
            queue.with_user(clerk).write({"price": 0.99})
        with self.assertRaises(AccessError):
            queue.with_user(clerk).write({"product_id": self.milk.id})
        queue.invalidate_recordset()
        self.assertAlmostEqual(queue.price, 2.49)
        self.assertEqual(queue.product_id, self.chips)
        queue.with_user(clerk).action_mark_printed()
        self.assertTrue(queue.printed, "le commis marque imprimée, et rien d'autre")

    # ── les 13 constats du tour 2 ────────────────────────────────
    def test_a_retail_manager_writes_the_products_and_the_categories(self):
        """C1 et le groupe « Gérant du détail » n'avait aucun droit sur
        les produits. Il proposait le lot, puis `action_apply` levait « Access Denied by ACLs
        for operation: write, model: product.product » : le bouton central du module était
        inatteignable pour le rôle que le module crée. Il écrit les produits et les
        catégories, sans créer ni effacer."""
        manager = new_test_user(
            self.env, login="manager-retail",
            groups="base.group_user,bf_retail.group_retail_manager")
        self.add_cost(self.chips, 2.00)
        change = self.Change.create({"company_id": self.company.id})
        change.action_propose()
        self.assertEqual(change.line_ids.product_id, self.chips)
        change.with_user(manager).action_apply()
        self.assertEqual(change.state, "applied")
        self.assertAlmostEqual(self.chips.with_company(self.company).lst_price, 2.99)
        # La catégorie de détail est à lui aussi : le menu lui est ouvert.
        self.categ_store.with_user(manager).write({"bf_retail_margin_target": 25.0})
        self.assertAlmostEqual(self.categ_store.bf_retail_margin_target, 25.0)
        # Créer ou effacer un article reste à l'acheteur et au commis d'inventaire.
        with self.assertRaises(AccessError):
            self.env["product.template"].with_user(manager).create({"name": "Nouveau"})
        with self.assertRaises(AccessError):
            self.product("À jeter", self.categ_store).with_user(manager).unlink()

    def test_a_cost_dies_with_the_bill_line_it_was_read_from(self):
        """C2 : `move_line_id` était en `ondelete="set null"`. Une ligne de facture effacée à
        la correction laissait son coût orphelin — la ligne disparue n'était plus dans
        `(move.line_ids - lines)` et le ménage ne l'atteignait jamais : la série gardait 12,00
        et le prix proposé en sortait."""
        move = self.bill([(self.chips, 10.0, 12.00, {}), (self.milk, 1.0, 1.00, {})],
                         post=False)
        line = move.invoice_line_ids.filtered(lambda l: l.product_id == self.chips)
        row = self.Cost.create({
            "product_id": self.chips.id, "company_id": self.company.id,
            "date": self.today - timedelta(days=2), "cost": 12.00, "source": "bill",
            "move_line_id": line.id})
        later = self.add_cost(self.chips, 1.00)
        self.assertTrue(later.has_previous, "la série enchaîne les deux lignes")
        move.write({"invoice_line_ids": [Command.delete(line.id)]})
        self.assertFalse(row.exists(), "la ligne de série meurt avec la ligne de facture")
        later.invalidate_recordset()
        self.assertFalse(later.has_previous, "et la série est rechaînée sans elle")
        self.assertAlmostEqual(self.chips.with_company(self.company).bf_retail_cost, 1.00)

    def test_a_bill_sent_back_to_draft_or_cancelled_leaves_no_cost(self):
        """C3 : une facture annulée ou laissée en brouillon gardait son coût, rien n'étant
        accroché à `button_draft` / `button_cancel` : la série disait toujours 12,00."""
        move = self.bill([(self.chips, 10.0, 12.00, {})])
        self.assertAlmostEqual(
            self.Cost.search([("product_id", "=", self.chips.id)]).cost, 12.00)
        move.button_draft()
        self.assertFalse(self.Cost.search([("product_id", "=", self.chips.id)]),
                         "une facture en brouillon ne dit plus de coût")
        move.action_post()
        self.assertAlmostEqual(
            self.Cost.search([("product_id", "=", self.chips.id)]).cost, 12.00)
        move.button_cancel()
        self.assertFalse(self.Cost.search([("product_id", "=", self.chips.id)]),
                         "une facture annulée ne dit plus de coût")
        self.assertAlmostEqual(self.chips.with_company(self.company).bf_retail_cost, 0.0)

    def test_the_proposal_leaves_out_what_the_application_would_refuse(self):
        """C4 : les deux gardes des arbitrages 1 et 2 du étaient à l'application et la
        proposition ne les connaissait pas : elle remplissait le lot de lignes que
        `action_apply` refusait toujours, et reproposer remettait les mêmes. Écarter et
        nommer, pas refuser tout le lot."""
        other = self.env["res.company"].create({"name": "Second store"})
        self.Cost.create({"product_id": self.milk.id, "company_id": other.id,
                          "date": self.today, "cost": 1.00, "source": "manual"})
        self.add_cost(self.chips, 2.00)
        change = self.Change.create({"company_id": self.company.id})
        change.action_propose()
        self.assertFalse(change.line_ids,
                         "une ligne que l'application refuse toujours n'entre pas au lot")
        bodies = " ".join(change.message_ids.mapped("body"))
        self.assertIn("Chips", bodies, "le lot nomme ce qu'il a écarté au lieu de le taire")
        change.action_propose()
        self.assertFalse(change.line_ids, "reproposer ne remet pas la même ligne")
        # Une variante dont le prix ne peut pas bouger seule est écartée, nommée, et le reste
        # du lot reste applicable.
        self.chips.product_tmpl_id.company_id = self.company.id
        grid = self.grid_template()
        tied = grid.product_variant_ids[0]
        self.add_cost(tied, 2.00)
        change.action_propose()
        self.assertEqual(change.line_ids.product_id, self.chips,
                         "le reste du lot est proposé sans la variante liée")
        bodies = " ".join(change.message_ids.mapped("body"))
        self.assertIn(tied.display_name, bodies)
        change.action_apply()
        self.assertEqual(change.state, "applied")
        self.assertAlmostEqual(self.chips.with_company(self.company).lst_price, 2.99)

    def test_a_rebate_line_lowers_the_cost_really_paid(self):
        """C5 et une ristourne en ligne négative sur le même produit était
        écartée et le coût BRUT restait, pendant que le chatter annonçait qu'aucun coût n'avait
        été lu. 24 × 1,29 moins 5,00, c'est 1,0817 l'unité et non 1,2900."""
        move = self.bill([(self.chips, 24.0, 1.29, {}), (self.chips, 1.0, -5.00, {})])
        rows = self.Cost.search([("product_id", "=", self.chips.id)])
        self.assertEqual(len(rows), 1, "une facture donne une ligne de série par produit")
        self.assertAlmostEqual(rows.cost, 1.0817, places=4,
                               msg="le coût est ce qui a vraiment été payé par unité")
        self.assertAlmostEqual(
            self.chips.with_company(self.company).bf_retail_cost, 1.0817, places=4)
        self.assertNotIn("No retail cost read", " ".join(move.message_ids.mapped("body")),
                         "le chatter ne dit pas « aucun coût lu » pour un produit qui en a un")

    def test_two_lines_of_the_same_product_make_one_cost(self):
        """C6 : deux lignes du même produit sur une facture donnaient deux lignes de série le
        même jour, une hausse fantôme de +100 %, et le coût courant était la dernière par
        identifiant."""
        self.bill([(self.chips, 12.0, 1.00, {}), (self.chips, 12.0, 1.20, {})])
        rows = self.Cost.search([("product_id", "=", self.chips.id)])
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows.cost, 1.10, msg="26,40 payés pour 24 unités")
        self.assertFalse(rows.has_previous, "aucune hausse fantôme dans une seule facture")
        self.assertAlmostEqual(self.chips.with_company(self.company).bf_retail_cost, 1.10)

    def test_a_purchase_receipt_writes_the_cost(self):
        """C7 : un reçu d'achat (`in_receipt`, le dépanneur qui paie comptant) n'écrivait
        rien."""
        self.bill([(self.chips, 10.0, 1.00, {})], move_type="in_receipt")
        row = self.Cost.search([("product_id", "=", self.chips.id)])
        self.assertEqual(len(row), 1, "un reçu d'achat est un achat")
        self.assertAlmostEqual(row.cost, 1.00)
        self.assertEqual(row.source, "bill")

    def test_a_new_price_of_zero_is_kept(self):
        """C8 : un prix neuf à 0,00 tapé par le gérant était remplacé en silence par le prix
        proposé (`if not vals.get(field)` ne distingue pas absent de zéro), alors que la
        contrainte SQL permet 0."""
        self.add_cost(self.chips, 2.00)
        change = self.Change.create({"company_id": self.company.id})
        change.write({"line_ids": [Command.create(
            {"product_id": self.chips.id, "price_new": 0.0})]})
        line = change.line_ids
        self.assertAlmostEqual(line.price_new, 0.0, msg="le prix tapé est celui qui reste")
        self.assertAlmostEqual(line.price_old, 2.49,
                               msg="le reste de la ligne se lit quand même du produit")
        change.action_apply()
        self.assertAlmostEqual(self.chips.with_company(self.company).lst_price, 0.0)

    def test_a_store_with_only_a_cancelled_batch_is_not_a_second_store(self):
        """C9 et « un second commerce suit le détail » se déduisait d'un
        lot même annulé, et un commerce qui a renoncé bloquait à jamais tous les produits
        partagés. Un coût de détail ou un lot APPLIQUÉ, et rien d'autre."""
        other = self.env["res.company"].create({"name": "Store that gave up"})
        gave_up = self.Change.create({"company_id": other.id})
        gave_up.action_cancel()
        self.assertNotIn(other, self.env["res.company"]._bf_retail_stores(),
                         "un lot annulé ne fait pas d'une société un commerce de détail")
        self.add_cost(self.chips, 2.00)
        change = self.Change.create({"company_id": self.company.id})
        change.action_propose()
        change.action_apply()
        self.assertAlmostEqual(self.chips.with_company(self.company).lst_price, 2.99,
                               msg="le produit partagé se chiffre : l'autre a renoncé")
        self.magazine.product_tmpl_id.company_id = other.id
        applied = self.Change.create({"company_id": other.id})
        applied.write({"line_ids": [Command.create({
            "product_id": self.magazine.id, "cost": 0.0,
            "price_old": 9.99, "price_new": 10.99})]})
        applied.action_apply()
        self.assertIn(other, self.env["res.company"]._bf_retail_stores(),
                      "un lot appliqué, lui, fait d'une société un commerce de détail")

    def test_proposing_again_spares_a_line_decided_by_hand(self):
        """C10 : « Proposer depuis les coûts » détruisait sans un mot les lignes décidées à la
        main (`line_ids.unlink`, aucune confirmation sur le bouton)."""
        self.add_cost(self.chips, 2.00)
        self.add_cost(self.milk, 5.00)
        change = self.Change.create({"company_id": self.company.id})
        change.write({"line_ids": [Command.create(
            {"product_id": self.milk.id, "price_new": 5.99})]})
        by_hand = change.line_ids
        change.action_propose()
        self.assertTrue(by_hand.exists(),
                        "la ligne décidée à la main survit à une proposition")
        self.assertAlmostEqual(by_hand.price_new, 5.99, msg="et garde le prix décidé")
        self.assertIn(self.chips, change.line_ids.product_id)
        self.assertEqual(len(change.line_ids), 2, "le produit déjà au lot n'est pas reproposé")
        change.action_propose()
        self.assertEqual(len(change.line_ids), 2, "une seconde proposition non plus")
        arch = self.env.ref("bf_retail.bf_retail_price_change_view_form").arch
        self.assertRegex(arch, r'name="action_propose"[^>]*confirm=',
                         "le bouton demande confirmation avant de remplacer ses lignes")

    def test_a_cost_dated_in_the_future_is_not_the_current_cost(self):
        """C11 : un coût daté dans l'avenir devenait le coût courant pour toujours et ne
        vieillissait jamais, `(today − date).days` étant négatif. C'est aussi le D9 du lot 2 :
        le lecteur de fichiers de prix date légitimement un coût du jour où la liste entre en
        vigueur, et c'est la LECTURE, de ce côté-ci, qui ne doit pas le prendre pour le coût
        d'aujourd'hui."""
        self.add_cost(self.chips, 1.00, self.today - timedelta(days=200))
        future = self.add_cost(self.chips, 5.00, self.today + timedelta(days=7))
        product = self.chips.with_company(self.company)
        self.assertAlmostEqual(product.bf_retail_cost, 1.00,
                               msg="le coût courant est celui qui est en vigueur")
        self.assertEqual(product.bf_retail_cost_date, self.today - timedelta(days=200))
        self.assertTrue(product.bf_retail_cost_stale,
                        "et son âge se compte sur un coût en vigueur, pas sur un jour négatif")
        self.assertAlmostEqual(product.bf_retail_price_suggested, 1.99)
        self.assertTrue(future.exists(), "la ligne à venir reste dans la série")

    def test_a_refused_unit_names_the_line_of_the_batch(self):
        """C12 : une seule unité douteuse fait échouer tout le `create` d'un lot. Mesuré
        INATTEIGNABLE depuis le lot 2 : la contrainte reste, et son message nomme
        maintenant la ligne fautive."""
        with self.assertRaises(ValidationError) as caught:
            self.Cost.create([
                {"product_id": self.chips.id, "company_id": self.company.id,
                 "date": self.today, "cost": 1.00, "source": "manual"},
                {"product_id": self.milk.id, "company_id": self.company.id,
                 "date": self.today, "cost": 3.00, "source": "manual",
                 "uom_id": self.env.ref("uom.product_uom_kgm").id},
            ])
        message = str(caught.exception)
        # Dans la langue du lecteur, lue comme le module la lit : sur une base d'essai, le message sort en
        # français (« Ligne 2 sur 2 : »), et l'essai ne fige pas une langue.
        prefix = _("Line %(number)s of %(total)s: %(reason)s",
                   number=2, total=2, reason="").strip()
        self.assertIn(prefix, message, "le message nomme la ligne fautive du lot")
        self.assertIn("Milk 2 L", message)

    def test_the_index_sees_a_price_moved_by_hand(self):
        """C13 et l'indice ne voyait que les prix passés par un lot ; une
        hausse passée à la main se lisait comme « le commerce n'a rien passé ». Tout mouvement
        de prix d'un article suivi est journalisé, et l'indice lit le journal."""
        beans = self.product("Fèves", self.categ_store, list_price=2.00)
        self.add_cost(beans, 1.00, self.today - timedelta(days=90))
        self.add_cost(beans, 1.20, self.today - timedelta(days=30))
        beans.with_company(self.company).list_price = 2.20
        index = self.env["bf.retail.index"].create({
            "company_id": self.company.id,
            "date_from": self.today - timedelta(days=60), "date_to": self.today})
        index.action_compute()
        line = index.line_ids.filtered(lambda l: l.categ_id == self.categ_store)
        self.assertEqual(line.product_count, 1)
        self.assertAlmostEqual(line.price_start, 2.00, places=2,
                               msg="le prix du début est celui d'avant la hausse à la main")
        self.assertAlmostEqual(line.price_end, 2.20, places=2)
        self.assertAlmostEqual(line.cost_index, 120.0, places=1)
        self.assertAlmostEqual(line.price_index, 110.0, places=1)
        self.assertAlmostEqual(line.spread, -10.0, places=1,
                               msg="une hausse passée à la main est une hausse passée")

    def test_a_cost_row_of_a_bill_is_rewritten_from_its_lines(self):
        """La deuxième ceinture du repostage, mesurée pour elle-même : une ligne de série qui
        porte encore la ligne d'une facture est RÉÉCRITE depuis les lignes, et non sautée comme
        avant le tour 1. Ce n'est pas un des 13 constats : depuis que le brouillon et
        l'annulation retirent les coûts (C3), un repostage passe par une création, et sans cet
        essai la branche de réécriture ne serait plus mesurée par personne."""
        move = self.bill([(self.chips, 10.0, 1.00, {})])
        row = self.Cost.search([("product_id", "=", self.chips.id)])
        self.assertEqual(row.move_line_id, move.invoice_line_ids)
        row.write({"cost": 9.00})
        move._bf_retail_write_costs()
        row.invalidate_recordset()
        self.assertAlmostEqual(row.cost, 1.00, msg="le coût est réécrit depuis la ligne")
        self.assertEqual(self.Cost.search_count([("product_id", "=", self.chips.id)]), 1,
                         "et la ligne de série n'est pas doublée")

    # ── les deux survivants de la passe de mutation du tour 2 ─────────────────
    def test_a_product_fully_credited_on_its_bill_writes_no_cost(self):
        """M1 (mutant 34) : la garde refuse ce que la facture laisse à zéro **ou en dessous**,
        `qty <= 0` **ou** `paid <= 0`. `test_a_negative_bill_line_is_not_a_cost` ne touche que
        la première moitié — une ligne de crédit seule laisse `qty = 0`. Ici 10 unités à 1,00
        et une ristourne de −10,00 sur le MÊME produit donnent `qty = 10` et `paid = 0,00` :
        sans la moitié `paid <= 0`, la série recevrait un coût de 0,00 « venu d'une facture »,
        la marge passerait à 100 % et le prix proposé s'effondrerait."""
        move = self.bill([(self.chips, 10.0, 1.00, {}), (self.chips, 1.0, -10.00, {})])
        # Ce que la facture dit vraiment, pour que l'essai ne puisse pas passer par la PREMIÈRE
        # moitié de la garde : la quantité reçue est positive, c'est le montant payé qui est nul.
        lines = move.line_ids.filtered(
            lambda line: line.display_type == "product" and line.product_id
            and line.product_id.bf_retail_managed)
        read = move._bf_retail_per_product(lines)[0][self.chips]
        self.assertAlmostEqual(read[0], 10.0, msg="les 10 unités sont bien reçues")
        self.assertAlmostEqual(read[1], 0.0, places=2, msg="et la facture n'en paie rien")
        self.assertFalse(self.Cost.search([("move_id", "=", move.id)]),
                         "un produit entièrement ristourné n'écrit aucun coût")
        self.assertFalse(self.Cost.search([("product_id", "=", self.chips.id)]),
                         "et la série du produit reste vide")
        # Dit à voix haute sur la facture, et lu par la MÊME fonction de traduction que le
        # module appelle, **sous la langue du commerce** : un corps que le chatter garde sort
        # dans la langue de la société du commerce et non dans celle du contexte, qui n'en
        # porte aucune hors requête web (16ᵉ constat ; `api.py` l. 788). L'attendu se bâtit
        # donc sur le même enregistrement relié que le module emploie, sans quoi il mesurerait
        # la langue de l'essai au lieu de celle du commerce.
        # Mesuré ici est le début de la phrase, avec le nom du produit : la suite porte une
        # apostrophe, que le champ HTML du chatter est libre d'encoder.
        from odoo.addons.bf_retail.models.res_company import (
            _bf_retail_dans_la_langue_du_commerce as dans_la_langue)
        named = dans_la_langue(move, move.company_id).env._(
            "No retail cost read for %s: the line costs nothing, or its quantity does "
            "not convert to the unit of the product.",
            self.chips.display_name).split(":")[0].strip()
        self.assertIn(named, " ".join(move.message_ids.mapped("body")),
                      "le chatter de la facture nomme le produit au lieu de le taire")

    def test_the_journal_sees_the_price_of_a_variant_moved_by_hand(self):
        """M2 (mutant 53) : `product.template.attribute.value.write` est la SECONDE porte du
        journal des prix — le prix d'une variante vit dans son `price_extra` (
        arbitrage), et c'est par là qu'il passe. `test_the_index_sees_a_price_moved_by_hand`
        écrit `list_price` sur le modèle, donc la PREMIÈRE porte, et personne ne lisait le
        journal après un prix de variante bougé.

        Le prix est bougé par `_bf_retail_write_shelf_price`, et non par un `price_extra` écrit
        à cru : c'est le chemin que le module emploie lui-même pour une variante (`lst_price`
        écrirait le prix du modèle PARTAGÉ et bougerait la sœur), il traverse la porte mesurée,
        et hors d'un lot — aucun `bf_retail_price_line` au contexte — le mouvement est « à la
        main », ce qui est le geste du gérant que le journal doit retenir."""
        attribute = self.env["product.attribute"].create({
            "name": "Format", "create_variant": "always",
            "value_ids": [Command.create({"name": "250 g"}), Command.create({"name": "1 kg"})]})
        template = self.env["product.template"].create({
            "name": "Beurre", "type": "consu", "categ_id": self.categ_store.id,
            "uom_id": self.unit.id, "uom_po_id": self.unit.id, "list_price": 4.00,
            "taxes_id": [Command.clear()], "supplier_taxes_id": [Command.clear()],
            "property_account_expense_id": self.company_data["default_account_expense"].id,
            "attribute_line_ids": [Command.create({
                "attribute_id": attribute.id,
                "value_ids": [Command.set(attribute.value_ids.ids)]})],
        })
        variants = template.product_variant_ids
        self.assertEqual(len(variants), 2)
        self.assertTrue(all(variants.mapped("bf_retail_managed")),
                        "les deux variantes sont sous une catégorie suivie")
        small = variants.filtered(
            lambda variant: variant.product_template_attribute_value_ids.name == "250 g")
        big = variants - small
        Move = self.env["bf.retail.price.move"]
        self.assertFalse(Move.search([("product_id", "in", variants.ids)]),
                         "la mise en place du modèle ne journalise rien")
        big._bf_retail_write_shelf_price(10.00)
        self.assertAlmostEqual(big.lst_price, 10.00, places=2)
        self.assertAlmostEqual(small.lst_price, 4.00, places=2, msg="la sœur garde son prix")
        moves = Move.search([("product_id", "in", variants.ids)])
        self.assertEqual(len(moves), 1,
                         "une seule ligne de journal : celle de la variante bougée")
        self.assertEqual(moves.product_id, big)
        self.assertAlmostEqual(moves.price_old, 4.00, places=2,
                               msg="de l'ancien prix de la variante")
        self.assertAlmostEqual(moves.price_new, 10.00, places=2, msg="au neuf")
        self.assertEqual(moves.source, "hand", "bougé à la main, hors de tout lot")
        self.assertEqual(moves.company_id, self.company)
        self.assertFalse(Move.search([("product_id", "=", small.id)]),
                         "et rien du tout pour sa sœur")


    # ── la langue d'un corps que le chatter garde (16ᵉ constat) ──────────────────────────
    def test_the_helper_reads_the_language_of_the_store_and_only_when_there_is_one(self):
        """16ᵉ constat, arbitré : un corps **stocké** sort dans la langue du commerce, qu'un
        humain ou un cron l'écrive. Mesuré dans la source d'Odoo 18 : `env.lang` n'est que
        `context.get("lang")` (`api.py` l. 765) et `env._` retombe sur `en_US` quand il est
        vide (l. 788) — hors requête web, le contexte ne porte aucune langue.

        ⚠️ L'essai mesure **la langue sous laquelle le corps est rendu**, et jamais le texte
        traduit : la passe tourne sans montée (`TEST_SANS_U=1`), activer une langue ici
        n'importe **pas** le `.po` du module, et comparer du français serait rouge pour la
        mauvaise raison. `_activate_lang` ne charge aucun catalogue, il rend seulement la
        langue active — ce que `env.lang` exige pour ne pas lever.

        La langue du commerce se lit sur son contact : `res.company` n'a pas de champ `lang`.
        Vide, **rien ne change** — c'est le comportement d'aujourd'hui, et la deuxième moitié
        de l'essai est là pour ça. 🔴 Cette moitié-là ne mesurait rien : la base d'une base d'essai n'a
        que `fr_CA` et `en_CA` actives, et la sortie « langue inactive » rendait le mutant 55
        (`... or "en_US"`) équivalent. Elle active donc `en_US` d'abord."""
        from odoo.addons.bf_retail.models.res_company import (
            _bf_retail_dans_la_langue_du_commerce as dans_la_langue)
        self.env["res.lang"]._activate_lang("fr_CA")
        self.assertFalse(self.env.context.get("lang"),
                         "le contexte de l'essai ne porte pas de langue, comme un cron")

        # Un commerce dont le contact lit le français.
        self.company.partner_id.lang = "fr_CA"
        move = self.bill([(self.chips, 10.0, 1.00, {})])
        for enregistrement in (self.company, move, self.chips.product_tmpl_id):
            dit = dans_la_langue(enregistrement, self.company)
            self.assertEqual(dit.env.context.get("lang"), "fr_CA",
                             "l'auxiliaire rend l'enregistrement dans la langue du commerce")
            self.assertEqual(dit.env.lang, "fr_CA", "et `env.lang` la voit")
            self.assertEqual(dit, enregistrement, "le même enregistrement, un autre contexte")

        # Un commerce qui n'en dit rien : rien ne change, le contexte courant tient.
        #
        # 🔴 `en_US` est ACTIVÉE ici, et c'est elle qui rend cette moitié discriminante. La
        # base d'une base d'essai ne porte que `fr_CA` et `en_CA` actives (mesuré en base), donc
        # `_get_code("en_US")` rendait **False** et la TROISIÈME sortie de l'auxiliaire — la
        # langue qui n'est plus active — avalait toute langue imposée à un commerce qui n'en
        # déclare aucune : le mutant 55, qui écrit `company.partner_id.lang or "en_US"`,
        # rendait donc le même enregistrement inchangé et survivait. En service, `en_US` est
        # la langue de base d'Odoo et elle est active : la seule chose qui sépare alors
        # « rien ne change » de « l'anglais imposé » est la décision elle-même, et c'est elle
        # qu'il faut mesurer. Activer une langue ne charge aucun catalogue (`_activate_lang`
        # écrit `active`, là où `toggle_active` importerait les traductions), donc rien ici
        # ne dépend d'une montée.
        self.env["res.lang"]._activate_lang("en_US")
        self.assertTrue(self.env["res.lang"]._get_code("en_US"),
                        "en_US est active : plus aucune sortie n'avale une langue imposée")
        autre = self.env["res.company"].create({"name": "Commerce sans langue"})
        autre.partner_id.lang = False
        inchange = dans_la_langue(move, autre)
        self.assertFalse(inchange.env.context.get("lang"),
                         "langue vide : le contexte courant reste celui d'avant")
        self.assertEqual(inchange.env.context, move.env.context,
                         "et pas une seule clé du contexte ne bouge")

    def test_the_helper_survives_a_language_the_store_declared_then_lost(self):
        """La TROISIÈME sortie de l'auxiliaire, celle que le avait annoncée **non
        couverte** — à tort : `_activate_lang` n'importe aucun catalogue, donc la scène se
        monte sans montée de module.

        🔴 Ce qu'elle garde : `env.lang` LÈVE sur un code qui n'est plus actif (`api.py`
        l. 770, `Invalid language code`), et `res.partner.lang` **garde sa valeur en base**
        quand on retire la langue ensuite. Sans cette sortie, le cron qui valide une facture
        fournisseur serait tué par le corps de son propre chatter — un commerce qui a dit
        « je lis l'allemand » puis dont on retire l'allemand ne doit pas cesser de pouvoir
        recevoir une facture."""
        from odoo.addons.bf_retail.models.res_company import (
            _bf_retail_dans_la_langue_du_commerce as dans_la_langue)
        Lang = self.env["res.lang"]
        Lang._activate_lang("de_DE")
        autre = self.env["res.company"].create({"name": "Commerce en allemand"})
        autre.partner_id.lang = "de_DE"
        move = self.bill([(self.chips, 10.0, 1.00, {})])
        # La langue est retirée APRÈS coup. Par le SQL et non par l'ORM : ce qu'on met en
        # scène est l'ÉTAT (un contact qui porte un code inactif), que la production atteint
        # en désactivant la langue — et les gardes d'Odoo sur `res.lang.write` ne sont pas
        # ce qu'on éprouve ici.
        self.env.cr.execute("UPDATE res_lang SET active = false WHERE code = 'de_DE'")
        # ⚠️ `invalidate_model` ne suffit PAS : `res.lang._get_code` lit `_get_data`, qui est
        # un `ormcache` du registre et non le cache de modèle. Sans ce vidage, la sonde
        # répondait « de_DE est encore active » et l'essai ne mesurait rien.
        self.env.registry.clear_cache()
        self.assertEqual(autre.partner_id.lang, "de_DE",
                         "le contact garde la langue qu'on vient de retirer")
        self.assertFalse(Lang._get_code("de_DE"), "et elle n'est plus active")

        dit = dans_la_langue(move, autre)
        self.assertEqual(dit, move, "l'auxiliaire rend l'enregistrement, et ne lève pas")
        self.assertFalse(dit.env.context.get("lang"),
                         "aucune langue morte n'entre dans le contexte")
        # Le chemin réel : le corps s'écrit, et la facture reste validée.
        dit._message_log(body=dit.env._("Essai."))
        self.assertTrue(move.message_ids, "le fil a bien reçu son corps")

    def test_a_bill_validated_outside_a_request_logs_in_the_language_of_the_store(self):
        """Le chemin réel, et celui du défaut : un cron valide une facture fournisseur, le
        contexte ne porte pas de langue, et le corps du chatter était figé en base **en
        anglais** pour un lecteur fr_CA.

        L'espion relève la langue **du contexte sous lequel `_message_log` est appelé**, et
        non le texte : sans montée du module, le `.po` n'est pas en base et le texte sortirait
        en anglais même avec la bonne langue au contexte. C'est le contexte qui porte la
        décision, et c'est lui qui est mesuré."""
        from unittest.mock import patch
        self.env["res.lang"]._activate_lang("fr_CA")
        self.company.partner_id.lang = "fr_CA"
        self.assertFalse(self.env.context.get("lang"),
                         "aucune langue au contexte, comme hors requête web")
        Move = type(self.env["account.move"])
        origine = Move._message_log
        langues = []

        def espion(self, *args, **kwargs):
            langues.append(self.env.context.get("lang"))
            return origine(self, *args, **kwargs)

        with patch.object(Move, "_message_log", espion):
            # Une ligne qui ne coûte rien : la facture écrit le corps « aucun coût lu ».
            move = self.bill([(self.chips, 10.0, 0.0, {})])
        self.assertTrue(langues, "la facture validée a bien écrit au chatter")
        self.assertEqual(langues, ["fr_CA"] * len(langues),
                         "chaque corps que la facture garde est rendu en langue du commerce")
        self.assertTrue(move.message_ids, "et le message est bien au fil")

    # ── un rapport au chatter ne tue pas le geste du gérant ────────
    def test_a_manager_without_an_email_applies_a_batch(self):
        """🔴 Mesuré sur une base semée, en parcours réel : un gérant **sans adresse courriel** qui
        appliquait un lot voyait les prix s'écrire puis TOUT être annulé par la levée de son
        propre rapport de chatter — `message_post` exige une adresse d'auteur
        (`_message_compute_author(..., raise_on_email=True)`, `mail_thread.py` l. 2924) et
        `res.partner.email_formatted` rend **False** sans courriel (`res_partner.py` l. 572).
        Le lot restait brouillon, l'étiquette n'était pas en file, et le commis ne comprenait
        pas pourquoi. Un commis ou un gérant de dépanneur n'a pas forcément d'adresse : le
        banc ne le voyait pas, `new_test_user` donnant un courriel à tous ses usagers
        (`common.py` l. 226).

        Un rapport « voici ce que le module vient de faire » n'a personne à notifier :
        `_message_log` écrit la même note sans notification et sans exiger d'adresse
        (`raise_on_email=False`, l. 2866)."""
        manager = new_test_user(
            self.env, login="manager-no-email",
            groups="base.group_user,bf_retail.group_retail_manager", email=False)
        self.assertFalse(manager.email, "l'usager d'essai n'a pas d'adresse, comme un commis")
        self.assertFalse(manager.partner_id.email_formatted,
                         "et son contact n'en rend aucune : c'est ce que la levée lisait")
        self.add_cost(self.chips, 2.00)
        change = self.Change.create({"company_id": self.company.id})
        change.action_propose()
        self.assertEqual(change.line_ids.product_id, self.chips)
        avant_produit = len(self.chips.product_tmpl_id.message_ids)
        avant_lot = set(change.message_ids.ids)
        courriels = self.env["mail.mail"].search_count([])

        # Le parcours RÉEL, et non celui d'une base d'essai : le contexte de classe porte
        # `tracking_disable`, qui coupe le suivi de champs d'Odoo. Or `state` est
        # `tracking=True`, et son écriture précède le rapport : `_message_track` poste par
        # `message_post` **dès qu'un `_track_subtype` existe** (`mail_thread.py` l. 651) et
        # retombe sur `_message_log` sinon (l. 658). Aucun des modèles des deux lots ne
        # déclare de `_track_subtype`, donc le suivi ne lève pas — mesuré ici plutôt que
        # supposé, en rendant le suivi.
        change.with_user(manager).with_context(tracking_disable=False).action_apply()

        # Le geste a tenu de bout en bout : le prix est écrit, l'étiquette est en file.
        self.assertEqual(change.state, "applied")
        self.assertAlmostEqual(self.chips.with_company(self.company).lst_price, 2.99)
        queue = self.env["bf.retail.label.queue"].search([("product_id", "=", self.chips.id)])
        self.assertEqual(len(queue), 1, "l'étiquette est en file")
        self.assertAlmostEqual(queue.price, 2.99)
        self.assertEqual(len(self.chips.product_tmpl_id.message_ids), avant_produit + 1,
                         "le produit dit dans son fil que son prix a bougé")
        # Et le rapport est au fil du lot. Il est reconnu par la MÊME fonction de traduction
        # que le module appelle, sous la langue du commerce : comparer de l'anglais écrit à
        # la main serait rouge dès que le commerce lit le français.
        from odoo.addons.bf_retail.models.res_company import (
            _bf_retail_dans_la_langue_du_commerce as dans_la_langue)
        attendu = dans_la_langue(change, self.company).env._("%s price(s) applied.", 1)
        nouvelles = change.message_ids.filtered(lambda note: note.id not in avant_lot)
        self.assertTrue(nouvelles, "le lot a écrit à son fil")
        self.assertIn(attendu, " ".join(nouvelles.mapped("body")),
                      "le rapport du module est au fil, dans la langue du commerce")
        for note in nouvelles:
            self.assertEqual(note.author_id, manager.partner_id, "écrite par le gérant")
            self.assertFalse(note.email_from,
                             "et sans adresse d'expéditeur, ce qui est tout le sujet")
            self.assertEqual(note.subtype_id, self.env.ref("mail.mt_note"),
                             "une note interne, comme avant la correction")
            self.assertFalse(note.notification_ids,
                             "un rapport du module ne notifie personne : c'est l'échange")
        self.assertEqual(self.env["mail.mail"].search_count([]), courriels,
                         "et rien ne part de la file de courriels")

    def test_a_manager_without_an_email_is_told_what_the_proposal_left_out(self):
        """Le même défaut à la PREMIÈRE porte : le rapport de ce que la proposition écarte
        (`_bf_retail_name_what_is_left_out`). Un gérant sans adresse ne pouvait pas même
        proposer un lot dès qu'un produit était à écarter — et c'est précisément le cas où le
        lot a quelque chose à dire."""
        manager = new_test_user(
            self.env, login="manager-no-email-2",
            groups="base.group_user,bf_retail.group_retail_manager", email=False)
        self.assertFalse(manager.partner_id.email_formatted)
        # Un second commerce suit le détail : le produit qu'aucun des deux ne possède est
        # écarté et NOMMÉ.
        other = self.env["res.company"].create({"name": "Second store"})
        self.Cost.create({"product_id": self.milk.id, "company_id": other.id,
                          "date": self.today, "cost": 1.00, "source": "manual"})
        self.add_cost(self.chips, 2.00)
        change = self.Change.create({"company_id": self.company.id})

        change.with_user(manager).action_propose()

        self.assertFalse(change.line_ids,
                         "une ligne que l'application refuserait toujours n'entre pas au lot")
        self.assertEqual(len(change.message_ids), 1, "et le lot dit ce qu'il a écarté")
        note = change.message_ids[0]
        self.assertIn("Chips", note.body, "en nommant le produit")
        self.assertEqual(note.author_id, manager.partner_id)
        self.assertFalse(note.email_from)
