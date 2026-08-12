"""Complete the complaints-team step when a first complaint is logged.

A complaint actually registered in the system is the proof that the
complaints channel is staffed and operating.
"""
import logging

from odoo import api, models

from .onboarding_onboarding import bf_cx_onb_complete

_logger = logging.getLogger(__name__)

STEP_COMPLAINT_TEAM = "bf_cx_onboarding.bf_onb_step_complaint_team"


class BfCxComplaint(models.Model):
    _inherit = "bf.cx.complaint"

    @api.model_create_multi
    def create(self, vals_list):
        complaints = super().create(vals_list)
        try:
            bf_cx_onb_complete(self.env, STEP_COMPLAINT_TEAM)
        except Exception:  # noqa: BLE001 - never block complaint intake
            _logger.exception("bf_cx_onboarding: complaint hook failed")
        return complaints
