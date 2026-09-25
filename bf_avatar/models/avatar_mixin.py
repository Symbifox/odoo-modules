from odoo import models


class AvatarMixin(models.AbstractModel):
    _inherit = "avatar.mixin"

    def _avatar_generate_svg(self):
        if self.env["bf.avatar"]._style() == "odoo":
            return super()._avatar_generate_svg()
        return self.env["bf.avatar"]._render_b64(self)
