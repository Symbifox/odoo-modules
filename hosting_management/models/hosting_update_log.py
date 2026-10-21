# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

from .hosting_lang import reader_env


class HostingUpdateLog(models.Model):
    _name = "hosting.update.log"
    _description = "Hosting update log"
    _order = "update_date desc"
    _rec_name = "display_name"

    service_id = fields.Many2one(
        comodel_name="hosting.service",
        string="Service",
        required=True,
        ondelete="cascade",
    )
    update_date = fields.Datetime(
        string="Update date",
        default=fields.Datetime.now,
        required=True,
    )
    from_version_id = fields.Many2one(
        comodel_name="hosting.software.version",
        string="Previous version",
    )
    to_version_id = fields.Many2one(
        comodel_name="hosting.software.version",
        string="Target version",
        required=True,
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Updated by",
        default=lambda self: self.env.user,
    )
    notes = fields.Text(
        string="Notes",
    )
    success = fields.Boolean(
        string="Successful",
        default=True,
    )
    display_name = fields.Char(
        string="Display name",
        compute="_compute_display_name",
        store=True,
    )

    # Champs liés pour accès facile
    partner_id = fields.Many2one(
        related="service_id.partner_id",
        string="Client",
        store=True,
    )
    software_id = fields.Many2one(
        related="service_id.software_id",
        string="Software",
        store=True,
    )

    @api.depends("service_id.name", "to_version_id.version", "update_date")
    def _compute_display_name(self):
        # Nom STOCKÉ : le repli suit la langue de la société.
        env = reader_env(self.env)
        for record in self:
            if record.service_id and record.to_version_id:
                date_str = record.update_date.strftime("%Y-%m-%d") if record.update_date else ""
                record.display_name = f"{record.service_id.name} → {record.to_version_id.version} ({date_str})"
            else:
                record.display_name = env._("Update")
