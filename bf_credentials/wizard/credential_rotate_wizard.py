from markupsafe import Markup

from odoo import api, fields, models, _


class ProjectCredentialRotateWizard(models.TransientModel):
    """Assistant pour la rotation de mots de passe d'identifiants avec piste d'audit."""
    _name = 'project.credential.rotate.wizard'
    _description = "Password rotation"

    credential_id = fields.Many2one(
        'project.credential',
        string="Credential",
        required=True,
        readonly=True,
    )
    credential_name = fields.Char(
        related='credential_id.name',
        string="Credential name",
        readonly=True,
    )
    project_id = fields.Many2one(
        related='credential_id.project_id',
        string="Project",
        readonly=True,
    )
    current_password = fields.Char(
        string="Current password",
        compute='_compute_current_password',
    )
    new_password = fields.Char(
        string="New password",
        required=True,
    )
    confirm_password = fields.Char(
        string="Confirm password",
        required=True,
    )
    rotation_reason = fields.Selection([
        ('scheduled', "Scheduled rotation"),
        ('security', "Security incident"),
        ('compromise', "Potential compromise"),
        ('policy', "Policy requirement"),
        ('other', "Other"),
    ], string="Reason", required=True, default='scheduled')
    notes = fields.Text(
        string='Notes',
        help="Additional notes on this password rotation",
    )

    @api.depends('credential_id')
    def _compute_current_password(self):
        """Obtenir le mot de passe actuel pour référence."""
        for wizard in self:
            if wizard.credential_id:
                wizard.current_password = wizard.credential_id.password
            else:
                wizard.current_password = False

    def action_rotate(self):
        """Effectuer la rotation du mot de passe et journaliser le changement."""
        self.ensure_one()

        if self.new_password != self.confirm_password:
            raise models.ValidationError(self.env._("The passwords do not match."))

        if not self.new_password:
            raise models.ValidationError(self.env._("The new password cannot be empty."))

        credential = self.credential_id

        # Mettre à jour l'identifiant
        credential.write({
            'password': self.new_password,
            'last_rotated': fields.Datetime.now(),
            'state': 'active',  # Réactiver si expiré
        })

        # Journaliser la rotation dans le chatter
        # Libellé dans la langue de l'usager (`_description_selection`, pas la
        # source de `selection`), et corps en Markup : un `str` serait échappé
        # au fil et montrerait les balises. Le `%` échappe les valeurs.
        reason_label = dict(
            self._fields['rotation_reason']._description_selection(self.env)
        ).get(self.rotation_reason, self.rotation_reason)
        body = Markup(self.env._("Password changed.<br/><b>Reason:</b> %s")) % reason_label
        if self.notes:
            body += Markup(self.env._("<br/><b>Notes:</b> %s")) % self.notes

        credential.message_post(
            body=body,
            message_type='notification',
        )

        return {'type': 'ir.actions.act_window_close'}
