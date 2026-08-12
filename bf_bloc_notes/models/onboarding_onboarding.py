# -*- coding: utf-8 -*-
from odoo import api, models


class OnboardingOnboarding(models.Model):
    _inherit = "onboarding.onboarding"

    @api.model
    def action_close_panel_bf_bloc_notes(self):
        self.action_close_panel("bf_bloc_notes.bf_onboarding_panel")
