# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class HostingSoftwareVersion(models.Model):
    _name = "hosting.software.version"
    _description = "Historique des versions de logiciel"
    _order = "software_id, release_date desc, version desc"
    _rec_name = "display_name"

    software_id = fields.Many2one(
        comodel_name="hosting.software",
        string="Logiciel",
        required=True,
        ondelete="cascade",
    )
    version = fields.Char(
        string="Version",
        required=True,
    )
    display_name = fields.Char(
        string="Nom affiché",
        compute="_compute_display_name",
        store=True,
    )
    release_date = fields.Date(
        string="Date de publication",
    )
    is_lts = fields.Boolean(
        string="LTS",
        help="Version avec support à long terme",
    )
    support_status = fields.Selection(
        selection=[
            ("supported", "Supporté"),
            ("security_only", "Sécurité uniquement"),
            ("deprecated", "Déprécié"),
            ("eol", "Fin de vie"),
        ],
        string="État du support",
        default="supported",
        required=True,
    )
    release_notes = fields.Html(
        string="Notes de version",
        translate=True,
    )
    breaking_changes = fields.Html(
        string="Changements cassants",
        translate=True,
        help="Changements cassants importants à connaître",
    )
    migration_notes = fields.Html(
        string="Notes de migration",
        translate=True,
        help="Instructions pour migrer vers cette version",
    )
    update_impact = fields.Selection(
        selection=[
            ("none", "Aucun"),
            ("minor", "Mineur"),
            ("moderate", "Modéré"),
            ("major", "Majeur"),
            ("breaking", "Cassant"),
        ],
        string="Impact de mise à jour",
        default="minor",
        help="Niveau d'impact lors de la mise à jour vers cette version",
    )
    service_ids = fields.One2many(
        comodel_name="hosting.service",
        inverse_name="installed_version_id",
        string="Services utilisant cette version",
    )
    service_count = fields.Integer(
        string="Nombre de services",
        compute="_compute_service_count",
    )

    _sql_constraints = [
        (
            "software_version_uniq",
            "UNIQUE(software_id, version)",
            "Cette version existe déjà pour ce logiciel !",
        ),
    ]

    @api.depends("software_id.name", "version")
    def _compute_display_name(self):
        for record in self:
            if record.software_id and record.version:
                record.display_name = f"{record.software_id.name} {record.version}"
            else:
                record.display_name = record.version or ""

    @api.depends("service_ids")
    def _compute_service_count(self):
        for record in self:
            record.service_count = len(record.service_ids)
