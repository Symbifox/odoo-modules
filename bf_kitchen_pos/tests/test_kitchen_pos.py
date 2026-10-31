"""The register: a dish sold takes its ingredients out of the stock; refunded, it is a loss.

The fixtures are bf_kitchen's TestKitchen (a dish of 240 g of salmon, 30 g of a cream sauce and
3 g of salt). Odoo 18 runs only the test methods a class defines itself."""
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.bf_kitchen.tests.test_kitchen import TestKitchen


@tagged("post_install", "-at_install")
class TestKitchenPos(TestKitchen):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company.point_of_sale_update_stock_quantities = "real"
        cls.company_data["default_journal_cash"].pos_payment_method_ids.unlink()
        cls.cash = cls.env["pos.payment.method"].create({
            "name": "Comptant", "journal_id": cls.company_data["default_journal_cash"].id,
            "receivable_account_id": cls.company_data["default_account_receivable"].id,
            "company_id": cls.company.id})
        cls.config = cls.env["pos.config"].create({
            "name": "Salle essai", "journal_id": cls.company_data["default_journal_sale"].id,
            "invoice_journal_id": cls.company_data["default_journal_sale"].id,
            "payment_method_ids": [Command.set(cls.cash.ids)]})
        cls.dish.available_in_pos = True
        cls.beer = cls.ingredient("Bière en bouteille", cls.unit, sale_ok=True, available_in_pos=True,
                                  list_price=7.0)
        cls.warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1)
        cls.stock = cls.warehouse.lot_stock_id

    def setUp(self):
        super().setUp()
        self.config.open_ui()
        self.session = self.config.current_session_id

    def on_stock(self, product):
        product.invalidate_recordset()
        return product.with_context(location=self.stock.id).qty_available

    def sell(self, lines):
        """lines: [(product, qty)], paid in cash."""
        total = sum(p.lst_price * q for p, q in lines)
        order = self.env["pos.order"].create({
            "company_id": self.company.id, "session_id": self.session.id,
            "lines": [Command.create({
                "name": p.name, "product_id": p.id, "price_unit": p.lst_price, "qty": q,
                "price_subtotal": p.lst_price * q, "price_subtotal_incl": p.lst_price * q,
                "tax_ids": [Command.clear()]}) for p, q in lines],
            "amount_total": total, "amount_tax": 0.0, "amount_paid": 0.0, "amount_return": 0.0,
        })
        self.pay(order)
        return order

    def pay(self, order):
        ctx = {"active_ids": order.ids, "active_id": order.id}
        self.env["pos.make.payment"].with_context(**ctx).create(
            {"amount": order.amount_total, "payment_method_id": self.cash.id}).with_context(**ctx).check()

    def refund(self, order):
        refund = self.env["pos.order"].browse(order.refund()["res_id"])
        self.pay(refund)
        return refund

    def test_p1_dish_sold_at_the_register(self):
        """A dish sold at the register leaves the stock as its ingredients."""
        order = self.sell([(self.dish, 2.0)])
        picking = order.picking_ids
        self.assertEqual(picking.state, "done")
        self.assertEqual(picking.move_ids.product_id, self.salmon | self.cream | self.salt_g)
        self.assertAlmostEqual(self.on_stock(self.salmon), -480.0)
        inside = set(self.env["stock.location"].search(
            [("id", "child_of", self.warehouse.view_location_id.id), ("usage", "=", "internal")]).ids)
        kind = picking.move_ids[:1]._bf_kitchen_variance_kind(inside)
        self.assertEqual(kind[0], "consumption")
        self.assertIn(kind[3], ("Register", "Caisse"), "the language of the test user")

    def test_p2_refund_is_a_loss(self):
        """Refunded, the dish's ingredients come back to stock, then leave as a loss, reason
        Customer return, once; a bottle sold as it is goes back to the stock."""
        order = self.sell([(self.dish, 2.0), (self.beer, 3.0)])
        self.assertAlmostEqual(self.on_stock(self.beer), -3.0)
        refund = self.refund(order)
        ret = refund.picking_ids
        self.assertEqual(ret.state, "done")
        self.assertEqual(ret.location_id.usage, "customer")
        scraps = self.env["stock.scrap"].search([("bf_kitchen_return_move_id", "in", ret.move_ids.ids)])
        self.assertEqual(scraps.product_id, self.salmon | self.cream | self.salt_g)
        self.assertTrue(all(s.state == "done" for s in scraps))
        reason = self.env.ref("bf_kitchen.scrap_reason_customer_return")
        self.assertTrue(all(s.scrap_reason_tag_ids == reason for s in scraps))
        self.assertAlmostEqual(self.on_stock(self.salmon), -480.0, msg="back in, then out as a loss")
        self.assertAlmostEqual(self.on_stock(self.beer), 0.0, msg="the bottle is back on the shelf")
        ret._bf_kitchen_scrap_register_returns()
        self.assertEqual(self.env["stock.scrap"].search_count(
            [("bf_kitchen_return_move_id", "in", ret.move_ids.ids)]), len(scraps), "once")

    def test_p3_refund_not_validated_waits(self):
        """A return the register could not validate makes no loss yet; validated later, it does."""
        order = self.sell([(self.dish, 1.0)])
        Picking = type(self.env["stock.picking"])
        real = Picking._action_done

        def refuse(pickings, *args, **kwargs):
            if pickings.filtered(lambda p: p.location_id.usage == "customer"):
                raise UserError("validation refusée (essai)")
            return real(pickings, *args, **kwargs)

        with patch.object(Picking, "_action_done", refuse):
            refund = self.refund(order)
        ret = refund.picking_ids
        self.assertNotEqual(ret.state, "done")
        self.assertFalse(self.env["stock.scrap"].search([("bf_kitchen_return_move_id", "in", ret.move_ids.ids)]))
        for move in ret.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        ret.button_validate()
        self.assertEqual(ret.state, "done")
        self.assertTrue(self.env["stock.scrap"].search([("bf_kitchen_return_move_id", "in", ret.move_ids.ids)]))

    def test_p4_recipe_put_on_sale(self):
        """The kitchen manager puts a dish on sale at the register from its recipe."""
        chef = self.mkuser("p4_chef", "bf_kitchen.group_kitchen_manager")
        self.dish.available_in_pos = False
        self.dish_bom.with_user(chef).bf_available_in_pos = True
        self.assertTrue(self.dish.available_in_pos)

    def test_p5_stock_at_session_closing(self):
        """When the register moves the stock at the session's closing, the session's return transfer
        makes the loss too."""
        self.session.action_pos_session_closing_control()
        self.company.point_of_sale_update_stock_quantities = "closing"
        self.config.open_ui()
        self.session = self.config.current_session_id
        order = self.sell([(self.dish, 1.0)])
        self.refund(order)
        self.assertFalse(order.picking_ids, "nothing moves before the closing")
        result = self.session.close_session_from_ui()
        self.assertTrue(result.get("successful"), result)
        ret = self.session.picking_ids.filtered(lambda p: p.location_id.usage == "customer")
        self.assertEqual(ret.state, "done")
        scraps = self.env["stock.scrap"].search([("bf_kitchen_return_move_id", "in", ret.move_ids.ids)])
        self.assertEqual(scraps.product_id, self.salmon | self.cream | self.salt_g)

    def test_r1_return_link_set_by_the_kitchen_only(self):
        """A loss is linked to a register return by the kitchen only."""
        order = self.sell([(self.dish, 1.0)])
        cook = self.mkuser("r1p_cook", "bf_kitchen.group_kitchen_cook")
        move = order.picking_ids.move_ids[:1]
        vals = {"product_id": self.salmon.id, "scrap_qty": 1.0, "location_id": self.stock.id}
        with self.assertRaises(AccessError):
            self.env["stock.scrap"].with_user(cook).create(dict(vals, bf_kitchen_return_move_id=move.id))
        scrap = self.env["stock.scrap"].with_user(cook).with_context(
            default_bf_kitchen_return_move_id=move.id).create(vals)
        self.assertFalse(scrap.bf_kitchen_return_move_id)
