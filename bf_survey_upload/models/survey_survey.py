from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


class SurveySurvey(models.Model):
    _inherit = "survey.survey"

    bf_link_uploads_to_project = fields.Boolean(
        string="Copier les téléversements sur un projet",
        default=False,
        help=(
            "Lors de la complétion du sondage, copie les pièces jointes "
            "téléversées vers le projet ciblé ci-dessous."
        ),
    )
    bf_target_project_id = fields.Many2one(
        "project.project",
        string="Projet cible pour les téléversements",
        help=(
            "Projet vers lequel copier les pièces jointes téléversées via ce "
            "sondage. Requis si « Copier les téléversements sur un projet » "
            "est activé. Définir explicitement évite que des fichiers "
            "atterrissent sur le mauvais projet."
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
                        "Vous n'avez pas accès au projet ciblé. "
                        "Choisissez un projet auquel vous avez accès en lecture."
                    )
                ) from e
