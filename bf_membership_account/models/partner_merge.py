from odoo import _, models
from odoo.exceptions import UserError


class MergePartnerAutomatic(models.TransientModel):
    _inherit = "base.partner.merge.automatic.wizard"

    def _merge(self, partner_ids, dst_partner=None, extra_checks=True):
        """🔴 L'assistant de fusion réécrit en SQL tout ce qui pointe les contacts
        fusionnés, factures comprises. Fusionner une organisation facturée DANS
        un doublon rattaché à une autre entreprise ferait passer sa facture de
        cotisation sous cette entreprise, lisible à son portail.

        La garde neutre du socle passe d'abord (sans le rôle Membres, un refus
        qui ne dit pas pourquoi), puis la nôtre, qui dit pourquoi.
        """
        partners = self.env["res.partner"].browse(partner_ids).exists()
        if dst_partner:
            partners |= dst_partner
        if len(partners) > 1:
            dst = dst_partner or self._get_ordered_partner(partners.ids)[-1:]
            self._check_membership_merge(partners)
            self._check_fee_invoice_merge(partners, dst)
        return super()._merge(partner_ids, dst_partner=dst_partner, extra_checks=extra_checks)

    def _check_fee_invoice_merge(self, partners, dst):
        """Refuser la fusion si un contact fusionné (ou un contact qui en
        descend) porte une facture de cotisation validée et que le contact
        gardé n'a pas la même chaîne de parents : la facture changerait de
        partenaire commercial ou de parents, donc de portail."""
        for src in (partners - dst).sudo():
            if src._invoiced_members() and dst.sudo()._ancestry() != src._ancestry():
                raise UserError(_(
                    "« %(src)s » porte une facture de cotisation validée : la fusionner dans « %(dst)s », "
                    "rattaché ailleurs, ferait passer la facture sous un autre partenaire commercial ou "
                    "d'autres parents, lisible à leur portail. Renversez d'abord la facture par un "
                    "avoir, ou gardez le contact facturé.", src=src.display_name, dst=dst.display_name))
