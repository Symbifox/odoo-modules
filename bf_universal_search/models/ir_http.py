from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        """Ship the effective Ctrl+K choice to the web client so the palette
        can read it on load without an extra RPC (same channel Odoo uses for
        the user's other settings; refreshed by the preferences dialog's
        reload_context)."""
        info = super().session_info()
        if info and self.env.user:
            info["bf_universal_search_ctrl_k_star"] = (
                self.env.user._bf_universal_search_ctrl_k_star()
            )
        return info
