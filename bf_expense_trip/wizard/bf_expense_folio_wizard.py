# -*- coding: utf-8 -*-
"""Split a hotel folio: one expense per line, the personal charges set aside.

⚠️ The mechanics are Odoo's split (``hr.expense.split.wizard.action_split_expense``): the first
line is written on the expense, each other line is a copy of it with the receipt copied too.
Two differences: lines can be personal (they are not reimbursed, so the lines need not add up
to the folio's total, only lines + personal charges do), and the split also works on an
expense already in a draft report, which Odoo's button does not.
"""
from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import formatLang


class BfExpenseFolioWizard(models.TransientModel):
    _name = "bf.expense.folio.wizard"
    _description = "Split a hotel folio"

    expense_id = fields.Many2one("hr.expense", required=True, readonly=True)
    company_id = fields.Many2one(related="expense_id.company_id")
    currency_id = fields.Many2one(related="expense_id.currency_id")
    total_folio = fields.Monetary(related="expense_id.total_amount_currency", string="Folio total")
    line_ids = fields.One2many("bf.expense.folio.line", "wizard_id", string="Lines")
    total_reimbursed = fields.Monetary(compute="_compute_totals")
    total_personal = fields.Monetary(compute="_compute_totals", string="Personal charges")
    difference = fields.Monetary(compute="_compute_totals",
                                 help="What the lines leave out of the folio's total.")

    @api.depends("line_ids.amount", "line_ids.personal", "total_folio")
    def _compute_totals(self):
        for wizard in self:
            personal = sum(wizard.line_ids.filtered("personal").mapped("amount"))
            business = sum(wizard.line_ids.filtered(lambda l: not l.personal).mapped("amount"))
            wizard.total_personal = personal
            wizard.total_reimbursed = business
            wizard.difference = wizard.total_folio - personal - business

    @api.model
    def default_get(self, fields_list):
        result = super().default_get(fields_list)
        expense = self.env["hr.expense"].browse(
            result.get("expense_id") or self.env.context.get("active_id"))
        if expense and "line_ids" in fields_list:
            result["expense_id"] = expense.id
            result["line_ids"] = [Command.create({
                "name": _("Room"),
                "product_id": expense.product_id.id,
                "tax_ids": [Command.set(expense.tax_ids.ids)],
                "amount": expense.total_amount_currency,
            })]
        return result

    def _bf_check(self):
        self.ensure_one()
        expense = self.expense_id
        if expense.state not in ("draft", "reported") or not expense.is_editable:
            raise UserError(_("Only an expense that is not submitted yet can be split."))
        if expense.product_has_cost:
            raise UserError(_("An expense priced by its category (mileage) is not a folio."))
        if "bf_trip_allowance" in expense._fields and expense.bf_trip_allowance:
            # Its copies would not be allowances, and generating again would pay them twice.
            raise UserError(_("An allowance is not a folio."))
        if "tip_amount_currency" in expense._fields and expense.tip_amount_currency:
            raise UserError(_("Remove the tip of this expense before splitting the folio: a "
                              "hotel folio carries its charges as lines."))
        if any(line.amount < 0 for line in self.line_ids):
            raise UserError(_("A line of the folio cannot be negative: enter a refund as its "
                              "own expense."))
        business = self.line_ids.filtered(lambda l: not l.personal)
        if expense.payment_mode == "company_account" and business != self.line_ids:
            # ⚠️ The company's card paid the whole folio: removing the personal charges would
            # book less than the card was charged. They are owed by the employee instead.
            raise UserError(_(
                "The company paid this folio: every charge stays on its expenses. Recover the "
                "personal charges from the employee instead of setting them aside."))
        if not business:
            raise UserError(_("Nothing on this folio is reimbursed: delete the expense instead."))
        if business.filtered(lambda l: not l.product_id):
            raise UserError(_("Each reimbursed line needs an expense category."))
        for line in business:
            if line.product_id.with_company(expense.company_id).standard_price:
                raise UserError(_(
                    "The \"%s\" category is priced by its cost (mileage, allowance): choose a "
                    "category without a cost.", line.product_id.display_name))
        if not self.currency_id.is_zero(self.difference):
            raise UserError(_(
                "The lines add up to %(lines)s, the folio to %(folio)s: every charge of the "
                "folio goes on a line, personal ones included.",
                lines=formatLang(self.env, self.total_reimbursed + self.total_personal,
                                 currency_obj=self.currency_id),
                folio=formatLang(self.env, self.total_folio, currency_obj=self.currency_id)))
        return business

    def action_split(self):
        business = self._bf_check()
        expense = self.expense_id
        personal = self.line_ids - business
        first, others = business[0], business[1:]
        attachments = self.env["ir.attachment"].search([
            ("res_model", "=", "hr.expense"), ("res_id", "=", expense.id)])
        # ⚠️ Every line's values are read BEFORE the first write: the names start from the
        # expense's, and the first write renames it (the copies were « Hotel: Room: Parking »).
        values = {line: line._bf_values() for line in business}
        expense.write(values[first])
        made = expense
        for line in others:
            copy = expense.copy(values[line])
            for attachment in attachments:
                attachment.copy({"res_model": "hr.expense", "res_id": copy.id})
            made |= copy
        if expense.sheet_id:
            # A copy of an expense leaves its report behind (sheet_id is not copied).
            made.write({"sheet_id": expense.sheet_id.id})
        if personal:
            expense.bf_folio_personal_amount = sum(personal.mapped("amount"))
            rows = Markup().join(
                Markup("<li>%s : %s</li>") % (
                    line.name, formatLang(self.env, line.amount, currency_obj=self.currency_id))
                for line in personal)
            body = Markup("%s<ul>%s</ul>") % (
                _("Hotel folio split. Personal charges set aside, not reimbursed:"), rows)
        else:
            body = _("Hotel folio split into %s expenses.", len(made))
        # ⚠️ A log, not message_post: message_post refuses an author without an email address,
        # and an assistant's account very often has none; the whole split was undone with it.
        expense._message_log(body=body)
        return {
            "type": "ir.actions.act_window",
            "res_model": "hr.expense",
            "name": _("Hotel folio"),
            "view_mode": "list,form",
            "domain": [("id", "in", made.ids)],
            "target": "current",
        }


