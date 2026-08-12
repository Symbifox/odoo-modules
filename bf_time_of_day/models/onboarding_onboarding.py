# -*- coding: utf-8 -*-
from odoo import api, models


class OnboardingOnboarding(models.Model):
    _inherit = "onboarding.onboarding"

    @api.model
    def action_close_panel_bf_time_of_day(self):
        self.action_close_panel("bf_time_of_day.bf_onboarding_panel")
