from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    school_childcare_run_id = fields.Many2one(
        "bf.school.childcare.billing.run", "Child care billing", readonly=True, copy=False, index="btree_not_null",
        ondelete="restrict")

    def _school_childcare_moves(self):
        """The child-care invoices: a month's, or a book of tickets'."""
        books = self.env["bf.school.childcare.ticket_book"].sudo().search([("invoice_id", "in", self.ids)])
        return self.filtered("school_childcare_run_id") | books.invoice_id

    @api.model_create_multi
    def create(self, vals_list):
        # 🔴 As the contract billing: set by the billing only, in sudo too (a user's ir.default).
        for vals in vals_list:
            if not self.env.su and vals.get("school_childcare_run_id"):
                raise UserError(_("The billing sets this, not a person."))
            vals.setdefault("school_childcare_run_id", False)
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and "school_childcare_run_id" in vals and any(
                move.school_childcare_run_id.id != (vals["school_childcare_run_id"] or False) for move in self):
            raise UserError(_("The billing sets this, not a person."))
        return super().write(vals)

    def button_draft(self):
        # 🔴 As a contract invoice: reset to draft, a child-care invoice could be changed and its
        # Relevé 24 with it. A credit note corrects it.
        if not self.env.su and self._school_childcare_moves():
            raise UserError(_("A child-care invoice is not reset to draft: correct it with a credit note."))
        return super().button_draft()

    def _invoice_paid_hook(self):
        result = super()._invoice_paid_hook()
        self.env["bf.school.childcare.ticket_book"].sudo().search([("invoice_id", "in", self.ids)])._school_paid()
        return result


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    school_childcare_student_id = fields.Many2one(
        "res.partner", "Child (child care)", readonly=True, index="btree_not_null", ondelete="restrict",
        help="The child whose child care this line pays: the Relevé 24 is per payer and per child.")

    @api.model_create_multi
    def create(self, vals_list):
        # 🔴 A line ticked child care, or tied to a child, on a child-care invoice would grow a Relevé 24.
        # A credit note copies the values of the line it reverses, as it should: it lowers it.
        if not self.env.su:
            moves = self.env["account.move"].sudo().browse(
                {vals["move_id"] for vals in vals_list
                 if vals.get("move_id") and (vals.get("school_rl24") or vals.get("school_childcare_student_id"))})
            if moves._school_childcare_moves():
                raise UserError(_("The billing sets this, not a person."))
        for vals in vals_list:
            vals.setdefault("school_childcare_student_id", False)
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and {"school_rl24", "school_childcare_student_id"} & set(vals):
            lines = self.sudo()
            moves = lines.move_id | lines.move_id.reversed_entry_id
            if moves._school_childcare_moves() and any(
                    line.school_rl24 != bool(vals.get("school_rl24", line.school_rl24))
                    or line.school_childcare_student_id.id != (vals.get("school_childcare_student_id",
                                                                        line.school_childcare_student_id.id) or False)
                    for line in lines):
                raise UserError(_("The billing sets this, not a person."))
        return super().write(vals)


class AccountPartialReconcile(models.Model):
    _inherit = "account.partial.reconcile"

    def unlink(self):
        """Every undone payment passes here (cancelled, unreconciled, reset to draft)."""
        moves = (self.debit_move_id | self.credit_move_id).move_id
        result = super().unlink()
        books = self.env["bf.school.childcare.ticket_book"].sudo().search(
            [("invoice_id", "in", moves.ids), ("state", "=", "paid")])
        books.filtered(lambda b: b.invoice_id.payment_state not in ("paid", "in_payment"))._school_unpaid()
        return result

