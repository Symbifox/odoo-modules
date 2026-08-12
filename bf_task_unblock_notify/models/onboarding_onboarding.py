# -*- coding: utf-8 -*-
from odoo import api, models


class OnboardingOnboarding(models.Model):
    _inherit = "onboarding.onboarding"

    @api.model
    def action_close_panel_bf_task_unblock_notify(self):
        self.action_close_panel("bf_task_unblock_notify.bf_onboarding_panel")
