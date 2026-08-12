"""Complete the first-wave step when a wave is actually sent.

The hook sits on write() rather than on action_send(): the state flip
to 'sent' is the single fact shared by the manual button, the deferred
retry of the daily cron, and any import path.
"""
import logging

from odoo import api, models

from .onboarding_onboarding import bf_cx_onb_complete

_logger = logging.getLogger(__name__)

STEP_WAVE = "bf_cx_onboarding.bf_onb_step_wave"


class BfCxWave(models.Model):
    _inherit = "bf.cx.wave"

    @api.model_create_multi
    def create(self, vals_list):
        waves = super().create(vals_list)
        try:
            if any(w.state == "sent" for w in waves):
                bf_cx_onb_complete(self.env, STEP_WAVE)
        except Exception:  # noqa: BLE001 - never block wave creation
            _logger.exception("bf_cx_onboarding: wave create hook failed")
        return waves

    def write(self, vals):
        res = super().write(vals)
        try:
            if vals.get("state") == "sent":
                bf_cx_onb_complete(self.env, STEP_WAVE)
        except Exception:  # noqa: BLE001 - never block the send flow
            _logger.exception("bf_cx_onboarding: wave write hook failed")
        return res
