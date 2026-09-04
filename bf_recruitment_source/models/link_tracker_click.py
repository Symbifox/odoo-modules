# Part of bf_recruitment_source. Voir LICENSE.
from odoo import api, models


class LinkTrackerClick(models.Model):
    """Le clic d'un chercheur d'emploi se compte sans garder son adresse IP.

    🔴 `link.tracker.click` du coeur écrit l'adresse IP de qui clique, et rien
    ne la supprime jamais. Publier un lien tracé sur un site d'emploi, ce
    serait donc ouvrir une collecte de renseignements personnels sur des gens
    qui n'ont rien demandé, à qui rien n'est dit, et pour une finalité qui
    n'en a aucun besoin : savoir quel site rapporte des candidatures.

    La minimisation n'est pas un réglage : le compteur a besoin d'un clic, pas
    d'une identité. Le pays reste, parce qu'il ne désigne personne et qu'il
    sert à savoir si une annonce porte hors du marché visé.

    ⚠️ Le reste du parc n'est pas touché : un lien tracé d'infolettre ou de
    campagne garde exactement le comportement du coeur. Seuls les liens
    rattachés à une source de recrutement sont concernés.
    """

    _inherit = "link.tracker.click"

    @api.model
    def _recruitment_link_ids(self, link_ids):
        """Les liens de la liste qui appartiennent à une source de recrutement.

        ⚠️ `sudo` : ce chemin s'exécute pour un visiteur public, qui n'a aucun
        droit sur `hr.recruitment.source`. Le domaine est borné aux
        identifiants qu'on nous passe, il ne rend rien d'autre qu'un ensemble
        d'identifiants de liens.
        """
        link_ids = [link_id for link_id in link_ids if link_id]
        if not link_ids:
            return set()
        sources = self.env["hr.recruitment.source"].sudo().search([
            ("link_tracker_id", "in", link_ids),
        ])
        return set(sources.mapped("link_tracker_id").ids)

    @api.model_create_multi
    def create(self, vals_list):
        """⚠️ La surcharge est sur `create`, pas sur `add_click`.

        `add_click` est le chemin du contrôleur `/r/<code>`, mais ce n'est pas
        le seul : un import, un essai ou un module tiers créent des clics
        directement. Poser la garde sur `create` la met sur TOUS les chemins.
        """
        recruitment_links = self._recruitment_link_ids(
            [vals.get("link_id") for vals in vals_list]
        )
        if recruitment_links:
            vals_list = [
                dict(vals, ip=False)
                if vals.get("link_id") in recruitment_links else vals
                for vals in vals_list
            ]
        return super().create(vals_list)
