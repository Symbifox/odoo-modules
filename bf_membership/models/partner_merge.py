from odoo import _, models
from odoo.exceptions import UserError


class MergePartnerAutomatic(models.TransientModel):
    """La fusion de contacts et le numéro de membre.

    🔴 L'assistant de fusion d'Odoo recopie sur le contact gardé les valeurs
    des contacts fusionnés. Pour le numéro de membre, c'est un piège : deux
    numéros dans la même base violent la contrainte d'unicité, et une personne
    qui n'est pas responsable des membres n'a pas le droit de l'écrire. Le
    numéro du contact gardé l'emporte ; s'il n'en a pas, il reprend celui d'un
    contact fusionné, qui ne se perd donc pas.
    """

    _inherit = "base.partner.merge.automatic.wizard"

    def _check_membership_merge(self, partners):
        """Une fusion qui touche un contact porteur d'une adhésion ou d'une
        délégation est réservée au rôle Membres.

        🔴 L'assistant d'Odoo réécrit les liens en SQL : il ferait passer une
        adhésion payée, son numéro et sa voix à un autre contact, sans trace et
        par-dessus le gel d'identité. Le refus ne dit rien de plus que « vos
        droits » : il ne doit pas apprendre qui est membre. Les greffons
        l'appellent EN PREMIER dans leur propre `_merge`, avant leurs contrôles
        dont les messages nomment l'adhésion ou l'assemblée.
        """
        if self.env.su:
            return
        if self.env.user.has_group("bf_membership.group_membership_user"):
            # Le rôle d'une société ne fusionne pas les membres d'une autre.
            if partners._membership_foreign():
                self._refuse_membership_merge()
            return
        if partners._membership_held():
            self._refuse_membership_merge()

    def _refuse_membership_merge(self):
        """Le refus neutre, un seul texte pour le socle et les greffons : il ne
        nomme ni le contact, ni l'adhésion, ni l'assemblée, et ne dit pas pourquoi."""
        raise UserError(_("Ces contacts ne peuvent pas être fusionnés avec vos droits. "
                          "Demandez à une personne administratrice."))

    def _merge(self, partner_ids, dst_partner=None, extra_checks=True):
        partners = self.env["res.partner"].browse(partner_ids).exists() | (dst_partner or self.env["res.partner"])
        self._check_membership_merge(partners)
        # Le contact gardé, désigné exactement comme l'assistant d'Odoo le fait.
        merged = self.env["res.partner"].browse(partner_ids).exists()
        dst = dst_partner if dst_partner and dst_partner in merged else self._get_ordered_partner(merged.ids)[-1:]
        # 🔴 Une fusion entre natures différentes réécrit en SQL, par-dessus les
        # contraintes : une personne membre fondue DANS une organisation lui
        # donnait une catégorie réservée aux personnes et retirait sa voix.
        changing = (partners - dst)._membership_held().filtered(lambda p: p.is_company != dst.is_company)
        if changing and not self.env.su:
            raise UserError(_(
                "%s porte une adhésion, une délégation ou une ligne de votant : la fusion "
                "la ferait passer à un contact d'une autre nature (personne ou organisation). "
                "Gardez un contact de la même nature.", changing[:1].display_name))
        origin = {m.id: m.partner_id.id for m in partners.sudo().membership_ids}
        # 🔴 Les consentements gardés sont ceux du contact qui porte l'adhésion,
        # lus AVANT la fusion : une fois les liens réécrits, le doublon gardé
        # porterait l'adhésion, et ses consentements cochés par une autre équipe
        # passeraient pour ceux de la personne membre.
        holder = dst.sudo() if dst.sudo().membership_ids else next(
            (p for p in (partners - dst).sudo() if p.membership_ids), dst.sudo())
        consents = holder.read(["directory_consent", "notice_email_consent"])[0] if holder else {}
        consents.pop("id", None)
        res = super()._merge(partner_ids, dst_partner=dst_partner, extra_checks=extra_checks)
        if consents and dst.exists() and dst.sudo().read(list(consents))[0] != dict(consents, id=dst.id):
            dst.sudo().write(consents)
        moved = self.env["bf.membership"].sudo().browse(list(origin)).exists()
        moved.invalidate_recordset(["partner_id"])
        # Les contraintes que le SQL de la fusion contourne, rejouées : une
        # violation annule toute la fusion.
        moved._check_member_kind()
        moved._check_overlap()
        delegations = dst.sudo().exists().delegate_ids | dst.sudo().exists().delegation_ids
        delegations._check_kinds()
        delegations._check_counts()
        for membership in moved.filtered(lambda m: m.partner_id.id != origin[m.id]):
            # La fusion réécrit le lien en SQL, sans suivi : une ligne au fil.
            membership._message_log(body=_(
                "Adhésion rattachée à %(partner)s par une fusion de contacts, par %(who)s.",
                partner=membership.partner_id.display_name, who=self.env.user.name))
        return res

    def _update_values(self, src_partners, dst_partner):
        # 🔴 Les consentements sont ceux de la personne gardée, jamais ceux d'un
        # contact fusionné : un doublon créé par un inconnu au formulaire public
        # les cocherait à sa place (Loi 25).
        # Les consentements : voir `_merge`, qui les lit avant la fusion.
        sources = src_partners.sudo()
        legue = dst_partner.sudo().member_number or next(
            (p.member_number for p in sources if p.member_number), False)
        if sources.filtered("member_number"):
            sources.write({"member_number": False})
            sources.flush_recordset(["member_number"])
        res = super()._update_values(src_partners, dst_partner)
        if legue and dst_partner.sudo().member_number != legue:
            dst_partner.sudo().member_number = legue
        return res
