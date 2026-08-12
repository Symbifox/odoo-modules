"""Panel close action and shared step-completion helper.

Every completion hook in this module goes through the same funnel:
sudo (a regular CX user has no write access to the onboarding progress
models) plus a broad try/except, so the onboarding panel can never
break or slow down the host flow it observes.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


def bf_cx_onb_complete(env, xmlid):
    """Mark an onboarding step as done without ever raising."""
    try:
        env["onboarding.onboarding.step"].sudo().bf_complete_step(xmlid)
    except Exception:  # noqa: BLE001 - the panel must never block the flow
        _logger.exception(
            "bf_cx_onboarding: step completion failed for %s", xmlid
        )


class OnboardingOnboarding(models.Model):
    _inherit = "onboarding.onboarding"

    @api.model
    def action_close_panel_bf_cx_onboarding(self):
        self.action_close_panel("bf_cx_onboarding.bf_onboarding_panel")
