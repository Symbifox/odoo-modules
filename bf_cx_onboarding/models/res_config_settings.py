"""Complete onboarding steps when the CX cadence settings are saved.

Compares the relevant ir.config_parameter values around super() (the
same before/after technique bf_cx itself uses for the acknowledgement
delay): a save that changes the anti-oversolicitation cadence checks
off the cadence step, a save that changes the complaint acknowledgement
delay checks off the complaints-team step. No settings field is added
here: the module observes, it does not configure.
"""
import logging

from odoo import models

from .onboarding_onboarding import bf_cx_onb_complete

_logger = logging.getLogger(__name__)

STEP_CADENCE = "bf_cx_onboarding.bf_onb_step_cadence"
STEP_COMPLAINT_TEAM = "bf_cx_onboarding.bf_onb_step_complaint_team"

PARAM_CADENCE = "bf_cx.solicitation_cooldown_days"
PARAM_ACK = "bf_cx.complaint_ack_days"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    def set_values(self):
        icp = self.env["ir.config_parameter"].sudo()
        before_cadence = icp.get_param(PARAM_CADENCE, None)
        before_ack = icp.get_param(PARAM_ACK, None)
        res = super().set_values()
        try:
            after_cadence = icp.get_param(PARAM_CADENCE, None)
            after_ack = icp.get_param(PARAM_ACK, None)
            if after_cadence not in (None, "") and before_cadence != after_cadence:
                bf_cx_onb_complete(self.env, STEP_CADENCE)
            if after_ack not in (None, "") and before_ack != after_ack:
                bf_cx_onb_complete(self.env, STEP_COMPLAINT_TEAM)
        except Exception:  # noqa: BLE001 - never block saving the settings
            _logger.exception("bf_cx_onboarding: settings hook failed")
        return res
