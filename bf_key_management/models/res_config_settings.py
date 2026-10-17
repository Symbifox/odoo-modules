from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_key_manager_id = fields.Many2one(
        related="company_id.bf_key_manager_id", readonly=False)
    bf_key_retention_days = fields.Integer(
        related="company_id.bf_key_retention_days", readonly=False)
    bf_key_acknowledgement = fields.Html(
        related="company_id.bf_key_acknowledgement", readonly=False)
    bf_key_acknowledgement_standard = fields.Html(
        string="Standard text", compute="_compute_bf_key_acknowledgement_standard",
        help="Printed when the field above is empty, in the reader's language.")

    # ⚠️ depends= : the settings page opens through onchange, which leaves a compute without
    # dependencies empty.
    @api.depends("company_id")
    def _compute_bf_key_acknowledgement_standard(self):
        from .res_company import default_acknowledgement
        for settings in self:
            settings.bf_key_acknowledgement_standard = default_acknowledgement(self.env)
