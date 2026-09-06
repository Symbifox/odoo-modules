# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Les chiffres de l'état du parc, servis au tableau de bord.

⚠️ `bf.dashboard` est un `AbstractModel` (aucune table). L'étendre par une
classe CONCRÈTE fait lever `TypeError` à `_build_model_check_base` et le
registre ne charge plus du tout. D'où `models.AbstractModel`.

⚠️ La carte elle-même n'est pas posée ici. Au 2026-08-31, deux satellites du
tableau de bord (`bf_subscription_dashboard`, `bf_hour_bank_dashboard`)
importent encore `@bf_dashboard/js/bf_dashboard`, un chemin qu'aucun module
installé ne fournit depuis l'absorption de `bf_dashboard` dans `bf_home`.
Ajouter un troisième client à cette surface avant que ce soit réglé
reviendrait à empiler sur du cassé. Les données sont donc prêtes, la carte
attend.
"""

import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class BfDashboard(models.AbstractModel):
    _inherit = "bf.dashboard"

    @api.model
    def get_dashboard_data(self):
        data = super().get_dashboard_data()
        try:
            data["hosting_patch"] = self._get_hosting_patch_summary()
        except Exception:
            # Une tuile qui plante ne doit pas emporter le tableau de bord
            # entier. Mais elle doit le DIRE : un `except` muet ferait
            # disparaître la tuile sans que personne sache pourquoi.
            _logger.exception("bf_hosting_patch : résumé du parc indisponible")
            data["hosting_patch"] = None
        return data

    @api.model
    def _get_hosting_patch_summary(self):
        """Trois nombres, pas un graphique.

        ⚠️ `muets` vient en premier et compte à part : c'est le seul des trois
        qui dit « personne ne mesure » plutôt que « voici la mesure ».
        """
        Systeme = self.env["bf.patch.system"]
        suivis = Systeme.search([("patch_state", "!=", "unmanaged")])
        etats = suivis.mapped("patch_state")
        dernier = [s for s in suivis.mapped("agent_last_report") if s]
        return {
            "systems_tracked": len(suivis),
            "muted": etats.count("stale"),
            "security": etats.count("security"),
            "blind": etats.count("blind"),
            "reboot": etats.count("reboot"),
            "updates": etats.count("updates"),
            "ok": etats.count("ok"),
            # Le compteur ne part JAMAIS sans la date qui l'a produit.
            "last_report": max(dernier).isoformat() if dernier else None,
            "endpoints_unmanaged": self.env["hosting.endpoint"].search_count(
                [("patch_state", "=", "unmanaged")]
            ),
        }
