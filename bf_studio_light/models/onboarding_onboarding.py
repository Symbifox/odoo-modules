# -*- coding: utf-8 -*-
from odoo import api, models


class OnboardingOnboarding(models.Model):
    _inherit = "onboarding.onboarding"

    @api.model
    def action_close_panel_bf_studio_light(self):
        self.action_close_panel("bf_studio_light.bf_onboarding_panel")
