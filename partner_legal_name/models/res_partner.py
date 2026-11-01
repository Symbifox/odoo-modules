from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    legal_name = fields.Char(
        string="Legal name",
        help=(
            "Full legal name (e.g. 'Les services de consultation Blue Fox, Inc.'). "
            "When empty, reports fall back to Name. Use Name for the trade/DBA "
            "name shown in the UI."
        ),
        tracking=True,
    )

    @property
    def legal_or_display_name(self):
        return self.legal_name or self.name
