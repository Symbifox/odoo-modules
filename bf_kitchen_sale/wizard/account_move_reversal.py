from odoo import models


class AccountMoveReversal(models.TransientModel):
    _inherit = "account.move.reversal"

    def reverse_moves(self, is_modify=False):
        """The credit note's "modify" mode with a reversal dated later is not a cancellation for Odoo
        (the reversal is posted by its scheduled job), yet a new draft replaces the invoice: its
        food goes back now, as in the same-day case, and the new invoice takes out its own."""
        res = super().reverse_moves(is_modify=is_modify)
        if is_modify:
            self.move_ids.filtered(lambda m: m.move_type == "out_invoice")._bf_kitchen_return_invoice()
        return res
