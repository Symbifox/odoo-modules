from odoo import api, fields, models


class BfBiReportTemplateWizard(models.TransientModel):
    """« Depuis un modèle » : choisir un modèle livré, voir ce qu'il montre, le créer."""

    _name = "bf.bi.report.template.wizard"
    _description = "Create a BI report from a template"

    template = fields.Selection(selection="_selection_template", string="Template", required=True)
    description = fields.Text(compute="_compute_status")
    available = fields.Boolean(compute="_compute_status")
    missing = fields.Char(compute="_compute_status", string="Needs")

    @api.model
    def _selection_template(self):
        return [(t["key"], t["name"]) for t in self.env["bf.bi.report"].bf_list_templates()]

    @api.depends("template")
    def _compute_status(self):
        templates = {t["key"]: t for t in self.env["bf.bi.report"].bf_list_templates()}
        modules = {m.name: m.shortdesc for m in self.env["ir.module.module"].sudo().search(
            [("name", "in", sorted({n for t in templates.values() for n in t["missing_modules"]}))])}
        for wizard in self:
            status = templates.get(wizard.template) or {}
            wizard.description = status.get("description")
            wizard.available = bool(status.get("available"))
            wizard.missing = ", ".join([modules.get(m, m) for m in status.get("missing_modules", [])]
                                       + status.get("missing_codes", [])) or False

    def action_create(self):
        self.ensure_one()
        return self.env["bf.bi.report"].bf_create_from_template(self.template)
