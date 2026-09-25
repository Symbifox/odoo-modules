from odoo import fields, models


class BfShiftRefuseWizard(models.TransientModel):
    _name = "bf.shift.refuse.wizard"
    _description = "Refuse an offered shift"

    offer_line_id = fields.Many2one("bf.shift.offer.line", required=True, ondelete="cascade")
    reason = fields.Char()

    def action_confirm(self):
        self.ensure_one()
        self.offer_line_id._record("refused", reason=self.reason)
        return {"type": "ir.actions.act_window_close"}
