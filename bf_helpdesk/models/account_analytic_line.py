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
    @api.model_create_multi
    def create(self, vals_list):
        self._bf_check_ticket([v.get("ticket_id") for v in vals_list])
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("ticket_id"):
            self._bf_check_ticket([vals["ticket_id"]])
        return super().write(vals)

    def _bf_check_ticket(self, ticket_ids):
        """Le client du billet est recopié en sudo sur la ligne : on n'attache
        une ligne qu'à un billet que l'usager peut lire."""
        ids = [i for i in ticket_ids if i]
        if ids and not self.env.su:
            self.env["helpdesk.ticket"].browse(ids).check_access("read")

    ticket_partner_id = fields.Many2one(
        comodel_name="res.partner",
        compute="_compute_ticket_partner_id",
        string="Client du ticket",
        store=True,
        compute_sudo=True,
    )

    @api.depends("ticket_id.partner_id")
    def _compute_ticket_partner_id(self):
        """Le client du billet, recopié en sudo. Quand le billet vient de
        l'appelant (ligne neuve ou billet changé dans un onchange), seulement si
        l'usager peut le lire : sinon le client de n'importe quel billet sortait."""
        user_env = self.env(su=False)
        for line in self:
            ticket = line.ticket_id
            if ticket and ticket != line._origin.ticket_id \
                    and not ticket.with_env(user_env)._filtered_access("read"):
                line.ticket_partner_id = False
            else:
                line.ticket_partner_id = ticket.partner_id

    @api.onchange("ticket_id")
    def _onchange_ticket_id(self):
        for line in self:
            if not line.ticket_id:
                continue
            if line.ticket_id.project_id:
                line.project_id = line.ticket_id.project_id
            if line.ticket_id.task_id:
                line.task_id = line.ticket_id.task_id
