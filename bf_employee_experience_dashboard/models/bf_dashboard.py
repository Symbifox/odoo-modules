"""Tuile « Expérience employé » du tableau de bord Blue Fox.

Même patron d'extension que les autres sections : une clé de plus dans
`get_dashboard_data()`, calculée par une aide défensive. Le tableau de bord ne
doit jamais casser parce qu'une section échoue.
"""

import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class BfDashboard(models.AbstractModel):
    _inherit = "bf.dashboard"
    # ⚠️ `AbstractModel` obligatoire : `bf.dashboard` est abstrait (aucune
    # table), et `BaseModel._build_model_check_base` lève `TypeError` dès qu'une
    # classe concrète l'étend. Le registre ne charge alors plus du tout.

    @api.model
    def get_dashboard_data(self):
        data = super().get_dashboard_data()
        data["employee_experience"] = self._get_ex_summary()
        return data

    @api.model
    def _get_ex_summary(self):
        try:
            Benefit = self.env["bf.ex.benefit"]
            benefits = Benefit.search([("company_id", "=", self.env.company.id)])
            if not benefits:
                return None
            with_rights = benefits.filtered(lambda b: b.entitled_count)
            uptake = (
                sum(with_rights.mapped("uptake_rate")) / len(with_rights)
                if with_rights else 0.0
            )
            return {
                "benefit_count": len(benefits),
                "uptake_display": "%d %%" % round(uptake),
                "unused_count": len(benefits.filtered("unused")),
                "annual_cost": round(sum(benefits.mapped("annual_cost"))),
                "currency": self.env.company.currency_id.symbol or "",
            }
        except Exception:
            _logger.exception("Erreur au chargement du sommaire Expérience employé")
            return None
