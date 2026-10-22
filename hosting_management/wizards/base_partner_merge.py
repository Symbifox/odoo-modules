# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""La fusion de contacts réécrit en SQL toutes les clés étrangères vers `res_partner`,
sans passer par l'ORM ni par les droits d'hébergement. Elle déplacerait les services
d'hébergement d'une fiche vers une autre : fusionner une fiche dans un client fait paraître
ses services sur la page d'état de ce client, et dans ses avis de maintenance.

Hors gestionnaire d'hébergement, administrateurs compris, la fusion est donc refusée quand
une fiche qui DISPARAÎT, ou l'un de ses contacts, porte un service (quel que soit son état
ou son archivage), et quand la destination porte des services et changerait de client.
En pratique seuls les administrateurs fusionnent (Odoo lit en route des champs réservés),
donc ce sont eux que la garde arrête.
"""
from odoo import _, models
from odoo.exceptions import UserError

from ..models.hosting_maintenance_window import client_de


class BasePartnerMergeAutomaticWizard(models.TransientModel):
    _inherit = "base.partner.merge.automatic.wizard"

    def _merge(self, partner_ids, dst_partner=None, extra_checks=True):
        self._hosting_garde_fusion(partner_ids, dst_partner)
        return super()._merge(partner_ids, dst_partner=dst_partner, extra_checks=extra_checks)

    def _hosting_garde_fusion(self, partner_ids, dst_partner):
        # Du code serveur en sudo est du code de confiance : `_merge` est privé, et
        # `action_merge` (public) tourne dans l'environnement de l'usager.
        if self.env.su:
            return
        if self.env.user.has_group("hosting_management.group_hosting_manager"):
            return
        partenaires = self.env["res.partner"].browse(partner_ids).exists()
        if len(partenaires) < 2:
            return
        # La même destination que celle qu'Odoo choisira, pour ne juger que les fiches qui
        # disparaissent.
        if dst_partner and dst_partner in partenaires:
            dst = dst_partner
        else:
            dst = self._get_ordered_partner(partenaires.ids)[-1]
        sources = partenaires - dst
        Service = self.env["hosting.service"].sudo().with_context(active_test=False)
        # Odoo ne voit pas une source archivée sous la destination (son contrôle parent/enfant
        # suit `active_test`) : la destination prendrait pour parent l'un de ses propres
        # contacts, et la boucle de parents resterait en base.
        if not dst.parent_id:
            descendants = self.env["res.partner"].sudo().with_context(active_test=False).search(
                [("id", "child_of", dst.ids)]) - dst
            if any(f.parent_id in descendants for f in sources):
                raise UserError(_(
                    "The merge would make the destination a contact of one of its own "
                    "contacts. Unarchive the contacts concerned, or merge them in another order."))
        # Une fiche nommée destinataire hébergement d'un client : le SQL de la fusion réécrit la
        # liste vers la destination, qui prend aussi le parent de la source quand elle n'en a
        # pas. Un contact quelconque devenait ainsi destinataire nommé, avec la page d'état du
        # client au portail et le lien dans ses avis.
        if self.env["res.partner"].sudo().with_context(active_test=False).search_count(
                [("hosting_recipient_ids", "in", sources.ids)]):
            raise UserError(_(
                "One of the contacts that would disappear is a hosting recipient of a client: "
                "the merge would hand its maintenance notices and status page to the "
                "destination. Remove it from the client's hosting recipients first, or ask a "
                "hosting manager."))
        # Les contacts d'une fiche qui disparaît passent sous la destination (Odoo réécrit
        # aussi leur `parent_id`) : leurs services changent de client avec eux.
        if Service.search_count([("partner_id", "child_of", sources.ids)]):
            raise UserError(_(
                "One of the contacts that would disappear, or one of its own contacts, "
                "carries hosting services. Merging would move them to another client, onto "
                "its status page and into its maintenance notices. Keep the record that "
                "carries the services as the destination, or ask a hosting manager."))
        # La destination reste, mais Odoo lui donne le parent d'une source quand elle n'en a
        # pas, et la fait société si une source l'est : son client peut changer.
        if self._hosting_clients_apres_fusion(sources, dst) - {client_de(dst)} \
                and Service.search_count([("partner_id", "child_of", dst.ids)]):
            raise UserError(_(
                "The merge would attach the destination contact to another client, and it "
                "carries hosting services, which would follow it onto that client's status "
                "page and into its maintenance notices. Only a hosting manager can merge it."))

    def action_update_all_process(self):
        """« Tout fusionner automatiquement » finit par un SQL brut qui retire le statut de
        société à toute fiche qui a un parent : le client d'une filiale devient sa maison mère,
        hors de `_merge` et de sa garde."""
        self._hosting_garde_mise_a_jour_globale()
        return super().action_update_all_process()

    def _hosting_garde_mise_a_jour_globale(self):
        if self.env.su or self.env.user.has_group("hosting_management.group_hosting_manager"):
            return
        Partner = self.env["res.partner"].sudo().with_context(active_test=False)
        filiales = Partner.search([("is_company", "=", True), ("parent_id", "!=", False)])
        if filiales and self.env["hosting.service"].sudo().with_context(active_test=False).search_count(
                [("partner_id", "child_of", filiales.ids)]):
            raise UserError(_(
                "Merging everything automatically would turn subsidiaries into plain contacts "
                "of their parent company, and some of them carry hosting services. Only a "
                "hosting manager can run it."))

    @staticmethod
    def _hosting_clients_apres_fusion(sources, dst_partner):
        """Les clients possibles de la destination après la fusion, d'après `_update_values`
        d'Odoo : la destination garde son parent ; sans parent, elle prend celui d'une source
        (lequel dépend de l'ordre des sources : on les prend tous). Elle devient société si
        une fiche l'est. Un parent qui est l'une des fiches fusionnées est ignoré."""
        if any(f.is_company for f in sources | dst_partner):
            return {dst_partner}
        if dst_partner.parent_id:
            parents = [dst_partner.parent_id]
        else:
            parents = [f.parent_id for f in sources
                       if f.parent_id and f.parent_id not in sources | dst_partner]
        return {client_de(parent) for parent in parents} or {dst_partner}
