# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class HostingUpdateLog(models.Model):
    _name = "hosting.update.log"
    _description = "Journal des mises à jour d'hébergement"
    _order = "update_date desc"
    _rec_name = "display_name"

    service_id = fields.Many2one(
        comodel_name="hosting.service",
        string="Service",
        required=True,
        ondelete="cascade",
    )
    update_date = fields.Datetime(
        string="Date de mise à jour",
        default=fields.Datetime.now,
        required=True,
    )
    from_version_id = fields.Many2one(
        comodel_name="hosting.software.version",
        string="Version précédente",
    )
    to_version_id = fields.Many2one(
        comodel_name="hosting.software.version",
        string="Version cible",
        required=True,
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Mis à jour par",
        default=lambda self: self.env.user,
    )
    notes = fields.Text(
        string="Notes",
    )
    success = fields.Boolean(
        string="Réussi",
        default=True,
    )
    display_name = fields.Char(
        string="Nom affiché",
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
        string="Logiciel",
        store=True,
    )

    @api.depends("service_id.name", "to_version_id.version", "update_date")
    def _compute_display_name(self):
        for record in self:
            if record.service_id and record.to_version_id:
                date_str = record.update_date.strftime("%Y-%m-%d") if record.update_date else ""
                record.display_name = f"{record.service_id.name} → {record.to_version_id.version} ({date_str})"
            else:
                record.display_name = "Mise à jour"
