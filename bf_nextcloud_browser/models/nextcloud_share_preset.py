"""Configurable public-share presets for the Nextcloud browser.

Each config can carry several presets (e.g. "Interne" = read/write link without
expiry, "Externe" = read-only link with expiry). The browser exposes them as a
dropdown on the share button; the facade maps the chosen preset to the OCS
create-share parameters (permissions / expire_date / password).
"""

from odoo import fields, models


class NextcloudSharePreset(models.Model):
    _name = "nextcloud.share.preset"
    _description = "Nextcloud share preset"
    _order = "sequence, id"

    config_id = fields.Many2one(
        "nextcloud.document.config",
        string="Configuration",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    access = fields.Selection(
        [("read", "Read only"), ("read_write", "Read / write")],
        string="Access",
        default="read",
        required=True,
    )
    expiry_days = fields.Integer(
        string="Expiry (days)",
        default=0,
        help="0 = no expiry.",
    )
    password_protected = fields.Boolean(
        string="Password protected",
        help="Generates a random password for the link.",
    )

    def file_permissions(self, is_dir=False):
        """NC public-link permission bitmask: 1=read, 2=update, 4=create,
        8=delete. A read/write link on a folder allows upload (15); on a single
        file, create/delete are invalid so we cap at read+update (3)."""
        self.ensure_one()
        if self.access == "read":
            return 1
        return 15 if is_dir else 3
