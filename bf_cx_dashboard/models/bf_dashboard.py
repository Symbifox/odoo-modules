"""NPS tile data for the Blue Fox dashboard.

Same extension pattern as the other sections of bf.dashboard: add a key in
get_dashboard_data() and compute it in a defensive helper (the dashboard
must never break because one section fails). The NPS math itself lives in
bf.cx.feedback._nps_summary() - single source of truth with the digest and
the programs (honest window, score hidden under 10 answers).
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class BfDashboard(models.AbstractModel):
    _inherit = "bf.dashboard"
    # `AbstractModel` obligatoire : `bf.dashboard` est abstrait (aucune table),
    # et `BaseModel._build_model_check_base` lève `TypeError` dès qu'une classe
    # concrète l'étend — le registre ne charge plus du tout.

    @api.model
    def get_dashboard_data(self):
        data = super().get_dashboard_data()
        data["cx"] = self._get_cx_summary()
        return data

    @api.model
    def _get_cx_summary(self):
        try:
            Feedback = self.env["bf.cx.feedback"]
            company = self.env.company
            summary = Feedback._nps_summary(
                [("company_id", "=", company.id)]
            )
            return {
                "nps_display": summary["display"],
                "nps_n": summary["n"],
                "nps_days": summary["days"],
                # "À rappeler" mirrors the closed loop exactly: NPS
                # detractors AND dissatisfied CSAT, not yet handled.
                "followup_todo": Feedback.search_count(
                    [
                        ("needs_followup", "=", True),
                        ("state", "!=", "done"),
                        ("company_id", "=", company.id),
                    ]
                ),
                "complaints_open": self.env["bf.cx.complaint"].search_count(
                    [
                        ("state", "not in", ("resolved", "closed")),
                        ("company_id", "=", company.id),
                    ]
                ),
                "testimonial_candidates": Feedback.search_count(
                    [
                        ("is_testimonial_candidate", "=", True),
                        ("testimonial_id", "=", False),
                        ("company_id", "=", company.id),
                    ]
                ),
            }
        except Exception:
            _logger.exception("Error loading CX summary")
            return None
