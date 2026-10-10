from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _get_translation_frontend_modules_name(cls):
        # The upload errors are shown on the public survey page. The frontend
        # only loads the translations of the modules listed here (survey lists
        # itself the same way): without this line they would stay in English.
        return super()._get_translation_frontend_modules_name() + ["bf_survey_upload"]
