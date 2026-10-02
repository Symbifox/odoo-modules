from odoo import models


class MergePartnerAutomatic(models.TransientModel):
    _inherit = "base.partner.merge.automatic.wizard"

    def _merge(self, partner_ids, dst_partner=None, extra_checks=True):
        """🔴 Avant que l'assistant réécrive en SQL ce qui pointe les contacts
        fusionnés : un contact créé par le formulaire public d'adhésion qui
        porte une facture ne se fusionne pas (`bf.membership._check_partner_merge`)."""
        partners = self.env["res.partner"].browse(partner_ids).exists()
        if dst_partner:
            partners |= dst_partner
        # 🔴 Le contrôle du socle EN PREMIER : sans le rôle Membres, un refus
        # neutre, avant les nôtres, dont les messages nomment le formulaire et
        # la facture (et diraient donc qu'un contact a demandé à adhérer).
        self._check_membership_merge(partners)
        # Sans contact choisi, Odoo garde le dernier de son ordre (le plus ancien
        # contact actif) : le même calcul, pour contrôler le vrai contact gardé.
        dst = dst_partner or (self._get_ordered_partner(partners.ids)[-1:] if len(partners) > 1 else partners)
        self.env["bf.membership"]._check_partner_merge(partners, dst)
        return super()._merge(partner_ids, dst_partner=dst_partner, extra_checks=extra_checks)
