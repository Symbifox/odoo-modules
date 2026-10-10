from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


class SurveySurvey(models.Model):
    _inherit = "survey.survey"

    bf_link_uploads_to_project = fields.Boolean(
        string="Copy uploads to a project",
        default=False,
        help=(
            "When the survey is completed, copies the uploaded "
            "attachments to the target project below."
        ),
    )
    bf_target_project_id = fields.Many2one(
        "project.project",
        string="Target project for uploads",
        help=(
            "Project to copy the attachments uploaded through this survey "
            "to. Required if \"Copy uploads to a project\" is enabled. "
            "Setting it explicitly keeps files from landing on the wrong "
            "project."
        ),
    )

    @api.constrains("bf_target_project_id", "bf_link_uploads_to_project")
    def _check_bf_target_project_access(self):
        # Ensure the user setting bf_target_project_id has read access on the
        # project under their own ACLs (without sudo). Prevents an internal
        # user from routing public uploads into a project they cannot read,
        # which would otherwise succeed because the runtime hook uses sudo().
        # Skip on superuser writes (cron, migrations, automated propagation)
        # which legitimately bypass record rules.
        if self.env.su:
            return
        for survey in self:
            project = survey.bf_target_project_id
            if not project:
                continue
            try:
                project.check_access_rights("read")
                project.check_access_rule("read")
            except AccessError as e:
                raise ValidationError(
                    _(
                        "You do not have access to the target project. "
                        "Choose a project you can read."
                    )
                ) from e
