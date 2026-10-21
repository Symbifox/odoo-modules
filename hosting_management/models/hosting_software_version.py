# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class HostingSoftwareVersion(models.Model):
    _name = "hosting.software.version"
    _description = "Software version history"
    _order = "software_id, release_date desc, version desc"
    _rec_name = "display_name"

    software_id = fields.Many2one(
        comodel_name="hosting.software",
        string="Software",
        required=True,
        ondelete="cascade",
    )
    version = fields.Char(
        string="Version",
        required=True,
    )
    display_name = fields.Char(
        string="Display name",
        compute="_compute_display_name",
        store=True,
    )
    release_date = fields.Date(
        string="Release date",
    )
    is_lts = fields.Boolean(
        string="LTS",
        help="Long-term support version",
    )
    support_status = fields.Selection(
        selection=[
            ("supported", "Supported"),
            ("security_only", "Security only"),
            ("deprecated", "Deprecated"),
            ("eol", "End of life"),
        ],
        string="Support status",
        default="supported",
        required=True,
    )
    release_notes = fields.Html(
        string="Release notes",
        translate=True,
    )
    breaking_changes = fields.Html(
        string="Breaking changes",
        translate=True,
        help="Important breaking changes to know about",
    )
    migration_notes = fields.Html(
        string="Migration notes",
        translate=True,
        help="Instructions to migrate to this version",
    )
    update_impact = fields.Selection(
        selection=[
            ("none", "None"),
            ("minor", "Minor"),
            ("moderate", "Moderate"),
            ("major", "Major"),
            ("breaking", "Breaking"),
        ],
        string="Update impact",
        default="minor",
        help="Impact level when updating to this version",
    )
    service_ids = fields.One2many(
        comodel_name="hosting.service",
        inverse_name="installed_version_id",
        string="Services using this version",
    )
    service_count = fields.Integer(
        string="Service count",
        compute="_compute_service_count",
    )

    _sql_constraints = [
        (
            "software_version_uniq",
            "UNIQUE(software_id, version)",
            "This version already exists for this software!",
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
