from odoo import fields, models


class BfShiftPublishWizard(models.TransientModel):
    _name = "bf.shift.publish.wizard"
    _description = "Publish a schedule"

    schedule_id = fields.Many2one("bf.shift.schedule", required=True, ondelete="cascade")
    warning_ids = fields.One2many(related="schedule_id.warning_ids")
    warning_count = fields.Integer(related="schedule_id.warning_count")
    notify = fields.Boolean("Tell the employees", default=True)

    def action_confirm(self):
        self.ensure_one()
        self.schedule_id._do_publish(notify=self.notify)
        return {"type": "ir.actions.act_window_close"}
