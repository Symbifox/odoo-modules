"""La signature se refait quand les pronoms de la fiche changent.

`bf_multi_company_email` refait la signature quand on écrit sur l'usager
(`_BF_SIGNATURE_USER_TRIGGERS`). Les pronoms, eux, s'écrivent aussi sur la
fiche : au portail, dans Contacts, ou par le champ lié de `bf_preferred_name`.
Sans ce crochet, la signature garderait les anciens jusqu'au prochain
changement de gabarit.
"""

from odoo import models


class ResPartner(models.Model):
    _inherit = "res.partner"

    def write(self, vals):
        res = super().write(vals)
        if "bf_pronouns" in vals:
            users = self.sudo().with_context(active_test=False).user_ids.filtered(
                lambda u: not u.share)
            if users:
                users._bf_apply_company_signature()
        return res
