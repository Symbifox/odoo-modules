from odoo import api, fields, models


class AccountAnalyticLine(models.Model):
    """Link timesheet/analytic lines to a helpdesk ticket.

    BF-native subset of what OCA ``helpdesk_mgmt_timesheet`` provides, minus
    the ``project_timesheet_time_control`` timer stack (BF already ships its
    own ``bf_timesheet_timer``). Lines carry the ticket's ``project_id`` so
    they feed the team hour bank, which aggregates ``account.analytic.line``
    by project.
    """

    _inherit = "account.analytic.line"

    ticket_id = fields.Many2one(
        comodel_name="helpdesk.ticket",
        string="Ticket",
        index=True,
        domain=[("project_id", "!=", False)],
        help="Ticket helpdesk auquel cette ligne de temps est rattachée.",
    )
    ticket_partner_id = fields.Many2one(
        comodel_name="res.partner",
        related="ticket_id.partner_id",
        string="Client du ticket",
        store=True,
        compute_sudo=True,
    )

    @api.onchange("ticket_id")
    def _onchange_ticket_id(self):
        for line in self:
            if not line.ticket_id:
                continue
            if line.ticket_id.project_id:
                line.project_id = line.ticket_id.project_id
            if line.ticket_id.task_id:
                line.task_id = line.ticket_id.task_id
