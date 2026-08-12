# -*- coding: utf-8 -*-
from odoo import api, models


class OnboardingOnboarding(models.Model):
    _inherit = "onboarding.onboarding"

    @api.model
    def action_close_panel_bluefox_branding(self):
        self.action_close_panel("bluefox_branding.bf_onboarding_panel")
