from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _get_translation_frontend_modules_name(cls):
        # The "one row per column" error is shown on the public survey page,
        # which only loads the translations of the modules listed here.
        return super()._get_translation_frontend_modules_name() + [
            "bf_survey_priorities"
        ]
