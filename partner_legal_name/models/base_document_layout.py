from odoo import fields, models


class BaseDocumentLayout(models.TransientModel):
    """Expose company.legal_name on the document-layout wizard so the
    `company` proxy used in `web.external_layout_*` previews can resolve it.
    Without this, opening Settings > General > Configure Document Layout
    raises AttributeError on the partner_legal_name template inheritance.
    """
    _inherit = "base.document.layout"

    legal_name = fields.Char(
        related="company_id.legal_name",
        readonly=True,
    )
