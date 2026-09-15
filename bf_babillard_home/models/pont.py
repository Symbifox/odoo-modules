# -*- coding: utf-8 -*-
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class BfDashboard(models.AbstractModel):
    _inherit = "bf.dashboard"
    # ⚠️ `AbstractModel` obligatoire : `bf.dashboard` est abstrait, et une
    # classe concrète qui l'étend empêche le registre de charger.

    @api.model
    def get_dashboard_data(self):
        data = super().get_dashboard_data()
        data["babillard"] = self._get_babillard_summary()
        return data

    @api.model
    def _get_babillard_summary(self):
        """Ce qui m'attend au babillard. Jamais ce qui attend quelqu'un d'autre."""
        try:
            Post = self.env["bf.babillard.post"]
            a_lire = Post.search([
                ("state", "=", "publie"),
                ("lecture_requise", "=", True),
                ("lu_par_moi", "=", False),
            ])
            # 🔴 La rédaction et la modération lisent TOUT le babillard : sans ce
            # filtre, la tuile leur comptait des annonces qui ne leur sont pas
            # adressées, et « J'ai lu » tombait en erreur d'accès.
            a_lire = a_lire.filtered(
                lambda p: p.sudo()._est_destinataire(self.env.user))
            if not a_lire:
                return None
            return {
                "count": len(a_lire),
                "titles": a_lire[:3].mapped("name"),
                "action": "bf_babillard.action_babillard_fil",
            }
        except Exception:  # une section qui échoue ne casse pas l'accueil
            _logger.exception("Babillard : tuile d'accueil indisponible")
            return None
