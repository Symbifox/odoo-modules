# -*- coding: utf-8 -*-
from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        """Ship the per-user dark mode preference to the web client so it can
        be read on load without an extra RPC (mirrors how Odoo exposes other
        user settings through the session)."""
        info = super().session_info()
        if info:
            info["bf_dark_mode_enabled"] = self.env.user.bf_dark_mode_enabled
        return info
