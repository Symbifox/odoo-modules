from odoo import _, fields, models


class SurveyUserInput(models.Model):
    _inherit = "survey.user_input"

    def _mark_done(self):
        """Hook into survey completion to auto-advance the linked project's
        onboarding state from intake_pending → intake_completed."""
        res = super()._mark_done()
        Project = self.env["project.project"]
        for response in self:
            projects = Project.search([("intake_survey_response_id", "=", response.id)])
            for project in projects:
                if project.onboarding_state == "intake_pending":
                    project.intake_completed_date = fields.Date.context_today(project)
                    project._set_state(
                        "intake_completed",
                        body=_(
                            "Intake reçue (réponse survey #%s) — état avancé "
                            "automatiquement."
                        ) % response.id,
                    )
        return res
