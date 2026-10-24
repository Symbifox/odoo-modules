from odoo import _, api, fields, models
from odoo.exceptions import UserError

RECEIVABLE = ("asset_receivable", "liability_payable")
#: Set by the school billing only, and what any other entry is born with.
MOVE_DEFAULTS = {"school_contract_id": False, "school_billing_sent": False}


class AccountMove(models.Model):
    _inherit = "account.move"

    school_contract_id = fields.Many2one(
        "bf.school.contract", "School contract", readonly=True, copy=False, index="btree_not_null",
        ondelete="restrict")
    school_billing_sent = fields.Boolean("Sent by the school billing", readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        # 🔴 Written on every entry, in sudo too: an invoice made in sudo in a user's session (the
        # re-enrolment fee on the portal, a meal top-up) took their own ir.default, and an
        # employee who is also a parent tied their invoice to another child's contract, whose
        # Relevé 24 it then fed. Only the billing passes them.
        for vals in vals_list:
            if not self.env.su and any(vals.get(f) for f in MOVE_DEFAULTS):
                raise UserError(_("The billing sets this, not a person."))
            for field, value in MOVE_DEFAULTS.items():
                vals.setdefault(field, value)
        return super().create(vals_list)

    def write(self, vals):
        # 🔴 An Invoicing user moved a paid invoice to another contract (its amount paid with it),
        # and marked an invoice sent that nobody received.
        if not self.env.su and any(
                f in vals and any(m[f].id != (vals[f] or False) if f == "school_contract_id"
                                  else m[f] != bool(vals[f]) for m in self)
                for f in MOVE_DEFAULTS):
            raise UserError(_("The billing sets this, not a person."))
        return super().write(vals)

    def _school_cash_share(self):
        """The share of this entry's receivable lines that is money really received or paid
        out: a payment, a bank statement line or an entry on a bank or cash account, net of its
        write-off (a discount, a bad debt); nothing for any other entry (a write-off is not money)."""
        self.ensure_one()
        if self.origin_payment_id:
            cash = self.origin_payment_id._seek_for_lines()[0]
        elif self.statement_line_id:
            cash = self.statement_line_id._seek_for_lines()[0]
        else:
            # 🔴 A payment typed as an entry on the bank or the cash account is money: read as a
            # write-off, it was never refunded at the termination.
            cash = self.line_ids.filtered(lambda l: l.account_id.account_type == "asset_cash")
            if not cash:
                return 0.0
        receivable = self.line_ids.filtered(lambda l: l.account_id.account_type in RECEIVABLE)
        due = abs(sum(receivable.mapped("balance")))
        return min(1.0, abs(sum(cash.mapped("balance"))) / due) if due else 0.0

    def _school_paid_amount(self):
        """Paid on these invoices, net of what went back out on these credit notes.

        Counts the money: a payment or a bank line (net of its write-off), and a credit note of
        ANOTHER invoice applied here (money moved from elsewhere). Does not count a credit note
        of the same invoice, a credit note typed by hand (it reverses no invoice) nor a write-off
        entry: the invoice is settled, nothing was paid.
        """
        terms = self.line_ids.filtered(lambda l: l.display_type == "payment_term")
        paid = 0.0
        for line in terms.filtered(lambda l: l.move_id.move_type == "out_invoice"):
            for partial in line.matched_credit_ids:
                other = partial.credit_move_id.move_id
                if other.move_type == "out_refund":
                    moved = other.reversed_entry_id and other.reversed_entry_id != line.move_id
                    paid += partial.amount if moved else 0.0
                else:
                    paid += partial.amount * other._school_cash_share()
        for line in terms.filtered(lambda l: l.move_id.move_type == "out_refund"):
            for partial in line.matched_debit_ids:
                other = partial.debit_move_id.move_id
                if other.move_type == "out_invoice":
                    moved = line.move_id.reversed_entry_id and other != line.move_id.reversed_entry_id
                    paid -= partial.amount if moved else 0.0
                else:
                    paid -= partial.amount * other._school_cash_share()
        return paid

    def _school_frozen(self):
        """The contract invoices and credit notes, which only a credit note corrects."""
        return self.filtered(lambda m: m.school_contract_id and m.move_type in ("out_invoice", "out_refund"))

    def button_draft(self):
        # 🔴 A contract invoice reset to draft could be changed, cancelled and billed again, or
        # posted back after a termination: the contract, the payers and the Relevé 24 would no
        # longer match it.
        if self._school_frozen():
            raise UserError(_("An invoice of a school contract is not reset to draft: correct it "
                              "with a credit note."))
        return super().button_draft()

    def _post(self, soft=True):
        for move in self.filtered(lambda m: m.move_type == "out_invoice" and m.school_contract_id):
            if move.school_contract_id.state == "terminated" and move.state == "draft":
                raise UserError(_("The contract %s is terminated: its invoices are not confirmed any more.",
                                  move.school_contract_id.name))
        return super()._post(soft)


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    school_rl24 = fields.Boolean(
        "Child care (Relevé 24)",
        help="This line pays for child care in the sense of the Taxation Act: what is paid on it "
             "goes on the payer's Relevé 24.")

    @api.model_create_multi
    def create(self, vals_list):
        # 🔴 As the entry: a line made in sudo took the user's own ir.default and fed the Relevé 24.
        # A reversal copies the line's value, as it should.
        # 🔴 Guarded in write only: an accounting user created a line ticked child care on a posted
        # contract invoice, and the Relevé 24 grew. Refused when forged on a
        # contract entry; unticked, the accounting's own lines pass.
        if not self.env.su:
            forged = {vals["move_id"] for vals in vals_list if vals.get("school_rl24") and vals.get("move_id")}
            if self.env["account.move"].sudo().browse(forged).filtered("school_contract_id"):
                raise UserError(_("The billing sets this, not a person."))
        for vals in vals_list:
            vals.setdefault("school_rl24", False)
        return super().create(vals_list)

    def write(self, vals):
        # 🔴 An Invoicing user ticked child care on a posted contract invoice: the Relevé 24 grew.
        if not self.env.su and "school_rl24" in vals and any(
                line.school_rl24 != bool(vals["school_rl24"])
                and (line.move_id.school_contract_id or line.move_id.reversed_entry_id.school_contract_id)
                for line in self.sudo()):
            raise UserError(_("The billing sets this, not a person."))
        return super().write(vals)
