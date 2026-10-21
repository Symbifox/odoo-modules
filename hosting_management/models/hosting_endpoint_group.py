# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""hosting.endpoint.group — Groupes de postes (taggués Action1 ou natifs).

Un groupe est ancré à un partenaire (client SLA). Optionnellement lié à un
groupe Action1 via ``action1_group_id`` : c'est la clé qu'utilise la synchro
pour rattacher automatiquement les endpoints au bon client.
"""
from odoo import api, fields, models


class HostingEndpointGroup(models.Model):
    _name = "hosting.endpoint.group"
    _description = "Fleet endpoint group"
    _order = "partner_id, name"
    _inherit = ["mail.thread"]

    name = fields.Char(string="Name", required=True, tracking=True)
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Client",
        required=True,
        tracking=True,
        domain="[('is_company', '=', True)]",
        index=True,
        help="All endpoints in the group will be attached to this client "
             "during the Action1 sync.",
    )
    description = fields.Text(string="Description")
    color = fields.Integer(string="Color", default=0)

    # --- Action1 link ---
    action1_group_id = fields.Char(
        string="Action1 group ID",
        index=True,
        copy=False,
        help="Group ID in the Action1 console. Fill it in to turn on "
             "automatic sync of member endpoints.",
    )
    action1_last_sync = fields.Datetime(
        string="Last Action1 sync",
        readonly=True,
        copy=False,
    )

    # --- Membres ---
    endpoint_ids = fields.Many2many(
        comodel_name="hosting.endpoint",
        relation="hosting_endpoint_group_rel",
        column1="group_id",
        column2="endpoint_id",
        string="Endpoints",
    )
    endpoint_count = fields.Integer(
        string="Endpoint count",
        compute="_compute_endpoint_count",
    )

    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "unique_action1_group_id",
            "UNIQUE (action1_group_id)",
            "An Action1 group ID can be linked to only one group.",
        ),
        (
            "unique_name_per_partner",
            "EXCLUDE (partner_id WITH =, name WITH =) WHERE (active = true)",
            "The group name must be unique per client.",
        ),
    ]

    @api.depends("endpoint_ids")
    def _compute_endpoint_count(self):
        for grp in self:
            grp.endpoint_count = len(grp.endpoint_ids)

    def action_view_endpoints(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Endpoints — %s", self.name),
            "res_model": "hosting.endpoint",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [("group_ids", "in", self.id)],
            "context": {"default_partner_id": self.partner_id.id},
        }
