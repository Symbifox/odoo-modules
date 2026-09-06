"""Revenue-at-risk indicator on the CX dashboard tile.

Same defensive pattern as bf_cx_dashboard: one broken section must never
break the whole dashboard, so this bridge wraps its own computation in a
try/except and only adds a plain float to the summary returned by
_get_cx_summary(). "At risk" means: an active subscription managed for a
client (managed_for_id) whose partner currently has an unhandled feedback
to follow up (needs_followup and not done) or an open complaint. Amounts
are the stored monthly_equivalent of bf_subscription, summed the same
naive way as the subscription dashboard tile.
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
    def _get_cx_summary(self):
        res = super()._get_cx_summary()
        if not res:
            return res
        res["revenue_at_risk"] = 0.0
        try:
            res["revenue_at_risk"] = self._get_cx_revenue_at_risk()
        except Exception:
            _logger.exception("Error computing CX revenue at risk")
        return res

    @api.model
    def _get_cx_revenue_at_risk(self):
        """Sum the monthly equivalent of active managed subscriptions
        whose client has a feedback to follow up or an open complaint.

        Returns 0.0 when nothing matches.
        """
        company = self.env.company
        partners = self.env["bf.cx.feedback"].search([
            ("needs_followup", "=", True),
            ("state", "!=", "done"),
            ("partner_id", "!=", False),
            ("company_id", "=", company.id),
        ]).mapped("partner_id")
        partners |= self.env["bf.cx.complaint"].search([
            ("state", "not in", ("resolved", "closed")),
            ("partner_id", "!=", False),
            ("company_id", "=", company.id),
        ]).mapped("partner_id")
        if not partners:
            return 0.0
        # Feedback often sits on a contact person while the managed
        # subscription points to the parent company: cover both levels.
        at_risk = partners | partners.mapped("commercial_partner_id")
        subscriptions = self.env["subscription.subscription"].search([
            ("state", "=", "active"),
            ("managed_for_id", "in", at_risk.ids),
            ("company_id", "=", company.id),
        ])
        return round(sum(subscriptions.mapped("monthly_equivalent")), 2)
