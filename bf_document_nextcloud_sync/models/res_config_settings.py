from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    nc_doc_default_config_id = fields.Many2one(
        "nextcloud.document.config",
        string="Default Nextcloud configuration",
        config_parameter="bf_document_nextcloud_sync.default_config_id",
    )
