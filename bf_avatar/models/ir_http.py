from odoo import models

from ..lib import render


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        result = super().session_info()
        user = self.env.user
        result["bf_avatar_composer"] = bool(
            user and user._is_internal()
            and self.env["bf.avatar"]._style() in render.CHARACTER_STYLES)
        return result
