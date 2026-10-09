"""La fusion de contacts réécrit en SQL toutes les clés étrangères vers `res_partner`, sans
passer par l'ORM ni par aucune garde. Ouverte au groupe « Création de contacts », elle
redirigerait la désignation d'un responsable de la protection des renseignements personnels. Hors administrateur, elle est refusée dès qu'une des fiches est en cause.
"""
from odoo import _, models
from odoo.exceptions import UserError


class MergePartnerAutomatic(models.TransientModel):
    _inherit = "base.partner.merge.automatic.wizard"

    def _merge(self, partner_ids, dst_partner=None, extra_checks=True):
        ids = list(partner_ids) + ([dst_partner.id] if dst_partner else [])
        if not self.env.user._is_system() and ids and self.env["res.partner"].sudo().with_context(active_test=False).search_count(
                ["|", ("privacy_officer_partner_id", "in", ids),
                 "&", ("id", "in", ids),
                 "|", ("privacy_officer_email", "!=", False), ("privacy_officer_partner_id", "!=", False)]):
            raise UserError(_("Une de ces fiches porte ou désigne un responsable de la protection des renseignements "
                            "personnels : seule une personne administratrice peut la fusionner."))
        return super()._merge(partner_ids, dst_partner=dst_partner, extra_checks=extra_checks)
