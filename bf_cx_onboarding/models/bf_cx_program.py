"""Complete onboarding steps when measurement programs take shape.

Creating a program checks off the first step. Giving a program its own
minimum cadence (cooldown_days) also counts as configuring the cadence:
that is the per-program half of the anti-oversolicitation setting.
"""
import logging

from odoo import api, models

from .onboarding_onboarding import bf_cx_onb_complete

_logger = logging.getLogger(__name__)

STEP_PROGRAM = "bf_cx_onboarding.bf_onb_step_program"
STEP_CADENCE = "bf_cx_onboarding.bf_onb_step_cadence"


class BfCxProgram(models.Model):
    _inherit = "bf.cx.program"

    @api.model_create_multi
    def create(self, vals_list):
        programs = super().create(vals_list)
        try:
            bf_cx_onb_complete(self.env, STEP_PROGRAM)
            if any(p.cooldown_days for p in programs):
                bf_cx_onb_complete(self.env, STEP_CADENCE)
        except Exception:  # noqa: BLE001 - never block program creation
            _logger.exception("bf_cx_onboarding: program create hook failed")
        return programs

    def write(self, vals):
        res = super().write(vals)
        try:
            if vals.get("cooldown_days"):
                bf_cx_onb_complete(self.env, STEP_CADENCE)
        except Exception:  # noqa: BLE001 - never block program updates
            _logger.exception("bf_cx_onboarding: program write hook failed")
        return res
