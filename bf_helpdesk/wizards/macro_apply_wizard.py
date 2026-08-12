from odoo import fields, models


class HelpdeskMacroApplyWizard(models.TransientModel):
    _name = "helpdesk.macro.apply.wizard"
    _description = "Apply a macro to a helpdesk ticket"

    ticket_id = fields.Many2one(
        comodel_name="helpdesk.ticket",
        required=True,
    )
    macro_id = fields.Many2one(
        comodel_name="helpdesk.macro",
        required=True,
        domain="['|', ('applicable_team_ids', '=', False), "
               "('applicable_team_ids', 'in', ticket_team_id)]",
    )
    ticket_team_id = fields.Many2one(
        related="ticket_id.team_id",
        readonly=True,
    )
    preview_html = fields.Html(
        related="macro_id.body_html",
        readonly=True,
    )

    def action_apply(self):
        self.ensure_one()
        if not self.macro_id:
            return False
        self.ticket_id.message_post(
            body=self.macro_id.body_html,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        return {"type": "ir.actions.act_window_close"}
