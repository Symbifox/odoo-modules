from odoo import api, fields, models

from ..lib import render
from .bf_avatar import DEFAULT_STYLE, PARAM_CONTACTS, PARAM_PALETTE, PARAM_STYLE


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_avatar_style = fields.Selection(
        render.STYLES, string="Avatar style", default=DEFAULT_STYLE,
        config_parameter=PARAM_STYLE,
        help="How an avatar is drawn for someone who has not set a picture.")
    bf_avatar_palette = fields.Char(
        string="Avatar colours", config_parameter=PARAM_PALETTE,
        help="Hex colours separated by commas. Empty: the main company's colours.")
    bf_avatar_contacts = fields.Boolean(
        string="Avatars for contacts too", config_parameter=PARAM_CONTACTS, default=True,
        help="Contacts without a picture get a generated avatar instead of a grey silhouette.")

    def set_values(self):
        params = self.env["ir.config_parameter"].sudo()
        before = (params.get_param(PARAM_STYLE, DEFAULT_STYLE), params.get_param(PARAM_PALETTE, ""))
        super().set_values()
        after = (params.get_param(PARAM_STYLE, DEFAULT_STYLE), params.get_param(PARAM_PALETTE, ""))
        if before != after:
            self.env["bf.avatar"]._regenerate_generated()

    def action_bf_avatar_redraw(self):
        self.ensure_one()
        self.execute()
        count = self.env["bf.avatar"]._regenerate_generated()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": self.env._("%s generated avatar(s) redrawn. Uploaded pictures were left alone.", count),
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }
