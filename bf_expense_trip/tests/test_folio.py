# -*- coding: utf-8 -*-
import base64

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import TripCommon


@tagged("post_install", "-at_install")
class TestFolio(TripCommon):

    def _folio(self, amount=240.0, **vals):
        expense = self._expense(name="Hôtel Royal", amount=amount, product_id=self.hotel.id,
                                tax_ids=[Command.set(self.taxe.ids)], **vals)
        self.env["ir.attachment"].create({
            "name": "folio.pdf", "datas": base64.b64encode(b"%PDF-1.4 folio"),
            "res_model": "hr.expense", "res_id": expense.id})
        return expense

    def _wizard(self, expense, lines, user=None):
        Wizard = self.env["bf.expense.folio.wizard"]
        if user:
            Wizard = Wizard.with_user(user)
        wizard = Wizard.with_context(active_id=expense.id).create({"expense_id": expense.id})
        wizard.line_ids = [Command.clear()] + [Command.create(line) for line in lines]
        return wizard

    LINES = [
        {"name": "Chambre, 2 nuits", "amount": 200.0},
        {"name": "Taxe sur l'hébergement", "amount": 7.0},
        {"name": "Stationnement", "amount": 20.0},
        {"name": "Minibar", "amount": 13.0, "personal": True},
    ]

    def _lines(self):
        lines = [dict(line) for line in self.LINES]
        lines[0].update(product_id=self.hotel.id, tax_ids=[Command.set(self.taxe.ids)])
        lines[1].update(product_id=self.lodging_tax.id)
        lines[2].update(product_id=self.parking.id, tax_ids=[Command.set(self.taxe.ids)])
        return lines

    def test_split_with_personal_charges(self):
        trip = self._trip()
        expense = self._folio()
        self.assertEqual(expense.trip_id, trip)
        action = self._wizard(expense, self._lines()).action_split()
        made = self.env["hr.expense"].search(action["domain"], order="id")
        self.assertEqual(made[0], expense)
        self.assertEqual([(m.product_id, m.total_amount_currency, m.tax_ids) for m in made], [
            (self.hotel, 200.0, self.taxe),
            (self.lodging_tax, 7.0, self.env["account.tax"]),
            (self.parking, 20.0, self.taxe),
        ])
        self.assertEqual(made.mapped("name"), ["Hôtel Royal: Chambre, 2 nuits",
                                               "Hôtel Royal: Taxe sur l'hébergement",
                                               "Hôtel Royal: Stationnement"])
        self.assertEqual(expense.bf_folio_personal_amount, 13.0)
        self.assertEqual((made - expense).mapped("bf_folio_personal_amount"), [0.0, 0.0])
        self.assertEqual(made.trip_id, trip)
        for line in made:
            self.assertEqual(line.attachment_ids.mapped("name"), ["folio.pdf"])
        self.assertIn("Minibar", expense.message_ids[0].body)

    def test_the_lines_must_add_up_to_the_folio(self):
        expense = self._folio()
        lines = self._lines()[:3]
        with self.assertRaisesRegex(UserError, "every charge"):
            self._wizard(expense, lines).action_split()
        self.assertEqual(expense.total_amount_currency, 240.0)

    def test_all_personal_is_refused(self):
        expense = self._folio(amount=13.0)
        with self.assertRaisesRegex(UserError, "Nothing on this folio"):
            self._wizard(expense, [{"name": "Minibar", "amount": 13.0, "personal": True}]).action_split()

    def test_a_reimbursed_line_needs_a_category(self):
        expense = self._folio(amount=20.0)
        with self.assertRaisesRegex(UserError, "category"):
            self._wizard(expense, [{"name": "Stationnement", "amount": 20.0}]).action_split()

    def test_a_negative_line_is_refused(self):
        expense = self._folio(amount=200.0)
        lines = [{"name": "Chambre", "amount": 220.0, "product_id": self.hotel.id},
                 {"name": "Rabais", "amount": -20.0, "product_id": self.hotel.id}]
        with self.assertRaisesRegex(UserError, "negative"):
            self._wizard(expense, lines).action_split()

    def test_a_submitted_expense_is_not_split(self):
        expense = self._folio()
        sheet = self.env["hr.expense.sheet"].create({
            "name": "Hôtel", "employee_id": self.boss.id,
            "expense_line_ids": [Command.set(expense.ids)]})
        sheet.action_submit_sheet()
        with self.assertRaisesRegex(UserError, "not submitted"):
            self._wizard(expense, self._lines()).action_split()

    def test_an_expense_in_a_draft_report_stays_in_it(self):
        expense = self._folio()
        sheet = self.env["hr.expense.sheet"].create({
            "name": "Hôtel", "employee_id": self.boss.id,
            "expense_line_ids": [Command.set(expense.ids)]})
        self._wizard(expense, self._lines()).action_split()
        self.assertEqual(len(sheet.expense_line_ids), 3)
        self.assertEqual(sheet.total_amount, 227.0)

    def test_the_assistant_splits_the_boss_folio(self):
        expense = self._folio()
        action = self._wizard(expense, self._lines(), user=self.assistant_user).action_split()
        made = self.env["hr.expense"].search(action["domain"])
        self.assertEqual(len(made), 3)
        self.assertEqual(made.employee_id, self.boss)

    def test_a_stranger_cannot_split(self):
        expense = self._folio()
        with self.assertRaises(AccessError):
            self._wizard(expense, self._lines(), user=self.stranger_user).action_split()
        self.assertEqual(expense.total_amount_currency, 240.0)

    def test_a_folio_in_another_currency(self):
        expense = self._folio(currency_id=self.other_currency.id)
        rate = expense.currency_rate
        self.assertNotEqual(rate, 1.0)
        action = self._wizard(expense, self._lines()).action_split()
        made = self.env["hr.expense"].search(action["domain"], order="id")
        self.assertEqual(made.currency_id, self.other_currency)
        self.assertEqual(sorted(made.mapped("total_amount_currency")), [7.0, 20.0, 200.0])
        for line in made:
            self.assertAlmostEqual(
                line.total_amount, line.company_currency_id.round(line.total_amount_currency * rate),
                places=2)

    def test_an_assistant_without_an_email_address_splits(self):
        """⚠️ message_post refuses an author without an
        email address, and the whole split was undone with it."""
        self.assistant_user.partner_id.email = False
        expense = self._folio()
        action = self._wizard(expense, self._lines(), user=self.assistant_user).action_split()
        made = self.env["hr.expense"].search(action["domain"])
        self.assertEqual(len(made), 3)
        self.assertIn("Minibar", expense.message_ids[0].body)

    def test_an_added_line_takes_the_taxes_of_its_category(self):
        # As in the dialog: the line's company, read through a wizard not saved yet, is empty.
        line = self.env["bf.expense.folio.line"].new({"name": "Stationnement"})
        self.assertFalse(line.company_id)
        line.product_id = self.parking
        line._onchange_product_id()
        self.assertEqual(line.tax_ids._origin, self.taxe)
