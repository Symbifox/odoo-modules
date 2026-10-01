from odoo import api, fields, models


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    # Trace de fusion : le billet fondu garde vers qui il est parti, le billet
    # conservé garde d'où viennent ses messages. Chez Zendesk, le billet
    # conservé n'en garde aucune trace ; ici, les deux côtés la gardent.
    merged_into_id = fields.Many2one(
        "helpdesk.ticket", string="Fusionné dans", readonly=True, copy=False,
        index=True, ondelete="set null", groups="base.group_user",
    )
    merged_ticket_ids = fields.One2many(
        "helpdesk.ticket", "merged_into_id", string="Billets fusionnés ici",
        context={"active_test": False},
    )
    merged_count = fields.Integer(compute="_compute_merged_count")

    @api.depends("merged_ticket_ids")
    def _compute_merged_count(self):
        for ticket in self:
            ticket.merged_count = len(ticket.with_context(active_test=False).merged_ticket_ids)

    def action_view_merged_tickets(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Billets fusionnés",
            "res_model": "helpdesk.ticket",
            "view_mode": "list,form",
            "domain": [("merged_into_id", "=", self.id)],
            "context": {"active_test": False},
        }
