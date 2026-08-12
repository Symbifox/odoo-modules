"""Configurable public-share presets for the Nextcloud browser.

Each config can carry several presets (e.g. "Interne" = read/write link without
expiry, "Externe" = read-only link with expiry). The browser exposes them as a
dropdown on the share button; the facade maps the chosen preset to the OCS
create-share parameters (permissions / expire_date / password).
"""

from odoo import fields, models


class NextcloudSharePreset(models.Model):
    _name = "nextcloud.share.preset"
    _description = "Prereglage de partage Nextcloud"
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
        [("read", "Lecture seule"), ("read_write", "Lecture / ecriture")],
        string="Acces",
        default="read",
        required=True,
    )
    expiry_days = fields.Integer(
        string="Expiration (jours)",
        default=0,
        help="0 = aucune expiration.",
    )
    password_protected = fields.Boolean(
        string="Protege par mot de passe",
        help="Genere un mot de passe aleatoire pour le lien.",
    )

    def file_permissions(self, is_dir=False):
        """NC public-link permission bitmask: 1=read, 2=update, 4=create,
        8=delete. A read/write link on a folder allows upload (15); on a single
        file, create/delete are invalid so we cap at read+update (3)."""
        self.ensure_one()
        if self.access == "read":
            return 1
        return 15 if is_dir else 3
