from odoo import models


class ResPartner(models.Model):
    _inherit = "res.partner"

    def _compute_avatar(self, avatar_field, image_field):
        super()._compute_avatar(avatar_field, image_field)
        avatars = self.env["bf.avatar"]
        if avatars._style() == "odoo" or not avatars._contacts_enabled():
            return
        # Odoo draws a grey silhouette for a contact without a picture: give a
        # person the same generated avatar a colleague gets. Companies, delivery
        # and invoice addresses keep Odoo's icons, which say what they are.
        for partner in self:
            if (partner.id and partner.name and not partner[image_field]
                    and not partner.is_company and partner.type in ("contact", "other")
                    and not partner.sudo().user_ids.filtered(lambda u: not u.share)):
                partner[avatar_field] = avatars._render_b64(partner)
