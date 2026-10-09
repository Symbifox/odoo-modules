from odoo import api, fields, models


class BfDocumentExportWizard(models.TransientModel):
    _name = "bf.document.export.wizard"
    _description = "Export the registry"

    template_id = fields.Many2one(
        "bf.document.export.template",
        required=True,
        default=lambda self: self.env["bf.document.export.template"].search([], limit=1),
    )
    document_ids = fields.Many2many(
        "project.document",
        "bf_document_export_wizard_document_rel",
        string="Documents",
        help="Leave empty to export every document the template covers.",
    )
    document_count = fields.Integer(compute="_compute_document_count")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        context = self.env.context
        if context.get("active_model") == "project.document" and context.get("active_ids"):
            values["document_ids"] = [(6, 0, context["active_ids"])]
        return values

    @api.depends("template_id", "document_ids")
    def _compute_document_count(self):
        Document = self.env["project.document"]
        for wizard in self:
            if not wizard.template_id:
                wizard.document_count = 0
                continue
            domain = wizard.template_id._document_domain()
            if wizard.document_ids:
                domain = domain + [("id", "in", wizard.document_ids.ids)]
            wizard.document_count = Document.search_count(domain)

    def action_export(self):
        self.ensure_one()
        run = self.env["bf.document.export.run"].create({
            "template_id": self.template_id.id,
            "document_ids": [(6, 0, self.document_ids.ids)],
            "trigger": "manual",
        })
        run._queue()
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.document.export.run",
            "res_id": run.id,
            "view_mode": "form",
            "target": "current",
        }
