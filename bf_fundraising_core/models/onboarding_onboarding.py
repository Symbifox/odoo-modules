# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import api, models


class OnboardingOnboarding(models.Model):
    _inherit = "onboarding.onboarding"

    @api.model
    def action_close_panel_bf_fundraising(self):
        self.action_close_panel("bf_fundraising_core.bf_onboarding_panel")
