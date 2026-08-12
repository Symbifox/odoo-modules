# -*- coding: utf-8 -*-
import re

from odoo import api, models

_ACTION_DESCRIPTION_RE = re.compile(r"\[action:([\w.]+)\]")


class OnboardingOnboardingStep(models.Model):
    _inherit = "onboarding.onboarding.step"

    @api.model
    def bf_open_res_config_settings(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": "res.config.settings",
            "view_mode": "form",
            "target": "inline",
        }

    @api.model
    def bf_open_action(self):
        step = self.env.context.get("onboarding_step_xmlid")
        record = self.env.ref(step, raise_if_not_found=False) if step else None
        if record is None and len(self) == 1:
            record = self
        if record is None:
            return self.bf_open_res_config_settings()
        match = _ACTION_DESCRIPTION_RE.search(record.description or "")
        if not match:
            return self.bf_open_res_config_settings()
        action = self.env.ref(match.group(1), raise_if_not_found=False)
        if action is None:
            return self.bf_open_res_config_settings()
        return action.sudo().read()[0]

    @api.model
    def bf_complete_step(self, xmlid):
        return self.action_validate_step(xmlid)
