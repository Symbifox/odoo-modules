# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""L'état du parc, servi à la tuile « Parc informatique » de l'accueil.

⚠️ `bf.dashboard` est un `AbstractModel` (aucune table). L'étendre par une
classe CONCRÈTE fait lever `TypeError` à `_build_model_check_base` et le
registre ne charge plus du tout. D'où `models.AbstractModel`.

La carte a attendu que deux satellites du tableau de bord cessent d'importer
`@bf_dashboard/js/bf_dashboard`, un chemin qu'aucun module installé ne
fournissait plus depuis l'absorption de `bf_dashboard` dans `bf_home`. Ils
importent maintenant `@bf_home/js/bf_dashboard`, et la carte est posée.

Le collecteur suit le contrat du socle plutôt que son propre `try` :

* il passe par `_safe()`, dont le point de reprise empêche une requête
  avortée d'emporter les collecteurs suivants ;
* un échec est nommé dans `data["failed"]`, et la tuile dit « Données non
  disponibles » au lieu de disparaître ;
* une personne sans accès à l'hébergement reçoit `None` : pas de tuile du
  tout. Sans ce garde, la lecture lèverait `AccessError`, `_safe()` la
  prendrait pour une panne, et la tuile annoncerait une défaillance à
  quelqu'un qui n'a simplement pas à voir le parc.
"""

import logging

from odoo import _, api, models
from odoo.exceptions import AccessError

from odoo.addons.bf_home.models.bf_dashboard import FAILED

_logger = logging.getLogger(__name__)

HOSTING_USER = "hosting_management.group_hosting_user"

# Les lignes de la tuile, et le filtre de la vue de recherche que chacune ouvre.
# Une clé hors de cette liste n'ouvre rien : `action_open_patch_systems` est
# appelable par RPC, et le contexte qu'elle rend ne se compose pas à la volée.
STATE_FILTERS = ("stale", "blind", "security", "reboot")

# 🔴 Chaque chiffre compte EXACTEMENT ce que son clic ouvre : le domaine du
# filtre, borné aux systèmes suivis comme la liste ouverte. La 18.0.4.4.0
# comptait « Redémarrage requis » sur `patch_state`, alors que le filtre porte
# sur `reboot_required` : un système avec un correctif de sécurité en attente
# ET un redémarrage dû passe en « security » (l'état le plus grave gagne), et
# la tuile disait 0 redémarrage au-dessus d'une liste qui en montrait un.
SUIVIS = [("patch_state", "!=", "unmanaged")]
ROW_DOMAINS = {
    "muted": [("patch_state", "=", "stale")],
    "blind": [("patch_state", "=", "blind")],
    "security": [("patch_state", "=", "security")],
    "reboot": [("reboot_required", "=", True)],
}


class BfDashboard(models.AbstractModel):
    _inherit = "bf.dashboard"

    @api.model
    def _can_see_fleet(self):
        """⚠️ `has_group` rend Faux pour le superutilisateur, qui n'appartient
        à aucun groupe : sans le `su`, les crons et le shell ne verraient rien."""
        return self.env.su or self.env.user.has_group(HOSTING_USER)

    @api.model
    def get_dashboard_data(self):
        data = super().get_dashboard_data()
        value = self._safe(self._get_hosting_patch_summary)
        if value is FAILED:
            data["hosting_patch"] = None
            data.setdefault("failed", {})["hosting_patch"] = True
        else:
            data["hosting_patch"] = value
        return data

    @api.model
    def _get_hosting_patch_summary(self):
        """Quatre nombres et une date, pas un graphique.

        ⚠️ `muted` et `blind` passent avant `security` : ce sont les deux qui
        disent « personne ne mesure » plutôt que « voici la mesure ».
        """
        if not self._can_see_fleet():
            return None
        Systeme = self.env["bf.patch.system"]
        suivis = Systeme.search([("patch_state", "!=", "unmanaged")])
        etats = suivis.mapped("patch_state")
        dernier = [s for s in suivis.mapped("agent_last_report") if s]
        lignes = {cle: Systeme.search_count(SUIVIS + domaine)
                  for cle, domaine in ROW_DOMAINS.items()}
        return {
            "systems_tracked": len(suivis),
            **lignes,
            "updates": etats.count("updates"),
            "ok": etats.count("ok"),
            # Le compteur ne part JAMAIS sans la date qui l'a produit.
            "last_report": max(dernier).isoformat() if dernier else None,
            "endpoints_unmanaged": self.env["hosting.endpoint"].search_count(
                [("patch_state", "=", "unmanaged")]
            ),
        }

    @api.model
    def action_open_patch_systems(self, state=None):
        """Ouvre les systèmes suivis, filtrés sur une ligne de la tuile, ou
        les systèmes à regarder quand on clique la carte elle-même.

        ⚠️ Sans `_`, donc appelable par RPC : le garde de groupe est ici et
        non dans le gabarit, qui ne protège rien.
        """
        if not self._can_see_fleet():
            raise AccessError(_("L'état du parc est réservé à l'hébergement."))
        if state in STATE_FILTERS:
            action = self.env["ir.actions.act_window"]._for_xml_id(
                "bf_hosting_patch.bf_patch_system_action")
            action["context"] = {f"search_default_{state}": 1}
            # La même borne que le chiffre : un système révoqué peut garder
            # son dernier `reboot_required`, et n'est pas compté.
            action["domain"] = SUIVIS
            return action
        return self.env["ir.actions.act_window"]._for_xml_id(
            "bf_hosting_patch.bf_patch_system_action_attention")
