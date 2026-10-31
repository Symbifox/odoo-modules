"""Dishes sold on sales orders and invoices take their ingredients out of the kitchen stock, once.

The fixtures are bf_kitchen's TestKitchen (a dish of 240 g of salmon, 30 g of a cream sauce and
3 g of salt). Odoo 18 runs only the test methods a class defines itself."""
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.bf_kitchen.tests.test_kitchen import TestKitchen


@tagged("post_install", "-at_install")
class TestKitchenSale(TestKitchen):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env["res.partner"].create({"name": "Banquet Essai"})
        cls.stock = cls.env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1).lot_stock_id

    def on_stock(self, product):
        product.invalidate_recordset()
        return product.with_context(location=self.stock.id).qty_available

    def invoice(self, qty=10.0, post=True, product=None):
        move = self.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": self.customer.id, "invoice_date": self.today,
            "invoice_line_ids": [Command.create({
                "product_id": (product or self.dish).id, "quantity": qty, "price_unit": 20.0,
                "tax_ids": [Command.clear()]})],
        })
        if post:
            move.action_post()
        return move

    def test_s1_invoice_without_order_takes_the_dishes_out(self):
        """A posted invoice with no sales order: one Kitchen consumption, the dish exploded, dated
        of the invoice, linked to it."""
        move = self.invoice(10.0)
        picking = move.bf_kitchen_consumption_ids
        self.assertEqual(len(picking), 1)
        self.assertEqual(picking.state, "done")
        self.assertEqual(picking.bf_kitchen_source, "invoice")
        self.assertEqual(picking.bf_kitchen_event_date, move.invoice_date)
        self.assertEqual(picking.partner_id, self.customer)
        self.assertAlmostEqual(self.on_stock(self.salmon), -2400.0)
        self.assertAlmostEqual(self.on_stock(self.salt_g), -30.0)
        self.assertEqual(move.bf_kitchen_consumption_count, 1)

    def test_s2_draft_and_cancel_put_back(self):
        """Reset to draft, the food goes back; posted again, it leaves again, once; cancelled, back."""
        move = self.invoice(2.0)
        move.button_draft()
        self.assertAlmostEqual(self.on_stock(self.salmon), 0.0)
        move.invoice_line_ids.quantity = 3.0
        move.action_post()
        self.assertAlmostEqual(self.on_stock(self.salmon), -720.0, msg="the corrected quantity, not 2 + 3")
        move.button_draft()
        move.button_cancel()
        self.assertAlmostEqual(self.on_stock(self.salmon), 0.0)
        self.assertEqual(move.state, "cancel")

    def test_s3_sales_order_left_to_its_delivery(self):
        """A dish on a sales order leaves with the delivery (sale_mrp explodes the kit); its invoice,
        on ordered or delivered quantities, takes nothing more out."""
        order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "order_line": [Command.create({"product_id": self.dish.id, "product_uom_qty": 4.0, "price_unit": 20.0,
                                           "tax_id": [Command.clear()]})],
        })
        order.action_confirm()
        delivery = order.picking_ids
        self.assertEqual(delivery.move_ids.product_id, self.salmon | self.cream | self.salt_g)
        for move in delivery.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        delivery.button_validate()
        self.assertAlmostEqual(self.on_stock(self.salmon), -960.0)
        invoice = order._create_invoices()
        invoice.action_post()
        self.assertFalse(invoice.bf_kitchen_consumption_ids)
        self.assertAlmostEqual(self.on_stock(self.salmon), -960.0, msg="not counted twice")

    def test_s4_never_blocks_the_posting(self):
        """A consumption that fails leaves the invoice posted, with the reason on it."""
        Picking = type(self.env["stock.picking"])
        with patch.object(Picking, "_bf_kitchen_consume", side_effect=UserError("panne d'essai")):
            move = self.invoice(1.0)
        self.assertEqual(move.state, "posted")
        self.assertFalse(move.bf_kitchen_consumption_ids)
        self.assertIn("panne d'essai", move.message_ids[:1].body)

    def test_s5_only_kitchen_lines(self):
        """An invoice with no kitchen item takes nothing; a credit note neither."""
        service = self.env["product.product"].create({"name": "Location de salle", "type": "service"})
        move = self.invoice(1.0, product=service)
        self.assertFalse(move.bf_kitchen_consumption_ids)
        refund = self.invoice(1.0, post=False)
        refund.move_type = "out_refund"
        refund.action_post()
        self.assertFalse(refund.bf_kitchen_consumption_ids)

    def test_s6_link_set_by_the_kitchen_only(self):
        """The invoice behind a consumption is the server's to write."""
        move = self.invoice(1.0)
        clerk = self.mkuser("s6_clerk", "stock.group_stock_manager")
        with self.assertRaises(AccessError):
            move.bf_kitchen_consumption_ids.with_user(clerk).write({"bf_kitchen_invoice_id": False})

    def test_s7_variance_columns(self):
        """In the variance report, an invoice and a delivery are consumption, each in its column."""
        move = self.invoice(1.0)
        moves = move.bf_kitchen_consumption_ids.move_ids
        inside = set(self.env["stock.location"].search([("id", "child_of", self.stock.warehouse_id.view_location_id.id),
                                                        ("usage", "=", "internal")]).ids)
        kind = moves[:1]._bf_kitchen_variance_kind(inside)
        self.assertEqual(kind[0], "consumption")
        self.assertEqual(kind[3], "Invoice without a sales order")

    def test_s8_accountant_without_email(self):
        """An accountant with no email address posts an invoice of dishes: the note never blocks it."""
        clerk = self.mkuser("s8_clerk", "account.group_account_invoice")
        clerk.partner_id.email = False
        move = self.invoice(2.0, post=False)
        move.with_user(clerk).action_post()
        self.assertEqual(move.state, "posted")
        self.assertTrue(move.bf_kitchen_consumption_ids)
        move.with_user(clerk).button_draft()
        self.assertAlmostEqual(self.on_stock(self.salmon), 0.0)

    def test_r1_credit_note_modify_mode(self):
        """Corrected by the credit note's "modify" mode: the food of the first invoice
        goes back, the new invoice takes out its own quantity, once. A plain credit note puts nothing
        back."""
        move = self.invoice(10.0)
        wizard = self.env["account.move.reversal"].with_context(
            active_model="account.move", active_ids=move.ids).create({"journal_id": move.journal_id.id})
        action = wizard.modify_moves()
        new = self.env["account.move"].browse(action["res_id"])
        self.assertAlmostEqual(self.on_stock(self.salmon), 0.0, msg="the first invoice's food is back")
        new.invoice_line_ids.quantity = 8.0
        new.action_post()
        self.assertAlmostEqual(self.on_stock(self.salmon), -8 * 240.0)
        gesture = self.invoice(1.0)
        wizard = self.env["account.move.reversal"].with_context(
            active_model="account.move", active_ids=gesture.ids).create({"journal_id": gesture.journal_id.id})
        wizard.refund_moves()
        self.assertAlmostEqual(self.on_stock(self.salmon), -9 * 240.0, msg="a plain credit note: nothing back")

    def test_r1_note_links_kept(self):
        """The note of a return shows a link, not escaped HTML."""
        move = self.invoice(1.0)
        move.button_draft()
        body = str(move.message_ids.filtered(lambda m: "WH/" in str(m.body) or "CONS" in str(m.body))[:1].body)
        self.assertIn("<a ", body)
        self.assertNotIn("&lt;a", body)

    def test_r1_invoice_from_the_kitchen_warehouse(self):
        """An invoice takes the dishes out of the company's kitchen warehouse."""
        # A second warehouse turns the multi-warehouse group on through the settings, which wake
        # modules of the test base that call the network: not the kitchen's business here.
        with patch.object(type(self.env["stock.warehouse"]), "_check_multiwarehouse_group", lambda wh: None):
            other = self.env["stock.warehouse"].create({"name": "Salle des banquets", "code": "BANQ"})
        self.company.bf_kitchen_warehouse_id = other
        move = self.invoice(1.0)
        self.assertEqual(move.bf_kitchen_consumption_ids.picking_type_id.warehouse_id, other)

    def test_r1_invoice_link_not_by_context(self):
        """A `default_*` of the context does not link a transfer to an invoice."""
        move = self.invoice(1.0)
        clerk = self.mkuser("r1s_clerk", "stock.group_stock_manager")
        wh = self.env["stock.warehouse"].search([("company_id", "=", self.company.id)], limit=1)
        picking = self.env["stock.picking"].with_user(clerk).with_context(
            default_bf_kitchen_invoice_id=move.id).create({"picking_type_id": wh.out_type_id.id})
        self.assertFalse(picking.bf_kitchen_invoice_id)

    def test_r2_modify_dated_later(self):
        """"Modify" with a reversal dated later (posted by Odoo's job): the first
        invoice's food goes back now all the same, and the new invoice takes out its own."""
        from datetime import timedelta
        move = self.invoice(4.0)
        wizard = self.env["account.move.reversal"].with_context(
            active_model="account.move", active_ids=move.ids).create(
            {"journal_id": move.journal_id.id, "date": self.today + timedelta(days=3)})
        action = wizard.modify_moves()
        self.assertAlmostEqual(self.on_stock(self.salmon), 0.0, msg="the first invoice's food is back")
        new = self.env["account.move"].browse(action["res_id"])
        new.action_post()
        self.assertAlmostEqual(self.on_stock(self.salmon), -4 * 240.0, msg="4 dishes, not 8")
