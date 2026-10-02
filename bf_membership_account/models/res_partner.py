from odoo import _, models
from odoo.exceptions import UserError


class ResPartner(models.Model):
    _inherit = "res.partner"

    def write(self, vals):
        """🔴 La place dans la hiérarchie des contacts d'un membre dont une facture
        validée porte la cotisation ne change pas : ni son partenaire
        commercial, ni ses parents.

        Odoo porte la créance sur le partenaire commercial, et ouvre la facture
        au portail de toute personne dont le partenaire commercial est un
        parent du client. Rattacher une personne facturée à une entreprise, ou
        donner une société mère à une organisation facturée (`parent_id`, par
        l'interface ou par RPC), ou changer `is_company` d'un parent, ferait
        lire et payer sa facture de cotisation au portail de l'entreprise, et
        rendrait le reçu fiscal impossible. Contrôlé APRÈS l'écriture, sur le
        partenaire commercial et la chaîne des parents réels, pour tous les
        chemins.
        """
        if self.env.su or not ({"parent_id", "is_company"} & vals.keys()):
            return super().write(vals)
        watched = self._invoiced_members()
        before = {p.id: (p.commercial_partner_id, p._ancestry()) for p in watched}
        res = super().write(vals)
        moved = watched.filtered(lambda p: (p.commercial_partner_id, p._ancestry()) != before[p.id])
        if moved:
            if not self.env.user.has_group("bf_membership.group_membership_user"):
                # Sans le rôle Membres : un refus neutre, qui ne dit pas pourquoi.
                self._refuse_membership_change()
            raise UserError(_(
                "%s : une facture de cotisation validée porte son adhésion. Le rattacher à une "
                "entreprise ou à une société mère, ou changer son partenaire commercial, rendrait la "
                "facture lisible et payable au portail de cette entreprise, et le reçu fiscal "
                "impossible. Renversez d'abord la facture par un avoir, ou annulez-la.",
                moved[:1].name))
        return res

    def _ancestry(self):
        """La chaîne des parents de ce contact, du plus proche au plus lointain."""
        self.ensure_one()
        chain, parent = [], self.parent_id
        while parent and parent.id not in chain:
            chain.append(parent.id)
            parent = parent.parent_id
        return tuple(chain)

    def _invoiced_members(self):
        """Parmi ces contacts et ceux qui en descendent, ceux dont une adhésion
        est portée par une facture validée (ni annulée ni renversée)."""
        memberships = self.env["bf.membership"].sudo().with_context(active_test=False).search([
            ("partner_id", "child_of", self.ids), ("invoice_id.state", "=", "posted")])
        return memberships.filtered(lambda m: m._invoice_carries()).partner_id
