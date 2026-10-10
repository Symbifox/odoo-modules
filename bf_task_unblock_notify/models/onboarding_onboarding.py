# -*- coding: utf-8 -*-
from odoo import api, models


class OnboardingOnboarding(models.Model):
    _inherit = "onboarding.onboarding"

    @api.model
    def action_close_panel_bf_task_unblock_notify(self):
        self.action_close_panel("bf_task_unblock_notify.bf_onboarding_panel")


class OnboardingOnboardingStep(models.Model):
    _inherit = "onboarding.onboarding.step"

    @api.model
    def action_open_bf_task_unblock_settings(self):
        """The Project settings, where the module's section lives.

        The panel used to open the general Settings page, where this module had
        nothing to set.
        """
        return self.env["ir.actions.act_window"]._for_xml_id(
            "project.project_config_settings_action")
