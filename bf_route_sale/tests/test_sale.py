from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.bf_route.tests.common import RouteCase


@tagged("post_install", "-at_install")
class TestSale(RouteCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Product = cls.env["product.product"]
        cls.deposit = Product.create({"name": "Jug deposit", "type": "service", "list_price": 10.0,
                                      "taxes_id": [(6, 0, [])]})
        cls.empty = Product.create({"name": "Empty jug", "type": "consu", "is_storable": True,
                                    "list_price": 0.0, "taxes_id": [(6, 0, [])]})
        cls.jug = Product.create({"name": "Water 18.9 L", "type": "consu", "is_storable": True,
                                  "list_price": 8.0, "taxes_id": [(6, 0, [])],
                                  "bf_route_deposit_product_id": cls.deposit.id,
                                  "bf_route_empty_product_id": cls.empty.id})
        cls.other_product = Product.create({"name": "Not on routes", "list_price": 1.0})
        cls.truck.action_bf_route_create_location()
        cls.truck_location = cls.truck.bf_route_location_id
        Journal = cls.env["account.journal"]
        cash = Journal.search([("company_id", "=", cls.company.id), ("type", "=", "cash")], limit=1) \
            or Journal.create({"name": "Route cash", "type": "cash", "code": "RCSH"})
        bank = Journal.search([("company_id", "=", cls.company.id), ("type", "=", "bank")], limit=1)
        cls.company.write({"bf_route_cash_journal_id": cash.id, "bf_route_card_journal_id": bank.id,
                           "bf_route_cheque_journal_id": bank.id})
        cls.route.stop_ids.sorted("sequence")[0].write(
            {"line_ids": [(0, 0, {"product_id": cls.jug.id, "quantity": 3})]})

    def setUp(self):
        super().setUp()
        self.day = self.make_day()
        self.stop_a = self.day.stop_ids.filtered(lambda s: s.partner_id == self.partner_a())

    def partner_a(self):
        return self.route.stop_ids.sorted("sequence")[0].partner_id

    def sell(self, delivered=3, empties=2, mode="cash", amount=34.0, reference="", key="k1",
             stop=None, start=True):
        if start and self.day.state == "planned":
            self.start(self.day)
        stop = stop or self.stop_a
        return stop.with_user(self.worker).app_mark("done", app_key=key, sale={
            "lines": [{"product_id": self.jug.id, "delivered": delivered, "empties": empties}],
            "payment": {"mode": mode, "amount": amount, "reference": reference}})

    def quantity(self, product, location):
        return sum(self.env["stock.quant"].search([("product_id", "=", product.id),
                                                   ("location_id", "=", location.id)]).mapped("quantity"))

    def test_usual_products_copied_to_the_day(self):
        self.assertEqual(self.stop_a.line_ids.product_id, self.jug)
        self.assertEqual(self.stop_a.line_ids.quantity_planned, 3)

    def test_phone_gets_prices_with_taxes(self):
        self.start(self.day)
        data = self.env["bf.route.day"].with_user(self.worker).browse(self.day.id)._app_data()
        sale = next(s for s in data["stops"] if s["id"] == self.stop_a.id)["sale"]
        self.assertEqual(sale["lines"][0]["unit_total"], 8.0)
        self.assertEqual(sale["lines"][0]["deposit_unit_total"], 10.0)

    def test_sale_paid_in_cash(self):
        self.sell()
        invoice = self.stop_a.invoice_id
        self.assertEqual(invoice.state, "posted")
        self.assertEqual(invoice.move_type, "out_invoice")
        self.assertAlmostEqual(invoice.amount_total, 34.0)  # 3 x 8 + 3 x 10 - 2 x 10
        self.assertIn(invoice.payment_state, ("paid", "in_payment"))
        self.assertEqual(self.stop_a.payment_mode, "cash")
        self.assertEqual(self.quantity(self.jug, self.truck_location), -3)
        self.assertEqual(self.quantity(self.empty, self.truck_location), 2)
        self.assertEqual(self.partner_a().bf_route_container_count, 1)
        self.assertAlmostEqual(self.day.cash_total, 34.0)

    def test_resend_makes_one_invoice(self):
        self.sell(key="same")
        self.sell(key="same", amount=99)
        invoices = self.env["account.move"].search([("invoice_origin", "=", self.day.name)])
        self.assertEqual(len(invoices), 1)
        self.assertEqual(self.stop_a.payment_amount, 34.0)

    def test_on_account_leaves_invoice_open(self):
        self.sell(mode="account", amount=50)
        self.assertEqual(self.stop_a.invoice_id.payment_state, "not_paid")
        self.assertFalse(self.stop_a.payment_id)
        self.assertEqual(self.stop_a.payment_amount, 0)

    def test_card_keeps_authorization_number(self):
        self.sell(mode="card", reference="AUTH 4711")
        self.assertEqual(self.stop_a.payment_reference, "AUTH 4711")
        self.assertIn("AUTH 4711", self.stop_a.payment_id.memo)

    def test_payment_is_recorded_exactly_as_received(self):
        self.sell(amount=34.03)
        self.assertEqual(self.stop_a.payment_id.amount, 34.03)
        self.assertAlmostEqual(self.day.cash_total, 34.03)
        self.assertIn("34.03", self.day.message_ids[0].body)

    def test_short_payment_is_kept_and_flagged(self):
        self.sell(amount=30.0)
        self.assertEqual(self.stop_a.payment_id.amount, 30.0)
        self.assertNotEqual(self.stop_a.invoice_id.payment_state, "paid")
        self.assertIn("30.0", self.day.message_ids[0].body)

    def test_only_empties_back_is_a_draft_credit_note_to_check(self):
        self.sell(delivered=0, empties=2, amount=0, mode="account")
        refund = self.stop_a.invoice_id
        self.assertEqual(refund.move_type, "out_refund")
        self.assertEqual(refund.state, "draft", "a credit note waits for the office")
        self.assertAlmostEqual(refund.amount_total, 20.0)
        self.assertEqual(self.partner_a().bf_route_container_count, -2)
        self.assertTrue(self.day.activity_ids.filtered(lambda a: "Sale to check" in a.summary))

    def test_money_with_a_credit_note_is_recorded_and_flagged(self):
        self.sell(delivered=0, empties=2, amount=5)
        self.assertEqual(self.stop_a.state, "done")
        self.assertEqual(self.stop_a.invoice_id.move_type, "out_refund")
        self.assertEqual(self.stop_a.payment_id.amount, 5.0, "the cash received is in the books")
        self.assertAlmostEqual(self.day.cash_total, 5.0)

    def test_negative_total_with_taxes_on_the_deposit_only(self):
        # Above 6.67 %, 4 full and 7 empties turn negative once taxed (TVQ: 9.975 %).
        tax = self.env["account.tax"].search([("company_id", "=", self.company.id),
                                              ("type_tax_use", "=", "sale"), ("amount", ">", 6.67),
                                              ("amount_type", "=", "percent")], limit=1)
        self.assertTrue(tax, "a sales tax exists in the test company")
        self.deposit.taxes_id = [(6, 0, tax.ids)]
        # Untaxed: 4 x 8 + 4 x 10 - 7 x 10 = +2; with the tax on the deposit, negative.
        self.sell(delivered=4, empties=7, mode="account", amount=0)
        self.assertEqual(self.stop_a.state, "done")
        self.assertFalse(self.stop_a.sale_error)
        self.assertEqual(self.stop_a.invoice_id.move_type, "out_refund")

    def test_documents_that_fail_never_lose_the_sale(self):
        with patch.object(type(self.env["bf.route.day.stop"]), "_bf_route_documents",
                          side_effect=UserError("No income account")):
            self.sell()
        self.assertEqual(self.stop_a.state, "done")
        self.assertEqual(self.stop_a.line_ids.quantity_delivered, 3)
        self.assertEqual(self.stop_a.payment_amount, 34.0)
        self.assertIn("No income account", self.stop_a.sale_error)
        self.assertFalse(self.stop_a.invoice_id)
        self.assertTrue(self.day.activity_ids.filtered(lambda a: "Sale to check" in a.summary))
        self.stop_a.with_user(self.manager).action_bf_route_retry_sale()
        self.assertEqual(self.stop_a.invoice_id.state, "posted")
        self.assertFalse(self.stop_a.sale_error)

    def test_lot_tracked_product_waits_for_the_office(self):
        self.jug.tracking = "lot"
        self.sell()
        picking = self.stop_a.picking_ids.filtered(lambda p: p.picking_type_code == "outgoing")
        self.assertNotEqual(picking.state, "done")
        self.assertEqual(self.stop_a.invoice_id.state, "posted")
        self.assertIn("lot or serial", self.day.message_ids[0].body)

    def test_free_delivery_with_money_is_a_payment_on_account(self):
        self.jug.list_price = 0.0
        self.deposit.list_price = 0.0
        self.sell(amount=12.0)
        self.assertFalse(self.stop_a.invoice_id)
        self.assertEqual(self.stop_a.payment_id.amount, 12.0)
        self.assertNotEqual(self.stop_a.payment_id.state, "draft")
        self.assertIn("without an invoice", self.day.message_ids[0].body)

    def test_invoice_in_the_currency_of_the_price_list(self):
        usd = self.env.ref("base.USD")
        usd.active = True
        pricelist = self.env["product.pricelist"].create({"name": "USD list", "currency_id": usd.id})
        self.partner_a().sudo().property_product_pricelist = pricelist
        self.sell(mode="account", amount=0)
        self.assertEqual(self.stop_a.invoice_id.currency_id, usd)

    def test_second_sale_on_an_invoiced_stop_is_flagged_not_invoiced(self):
        self.sell(key="one")
        self.stop_a.with_user(self.manager).write({"state": "todo"})
        self.sell(key="two", delivered=5, amount=90)
        invoices = self.env["account.move"].search([("invoice_origin", "=", self.day.name)])
        self.assertEqual(len(invoices), 1)
        self.assertIn("already sold", self.day.message_ids[0].body)

    def test_a_marked_stop_cannot_be_sold_twice(self):
        self.sell(delivered=0, empties=0, amount=100, key="one")
        with self.assertRaises(UserError):
            self.sell(delivered=0, empties=0, amount=100, key="two")
        payments = self.env["account.payment"].search([("memo", "like", self.day.name)])
        self.assertEqual(len(payments), 1)

    def test_the_phone_cannot_skip_the_invoice_through_its_context(self):
        if self.day.state == "planned":
            self.start(self.day)
        self.stop_a.with_user(self.worker).with_context(skip_invoice_sync=True).app_mark(
            "done", app_key="ctx", sale={
                "lines": [{"product_id": self.jug.id, "delivered": 3, "empties": 2}],
                "payment": {"mode": "account", "amount": 0}})
        self.assertEqual(self.stop_a.invoice_id.state, "posted")
        self.assertAlmostEqual(self.stop_a.invoice_id.amount_total, 34.0)

    def test_a_stop_paid_without_invoice_is_not_sold_twice(self):
        self.jug.list_price = 0.0
        self.deposit.list_price = 0.0
        self.sell(amount=12.0, key="one")
        self.stop_a.with_user(self.manager).write({"state": "todo"})
        self.sell(amount=12.0, key="two")
        payments = self.env["account.payment"].search([("memo", "like", self.day.name)])
        self.assertEqual(len(payments), 1)
        self.assertIn("already sold", self.day.message_ids[0].body)

    def test_a_sale_waiting_for_the_office_is_not_overwritten(self):
        with patch.object(type(self.env["bf.route.day.stop"]), "_bf_route_documents",
                          side_effect=UserError("No income account")):
            self.sell(mode="cheque", amount=24.0, reference="CHQ 1", key="one")
        self.stop_a.with_user(self.manager).write({"state": "todo"})
        self.sell(mode="cash", amount=10.0, key="two")
        self.assertEqual(self.stop_a.payment_mode, "cheque")
        self.assertEqual(self.stop_a.payment_amount, 24.0)

    def test_an_unusual_product_waits_for_the_office(self):
        other = self.env["product.product"].create({"name": "Ice bag", "list_price": 3.0,
                                                    "taxes_id": [(6, 0, [])]})
        self.start(self.day)
        self.stop_a.with_user(self.worker).app_mark("done", app_key="u", sale={
            "lines": [{"product_id": self.jug.id, "delivered": 0, "empties": 0},
                      {"product_id": other.id, "delivered": 2}],
            "payment": {"mode": "account", "amount": 0}})
        self.assertFalse(self.stop_a.invoice_id, "never invoiced on the phone's word alone")
        self.assertIn("Ice bag", self.stop_a.sale_error)
        self.stop_a.with_user(self.manager).action_bf_route_retry_sale()
        self.assertAlmostEqual(self.stop_a.invoice_id.amount_total, 6.0)

    def test_archived_or_unsaleable_products_wait_for_the_office(self):
        hosting = self.env["product.product"].create({"name": "Hosting", "type": "service",
                                                      "list_price": 1000.0, "sale_ok": False})
        old = self.env["product.product"].create({"name": "Old water", "list_price": 1.0,
                                                  "bf_route_sold": True})
        old.active = False
        self.start(self.day)
        self.stop_a.with_user(self.worker).app_mark("done", app_key="h", sale={
            "lines": [{"product_id": hosting.id, "delivered": 10}, {"product_id": old.id, "delivered": 1}],
            "payment": {"mode": "account", "amount": 0}})
        self.assertFalse(self.stop_a.invoice_id)
        self.assertTrue(self.stop_a.sale_error)

    def test_a_done_with_nothing_sold_does_not_block_the_real_sale(self):
        self.sell(delivered=0, empties=0, mode="account", amount=0, key="one")
        self.stop_a.with_user(self.manager).write({"state": "todo"})
        self.sell(key="two")
        self.assertEqual(self.stop_a.invoice_id.state, "posted")
        self.assertAlmostEqual(self.day.cash_total, 34.0)

    def test_refusal_details_only_name_the_stop_s_products(self):
        secret = self.env["product.product"].create({"name": "Secret thing"})
        self.start(self.day)
        self.day.with_user(self.worker).app_report_refusal("r1", stop_id=self.stop_a.id, reason="x", sale={
            "lines": [{"product_id": secret.id, "delivered": 1}, {"product_id": self.jug.id, "delivered": 2}]})
        body = self.day.message_ids[0].body
        self.assertNotIn("Secret thing", body)
        self.assertIn("Water 18.9 L", body)

    def test_the_phone_estimate_is_the_invoice_to_the_cent(self):
        tax = self.env["account.tax"].search([("company_id", "=", self.company.id),
                                              ("type_tax_use", "=", "sale"), ("amount", ">", 6.67),
                                              ("amount_type", "=", "percent")], limit=1)
        self.jug.write({"taxes_id": [(6, 0, tax.ids)], "list_price": 8.99})
        self.deposit.taxes_id = [(6, 0, tax.ids)]
        rates = self.stop_a._bf_route_tax_rates(self.jug)
        if rates is None:
            self.skipTest("the company does not round per line")

        def cents(value):  # the phone's roundCents
            sign = -1 if value < 0 else 1
            return sign * int(abs(value) * 100 + 0.5 + 1e-7) / 100

        def line(qty, price):  # the phone's lineTotal
            base = cents(qty * price)
            return base + sum(cents(base * rate / 100) for rate in rates)

        for qty in range(1, 31):
            empties = qty // 3
            invoice = self.env["account.move"].create({
                "move_type": "out_invoice", "partner_id": self.partner_a().id,
                "invoice_line_ids": [(0, 0, {"product_id": self.jug.id, "quantity": qty, "price_unit": 8.99}),
                                     (0, 0, {"product_id": self.deposit.id, "quantity": qty, "price_unit": 10.0}),
                                     (0, 0, {"product_id": self.deposit.id, "quantity": -empties, "price_unit": 10.0})]})
            estimate = cents(line(qty, 8.99) + line(qty, 10.0) + line(-empties, 10.0))
            self.assertAlmostEqual(estimate, invoice.amount_total, places=2,
                                   msg="%s full, %s empties" % (qty, empties))

    def test_legal_cash_rounding_is_not_reported(self):
        self.jug.list_price = 8.01  # 3 x 8.01 + 10 = 34.03, rounded to 34.05 in cash
        self.sell(amount=34.05)
        self.assertEqual(self.stop_a.payment_id.amount, 34.05)
        self.assertFalse(self.day.activity_ids.filtered(lambda a: "Sale to check" in a.summary))
        self.assertAlmostEqual(self.day.cash_total, 34.05)

    def test_a_typo_on_a_round_total_is_reported(self):
        self.sell(amount=33.99)  # 34.00 is already round: 1 cent short is not rounding
        self.assertTrue(self.day.activity_ids.filtered(lambda a: "Sale to check" in a.summary))

    def test_cash_rounding_is_written_off_when_the_payment_makes_an_entry(self):
        journal = self.company._bf_route_journal("cash")
        if not (journal.profit_account_id and journal.loss_account_id):
            self.skipTest("cash journal without difference accounts")
        outstanding = self.env["account.account"].search(
            [("company_ids", "in", self.company.id), ("account_type", "=", "asset_current"),
             ("reconcile", "=", True)], limit=1) or self.env["account.account"].create(
            {"name": "Outstanding receipts", "code": "101999", "account_type": "asset_current",
             "reconcile": True})
        journal.inbound_payment_method_line_ids[:1].payment_account_id = outstanding
        self.jug.list_price = 8.01
        self.sell(amount=34.05)
        invoice = self.stop_a.invoice_id
        self.assertTrue(self.stop_a.payment_id.move_id, "the payment made an entry")
        self.assertTrue(invoice.currency_id.is_zero(invoice.amount_residual),
                        "the 2 cents were written off: %s left" % invoice.amount_residual)

    def test_goods_for_nothing_are_reported(self):
        self.jug.list_price = 0.0
        self.deposit.list_price = 0.0
        self.sell(mode="account", amount=0)
        self.assertFalse(self.stop_a.invoice_id)
        self.assertIn("invoice of 0", self.day.message_ids[0].body)

    def test_tracked_products_wait_alone(self):
        ice = self.env["product.product"].create({"name": "Filter", "type": "consu", "is_storable": True,
                                                  "tracking": "lot", "list_price": 5.0,
                                                  "taxes_id": [(6, 0, [])], "bf_route_sold": True})
        self.start(self.day)
        self.stop_a.with_user(self.worker).app_mark("done", app_key="t", sale={
            "lines": [{"product_id": self.jug.id, "delivered": 3, "empties": 0},
                      {"product_id": ice.id, "delivered": 1}],
            "payment": {"mode": "account", "amount": 0}})
        outgoing = self.stop_a.picking_ids.filtered(lambda p: p.picking_type_code == "outgoing")
        self.assertEqual(sorted(outgoing.mapped("state")), ["assigned", "done"]
                         if "assigned" in outgoing.mapped("state") else sorted(outgoing.mapped("state")))
        done = outgoing.filtered(lambda p: p.state == "done")
        self.assertEqual(done.move_ids.product_id, self.jug, "the water leaves at once")

    def test_a_truck_is_unloaded_once(self):
        self.sell(delivered=5, empties=0, amount=0, mode="account")
        self.day.with_user(self.worker).app_finish()
        self.day.with_user(self.manager).action_unload_truck()
        with self.assertRaises(UserError):
            self.day.with_user(self.manager).action_unload_truck()

    def test_a_rights_error_is_never_a_sale_to_finish(self):
        self.start(self.day)
        with patch.object(type(self.env["bf.route.day.stop"]), "_bf_route_documents",
                          side_effect=AccessError("no right")):
            with self.assertRaises(AccessError):
                self.sell()

    def test_refused_sale_details_reach_the_office(self):
        self.start(self.day)
        self.day.with_user(self.worker).app_report_refusal("r0", stop_id=self.stop_a.id, reason="Already marked", state="done", sale={
            "lines": [{"product_id": self.jug.id, "delivered": 7, "empties": 1}],
            "payment": {"mode": "cheque", "amount": 98.5, "reference": "CHQ 4471"}})
        body = self.day.message_ids[0].body
        self.assertIn("Water 18.9 L", body)
        self.assertIn("CHQ 4471", body)

    def test_phone_estimate_keeps_the_decimals(self):
        tax = self.env["account.tax"].search([("company_id", "=", self.company.id),
                                              ("type_tax_use", "=", "sale"), ("amount", ">", 6.67),
                                              ("amount_type", "=", "percent")], limit=1)
        self.jug.write({"taxes_id": [(6, 0, tax.ids)], "list_price": 8.99})
        unit = self.stop_a._bf_route_unit_total(self.jug, 8.99)
        self.assertNotEqual(round(unit, 2), unit)

    def test_a_product_sold_once_does_not_become_usual(self):
        hosting = self.env["product.product"].create({"name": "Hosting", "list_price": 1000.0,
                                                      "taxes_id": [(6, 0, [])]})
        days = self.route._generate_days(today=self.day.date)
        nxt = days.filtered(lambda d: d.date > self.day.date)[:1]
        self.start(self.day)
        self.stop_a.with_user(self.worker).app_mark("done", app_key="v1", sale={
            "lines": [{"product_id": hosting.id, "delivered": 1}], "payment": {"mode": "account"}})
        self.assertTrue(self.stop_a.sale_error)
        self.stop_a.with_user(self.manager).write({"state": "todo"})
        nxt.stop_ids.filtered(lambda s: s.partner_id == self.partner_a()).unlink()
        self.stop_a.with_user(self.worker).app_mark("postponed", app_key="v2")
        moved = nxt.stop_ids.filtered(lambda s: s.partner_id == self.partner_a())
        self.assertNotIn(hosting, moved.line_ids.product_id, "a phone-added line is not carried")
        self.start(nxt)
        moved.with_user(self.worker).app_mark("done", app_key="v3", sale={
            "lines": [{"product_id": hosting.id, "delivered": 10}], "payment": {"mode": "account"}})
        self.assertFalse(moved.invoice_id)
        self.assertTrue(moved.sale_error)

    def test_a_postponed_order_is_added_to_the_next_day(self):
        days = self.route._generate_days(today=self.day.date)
        nxt = days.filtered(lambda d: d.date > self.day.date)[:1]
        self.stop_a.line_ids.with_user(self.manager).write({"quantity_planned": 13})
        self.start(self.day)
        self.stop_a.with_user(self.worker).app_mark("postponed", app_key="p1")
        target = nxt.stop_ids.filtered(lambda s: s.partner_id == self.partner_a())
        self.assertEqual(len(target), 1)
        self.assertEqual(target.line_ids.quantity_planned, 3 + 13)

    def test_group_tax_estimate_is_exact(self):
        group = self.env["account.tax"].search([("company_id", "=", self.company.id),
                                                ("type_tax_use", "=", "sale"), ("amount_type", "=", "group")],
                                               limit=1)
        if not group:
            self.skipTest("no group tax in this company")
        self.jug.write({"taxes_id": [(6, 0, group.ids)], "list_price": 8.99})
        rates = self.stop_a._bf_route_tax_rates(self.jug)
        self.assertEqual(len(rates), len(group.children_tax_ids))

        def cents(value):
            sign = -1 if value < 0 else 1
            return sign * int(abs(value) * 100 + 0.5 + 1e-7) / 100

        for qty in (7, 10, 11, 12):
            invoice = self.env["account.move"].create({
                "move_type": "out_invoice", "partner_id": self.partner_a().id,
                "invoice_line_ids": [(0, 0, {"product_id": self.jug.id, "quantity": qty, "price_unit": 8.99})]})
            base = cents(qty * 8.99)
            estimate = base + sum(cents(base * rate / 100) for rate in rates)
            self.assertAlmostEqual(cents(estimate), invoice.amount_total, places=2, msg=str(qty))

    def test_price_sent_to_the_phone_is_the_invoice_s(self):
        pricelist = self.env["product.pricelist"].create({"name": "-25 %", "item_ids": [(0, 0, {
            "compute_price": "percentage", "percent_price": 25, "applied_on": "3_global"})]})
        self.partner_a().sudo().property_product_pricelist = pricelist
        self.jug.list_price = 8.99
        self.assertEqual(self.stop_a._bf_route_price(self.jug, 1), 6.74)

    def test_a_truck_is_unloaded_again_once_the_first_is_done(self):
        self.sell(delivered=5, empties=0, amount=0, mode="account")
        self.day.with_user(self.worker).app_finish()
        self.day.with_user(self.manager).action_unload_truck()
        for picking in self.day.unload_picking_ids:
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
                move.picked = True
            picking.button_validate()
        self.env["stock.quant"]._update_available_quantity(self.jug, self.truck_location, 2)
        self.day.with_user(self.manager).action_unload_truck()
        self.assertEqual(len(self.day.unload_picking_ids), 2)

    def test_a_loaded_day_keeps_a_customer_taken_off_the_route(self):
        warehouse = self.env["stock.warehouse"].search([("company_id", "=", self.company.id)], limit=1)
        self.env["stock.quant"]._update_available_quantity(self.jug, warehouse.lot_stock_id, 20)
        days = self.route._generate_days(today=self.day.date)
        nxt = days.filtered(lambda d: d.date > self.day.date)[:1]
        nxt.with_user(self.manager).action_load_truck()
        self.assertFalse(nxt.edited)
        self.route.stop_ids.sorted("sequence")[0].with_user(self.manager).unlink()
        self.assertIn(self.partner_a(), nxt.stop_ids.partner_id, "the truck was loaded for them")
        self.assertTrue(nxt.activity_ids.filtered(lambda a: "taken off the route" in a.summary))

    def test_the_phone_prices_what_is_delivered(self):
        pricelist = self.env["product.pricelist"].create({"name": "Tiers", "item_ids": [(0, 0, {
            "applied_on": "0_product_variant", "product_id": self.jug.id, "min_quantity": 10,
            "compute_price": "fixed", "fixed_price": 7.5})]})
        self.partner_a().sudo().property_product_pricelist = pricelist
        self.start(self.day)
        data = self.env["bf.route.day"].with_user(self.worker).browse(self.day.id)._app_data()
        line = next(s for s in data["stops"] if s["id"] == self.stop_a.id)["sale"]["lines"][0]
        self.assertEqual([b[:2] for b in line["price_breaks"]], [[1.0, 8.0], [10.0, 7.5]])
        # The phone's priceAt for 12 delivered, then the invoice.
        price = [b for b in line["price_breaks"] if 12 >= b[0]][-1][1]
        estimate = 12 * price + 12 * 10 - 2 * 10
        self.sell(delivered=12, empties=2, amount=estimate)
        self.assertAlmostEqual(self.stop_a.invoice_id.amount_total, estimate)
        self.assertFalse(self.day.activity_ids.filtered(lambda a: "Sale to check" in a.summary))

    def test_a_postponed_stop_into_a_loaded_day_is_flagged(self):
        warehouse = self.env["stock.warehouse"].search([("company_id", "=", self.company.id)], limit=1)
        self.env["stock.quant"]._update_available_quantity(self.jug, warehouse.lot_stock_id, 20)
        days = self.route._generate_days(today=self.day.date)
        nxt = days.filtered(lambda d: d.date > self.day.date)[:1]
        nxt.with_user(self.manager).action_load_truck()
        self.start(self.day)
        self.stop_a.with_user(self.worker).app_mark("postponed", app_key="pl")
        self.assertTrue(nxt.activity_ids.filtered(lambda a: "prepared day" in a.summary))

    def test_instructions_are_not_repeated_by_merges(self):
        days = self.route._generate_days(today=self.day.date)
        nxt = days.filtered(lambda d: d.date > self.day.date)[:1]
        self.start(self.day)
        self.stop_a.with_user(self.worker).app_mark("postponed", app_key="i1")
        target = nxt.stop_ids.filtered(lambda s: s.partner_id == self.partner_a())
        self.assertEqual(target.instructions, "Back door")

    def test_an_old_day_is_not_unloaded_after_the_truck_left_again(self):
        warehouse = self.env["stock.warehouse"].search([("company_id", "=", self.company.id)], limit=1)
        self.env["stock.quant"]._update_available_quantity(self.jug, warehouse.lot_stock_id, 20)
        self.sell(delivered=5, empties=0, amount=0, mode="account")
        self.day.with_user(self.worker).app_finish()
        self.day.with_user(self.manager).action_unload_truck()
        for picking in self.day.unload_picking_ids:
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
                move.picked = True
            picking.button_validate()
        days = self.route._generate_days(today=self.day.date)
        later = days.filtered(lambda d: d.date > self.day.date)[:1]
        later.with_user(self.manager).action_load_truck()
        load = later.load_picking_id
        for move in load.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        load.button_validate()
        self.assertGreater(self.quantity(self.jug, self.truck_location), 0, "the truck left loaded")
        with self.assertRaisesRegex(UserError, "loaded again"):
            self.day.with_user(self.manager).action_unload_truck()

    def test_a_first_unload_is_refused_once_the_truck_left_again(self):
        warehouse = self.env["stock.warehouse"].search([("company_id", "=", self.company.id)], limit=1)
        self.env["stock.quant"]._update_available_quantity(self.jug, warehouse.lot_stock_id, 20)
        self.sell(delivered=1, empties=0, amount=0, mode="account")
        self.day.with_user(self.worker).app_finish()
        days = self.route._generate_days(today=self.day.date)
        later = days.filtered(lambda d: d.date > self.day.date)[:1]
        later.with_user(self.manager).action_load_truck()
        load = later.load_picking_id
        for move in load.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        load.button_validate()
        with self.assertRaisesRegex(UserError, "loaded again"):
            self.day.with_user(self.manager).action_unload_truck()

    def test_a_cancelled_later_day_does_not_block_unloading(self):
        self.sell(delivered=5, empties=0, amount=0, mode="account")
        self.day.with_user(self.worker).app_finish()
        days = self.route._generate_days(today=self.day.date)
        later = days.filtered(lambda d: d.date > self.day.date)[:1]
        later.with_user(self.manager).action_cancel()
        self.day.with_user(self.manager).action_unload_truck()
        self.assertTrue(self.day.unload_picking_ids)

    def test_a_duplicated_day_starts_fresh(self):
        hosting = self.env["product.product"].create({"name": "Hosting", "list_price": 1.0})
        self.start(self.day)
        self.stop_a.with_user(self.worker).app_mark("done", app_key="d1", sale={
            "lines": [{"product_id": self.jug.id, "delivered": 3}, {"product_id": hosting.id, "delivered": 1}],
            "payment": {"mode": "account"}})
        copy = self.day.with_user(self.manager).copy({"date": self.day.date.replace(year=2027)})
        stop = copy.stop_ids.filtered(lambda s: s.partner_id == self.partner_a())
        self.assertEqual(self.stop_a.state, "done", "the source stop was marked")
        self.assertEqual(stop.state, "todo")
        copied = self.stop_a.copy_data()[0]
        self.assertNotIn("state", copied, "a copy never carries the mark")
        self.assertNotIn("done_at", copied)
        self.assertFalse(sum(stop.line_ids.mapped("quantity_delivered")))
        self.assertTrue(stop.line_ids.filtered(lambda l: l.product_id == hosting).from_phone)

    def test_worker_cannot_read_container_counts(self):
        with self.assertRaises(AccessError):
            self.partner_a().with_user(self.worker).read(["bf_route_container_count"])

    def test_only_odoo_and_the_kit_define_create_payments(self):
        # The payment step of Odoo is called directly (see bf_route_sale.py): any NEW override
        # of it would be skipped. This test fails the day one appears, so it gets a look.
        owners = {cls.__module__.split(".")[2] for cls in type(self.env["account.payment.register"]).mro()
                  if "_create_payments" in vars(cls) and cls.__module__.startswith("odoo.addons.")}
        self.assertLessEqual(owners, {"account", "base_accounting_kit"})

    def test_a_product_that_does_not_exist_is_refused(self):
        self.start(self.day)
        with self.assertRaises(UserError):
            self.stop_a.with_user(self.worker).app_mark("done", sale={
                "lines": [{"product_id": 999999999, "delivered": 1}],
                "payment": {"mode": "cash", "amount": 1}})
        self.assertEqual(self.stop_a.state, "todo")
        self.assertFalse(self.stop_a.invoice_id)

    def test_absurd_quantities_are_refused(self):
        self.start(self.day)
        for bad in (-1, "many", 10 ** 9):
            with self.assertRaises(UserError):
                self.stop_a.with_user(self.worker).app_mark("done", sale={
                    "lines": [{"product_id": self.jug.id, "delivered": bad}]})

    def test_other_worker_cannot_sell(self):
        self.start(self.day)
        with self.assertRaises(AccessError):
            self.stop_a.with_user(self.other).app_mark("done", sale={"lines": []})

    def test_absent_sells_nothing(self):
        self.start(self.day)
        self.stop_a.with_user(self.worker).app_mark("absent", sale={
            "lines": [{"product_id": self.jug.id, "delivered": 3}], "payment": {"mode": "cash", "amount": 24}})
        self.assertFalse(self.stop_a.invoice_id)

    def test_load_and_unload_the_truck(self):
        warehouse = self.env["stock.warehouse"].search([("company_id", "=", self.company.id)], limit=1)
        self.env["stock.quant"]._update_available_quantity(self.jug, warehouse.lot_stock_id, 20)
        self.day.with_user(self.manager).action_load_truck()
        load = self.day.load_picking_id
        self.assertEqual(load.location_dest_id, self.truck_location)
        self.assertEqual(load.move_ids.product_uom_qty, 3)
        load.move_ids.quantity = 5
        load.move_ids.picked = True
        load.button_validate()
        self.sell()
        self.day.with_user(self.worker).app_finish()
        self.day.with_user(self.manager).action_unload_truck()
        unload = self.day.unload_picking_ids
        jugs_back = unload.move_ids.filtered(lambda m: m.product_id == self.jug)
        self.assertEqual(jugs_back.product_uom_qty, 2)
        self.assertEqual(unload.move_ids.filtered(lambda m: m.product_id == self.empty).product_uom_qty, 2)

    def test_unloading_corrects_a_truck_below_zero(self):
        self.sell(delivered=5, empties=0, amount=0, mode="account")
        self.assertEqual(self.quantity(self.jug, self.truck_location), -5)
        self.day.with_user(self.worker).app_finish()
        self.day.with_user(self.manager).action_unload_truck()
        correction = self.day.unload_picking_ids.filtered(lambda p: p.location_dest_id == self.truck_location)
        self.assertEqual(correction.move_ids.product_uom_qty, 5)

    def test_worker_cannot_load_the_truck(self):
        with self.assertRaises(AccessError):
            self.day.with_user(self.worker).action_load_truck()

    def test_postponed_stop_keeps_its_products(self):
        days = self.route._generate_days(today=self.day.date)
        nxt = days.filtered(lambda d: d.date > self.day.date)[:1]
        nxt.stop_ids.filtered(lambda s: s.partner_id == self.partner_a()).unlink()
        self.start(self.day)
        self.stop_a.with_user(self.worker).app_mark("postponed")
        moved = nxt.stop_ids.filtered(lambda s: s.partner_id == self.partner_a())
        self.assertEqual(moved.line_ids.product_id, self.jug)
