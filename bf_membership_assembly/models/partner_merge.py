from odoo import _, models
from odoo.exceptions import UserError


class MergePartnerAutomatic(models.TransientModel):
    """La fusion de contacts et les listes de votants.

    🔴 L'assistant de fusion d'Odoo réécrit en SQL toutes les références au
    contact qui disparaît, sans passer par les verrous des modèles. Sur la
    liste des votants d'une assemblée tenue, il changerait le membre ou la
    personne qui vote d'une ligne gelée. Et quand le contact gardé figure
    déjà à la même liste, la contrainte d'unicité fait échouer la réécriture :
    l'assistant supprime alors la ligne, et ses bulletins remis avec elle.

    Avec le rôle Membres, la fusion est refusée quand un contact qui
    disparaîtrait figure, comme membre ou comme personne qui vote, à la liste
    d'une assemblée convoquée, ouverte ou close, et le message le dit. Une
    assemblée en brouillon ou annulée ne bloque pas : sa liste se rebâtit
    depuis le registre. Le contact gardé, lui, peut figurer à toutes les
    listes : ses lignes ne sont pas réécrites.

    Sans le rôle, toute fusion qui touche un contact porté par une assemblée
    (ligne de votant, bulletin reçu, présidence, secrétariat, dépouillement)
    reçoit le refus neutre du socle, par le crochet `_membership_held`.
    """

    _inherit = "base.partner.merge.automatic.wizard"

    def _merge(self, partner_ids, dst_partner=None, extra_checks=True):
        partners = self.env["res.partner"].browse(partner_ids).exists()
        involved = partners | (dst_partner or self.env["res.partner"])
        # 🔴 La garde du socle EN PREMIER. Sans le rôle Membres, elle refuse sans
        # rien nommer toute fusion qui touche un contact retenu ; le crochet
        # `_membership_held` de ce module y ajoute les contacts portés par une
        # assemblée. Le contrôle ci-dessous, réservé au rôle, nomme le contact
        # et l'assemblée.
        self._check_membership_merge(involved)
        if len(partners) >= 2 and (self.env.su or self.env.user.has_group("bf_membership.group_membership_user")):
            # Les contacts qui disparaissent, désignés comme l'assistant le fait.
            if dst_partner and dst_partner in partners:
                sources = partners - dst_partner
            else:
                sources = self._get_ordered_partner(partners.ids)[:-1]
            self._check_assembly_voters(sources)
        return super()._merge(partner_ids, dst_partner=dst_partner, extra_checks=extra_checks)

    def _check_assembly_voters(self, partners):
        lines = self.env["bf.membership.assembly.voter"].sudo().search([
            "|", ("member_id", "in", partners.ids), ("representative_id", "in", partners.ids),
            ("assembly_id.state", "not in", ("draft", "cancelled")),
        ])
        if lines:
            names = lines.member_id.filtered(lambda p: p in partners) | lines.representative_id.filtered(
                lambda p: p in partners)
            raise UserError(_(
                "%(names)s figure à la liste des votants d'une assemblée convoquée, "
                "ouverte ou close (%(assemblies)s). La fusion réécrirait sa ligne, ou "
                "la supprimerait avec ses bulletins. Gardez plutôt ce contact, et "
                "fusionnez l'autre dans le sien s'il ne figure à aucune de ces listes.",
                names=", ".join(names.mapped("name")),
                assemblies=", ".join(lines.assembly_id.mapped("name"))))
