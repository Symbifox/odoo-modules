# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class HostingServiceDisconnectWizard(models.TransientModel):
    _name = "hosting.service.disconnect.wizard"
    _description = "Wizard: mark a service for disconnection"

    service_id = fields.Many2one(
        comodel_name="hosting.service",
        string="Service",
        required=True,
        ondelete="cascade",
    )
    disconnect_acknowledged_date = fields.Date(
        string="Acknowledgment date",
        required=True,
        default=fields.Date.context_today,
    )
    disconnect_reason = fields.Char(
        string="Reason",
        required=True,
        help="Migration to X, client decision, end of contract, etc.",
    )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        service_id = self.env.context.get("default_service_id")
        if service_id and "service_id" in fields_list:
            values["service_id"] = service_id
        return values

    def action_confirm(self):
        self.ensure_one()
        self.service_id.write({
            "state": "to_disconnect",
            "disconnect_acknowledged_date": self.disconnect_acknowledged_date,
            "disconnect_reason": self.disconnect_reason,
        })
        return {"type": "ir.actions.act_window_close"}