class BfExpenseFolioLine(models.TransientModel):
    _name = "bf.expense.folio.line"
    _description = "Line of a hotel folio"

    wizard_id = fields.Many2one("bf.expense.folio.wizard", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="wizard_id.company_id")
    currency_id = fields.Many2one(related="wizard_id.currency_id")
    name = fields.Char(string="Description", required=True)
    personal = fields.Boolean(help="A personal charge: not reimbursed.")
    product_id = fields.Many2one(
        "product.product", string="Category", check_company=True,
        domain=[("can_be_expensed", "=", True)])
    tax_ids = fields.Many2many(
        "account.tax", string="Taxes", check_company=True,
        domain="[('type_tax_use', '=', 'purchase')]")
    amount = fields.Monetary(string="Amount", currency_field="currency_id",
                             help="Taxes included, as printed on the folio.")

    @api.onchange("product_id")
    def _onchange_product_id(self):
        if self.product_id:
            # ⚠️ The line's company is read through the wizard, which the dialog has not saved
            # yet: empty, it kept every company's taxes out, and an added line had none.
            company = (self.company_id or self.wizard_id.expense_id.company_id
                       or self.env.company)
            self.tax_ids = self.product_id.supplier_taxes_id.filtered_domain(
                self.env["account.tax"]._check_company_domain(company))

    def _bf_values(self):
        self.ensure_one()
        expense = self.wizard_id.expense_id
        vals = {
            "name": _("%(expense)s: %(line)s", expense=expense.name, line=self.name),
            "product_id": self.product_id.id,
            "product_uom_id": self.product_id.uom_id.id,
            "quantity": 1,
            "total_amount_currency": self.amount,
            # ⚠️ The amount in the company's currency goes too, as in Odoo's split: a copy
            # otherwise keeps the original's, and every line of the folio is worth the folio.
            "total_amount": expense.company_currency_id.round(expense.currency_rate * self.amount),
            "tax_ids": [Command.set(self.tax_ids.ids)],
        }
        account = self.product_id.with_company(expense.company_id).product_tmpl_id.\
            _get_product_accounts()["expense"]
        if account:
            vals["account_id"] = account.id
        return vals
