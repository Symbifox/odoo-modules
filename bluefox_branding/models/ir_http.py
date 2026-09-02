"""Publish the installable-app whitelist to the web client.

Which applications may be installed as their own PWA is a tenant decision, not
a code one: `bluefox_branding.scoped_app_modules` holds a comma-separated list
of module names. Empty or absent means every application, which is what a fresh
tenant gets.

The list has to reach the browser because the decision is made in the user
menu, so it rides along in the session info rather than costing a round trip
every time the menu opens.
"""

from odoo import models

SCOPED_APP_MODULES_PARAM = "bluefox_branding.scoped_app_modules"


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        info = super().session_info()
        raw = self.env["ir.config_parameter"].sudo().get_param(
            SCOPED_APP_MODULES_PARAM, ""
        )
        info["scoped_app_modules"] = [
            name.strip() for name in (raw or "").split(",") if name.strip()
        ]
        return info
