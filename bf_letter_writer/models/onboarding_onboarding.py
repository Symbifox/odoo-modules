from odoo import api, models


class OnboardingOnboarding(models.Model):
    _inherit = "onboarding.onboarding"

    @api.model
    def action_close_panel_bf_letter_writer(self):
        self.action_close_panel("bf_letter_writer.bf_onboarding_panel")
