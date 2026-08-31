from markupsafe import escape

from odoo import api, fields, models, _


class ProjectCredentialRotateWizard(models.TransientModel):
    """Assistant pour la rotation de mots de passe d'identifiants avec piste d'audit."""
    _name = 'project.credential.rotate.wizard'
    _description = 'Rotation de mot de passe'

    credential_id = fields.Many2one(
        'project.credential',
        string='Identifiant',
        required=True,
        readonly=True,
    )
    credential_name = fields.Char(
        related='credential_id.name',
        string="Nom de l'identifiant",
        readonly=True,
    )
    project_id = fields.Many2one(
        related='credential_id.project_id',
        string='Projet',
        readonly=True,
    )
    current_password = fields.Char(
        string='Mot de passe actuel',
        compute='_compute_current_password',
    )
    new_password = fields.Char(
        string='Nouveau mot de passe',
        required=True,
    )
    confirm_password = fields.Char(
        string='Confirmer le mot de passe',
        required=True,
    )
    rotation_reason = fields.Selection([
        ('scheduled', 'Rotation planifiée'),
        ('security', 'Incident de sécurité'),
        ('compromise', 'Compromission potentielle'),
        ('policy', 'Exigence de politique'),
        ('other', 'Autre'),
    ], string='Raison', required=True, default='scheduled')
    notes = fields.Text(
        string='Notes',
        help='Notes supplémentaires sur cette rotation de mot de passe',
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
            raise models.ValidationError(_('Les mots de passe ne correspondent pas.'))

        if not self.new_password:
            raise models.ValidationError(_('Le nouveau mot de passe ne peut pas être vide.'))

        credential = self.credential_id

        # Mettre à jour l'identifiant
        credential.write({
            'password': self.new_password,
            'last_rotated': fields.Datetime.now(),
            'state': 'active',  # Réactiver si expiré
        })

        # Journaliser la rotation dans le chatter
        reason_label = dict(self._fields['rotation_reason'].selection).get(
            self.rotation_reason, self.rotation_reason
        )
        body = _('Mot de passe changé.<br/><b>Raison :</b> %s') % reason_label
        if self.notes:
            body += _('<br/><b>Notes :</b> %s') % escape(self.notes)

        credential.message_post(
            body=body,
            message_type='notification',
        )

        return {'type': 'ir.actions.act_window_close'}
