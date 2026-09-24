from odoo import api, fields, models


class ProjectProject(models.Model):
    """Extension de project.project pour le smart button meetings."""
    _inherit = 'project.project'

    meeting_record_ids = fields.One2many(
        'meeting.record',
        'project_id',
        string='Comptes rendus',
    )
    meeting_count = fields.Integer(
        string='Nombre de comptes rendus',
        compute='_compute_meeting_count',
    )
    bf_skip_dashboard = fields.Boolean(
        string='Exclure du tableau de bord rencontres',
        help="Cocher pour masquer toutes les rencontres rattachées à ce "
             "projet du tableau de bord des rencontres (OdJ et CR).",
    )
    # ── Envoi automatique du compte rendu ────────────────────────────────
    # La permission vit ICI, sur le projet, et nulle part ailleurs : c'est une
    # liste blanche, jamais une exclusion. Un projet neuf arrive décoché, donc
    # l'état par défaut de tout le parc est « aucun envoi automatique » — le
    # jour où le déclencheur se trompe de compte rendu, la garde tient parce
    # qu'elle est en base et pas dans la consigne donnée à Gen.
    meeting_autosend = fields.Boolean(
        string='Envoi automatique du compte rendu',
        default=False,
        help="Après la revue Gen, envoyer le compte rendu aux destinataires "
             "sans intervention. À réserver aux projets dont TOUS les comptes "
             "rendus sont destinés à la même personne — une ligne d'appels "
             "dédiée, par exemple. Laisser décoché partout ailleurs.",
    )
    meeting_report_recipient_ids = fields.Many2many(
        'res.partner',
        'project_meeting_report_recipient_rel',
        'project_id',
        'partner_id',
        string='Destinataires par défaut du compte rendu',
        help="Recopiés dans « Destinataires » des comptes rendus du projet "
             "quand ce champ y est encore vide. Sans eux, l'envoi — manuel "
             "comme automatique — refuse de partir.",
    )

    @api.depends('meeting_record_ids')
    def _compute_meeting_count(self):
        for project in self:
            project.meeting_count = len(project.meeting_record_ids)

    def action_view_meetings(self):
        """Ouvrir les comptes rendus pour ce projet."""
        self.ensure_one()
        action = {
            'type': 'ir.actions.act_window',
            'name': f'Comptes rendus — {self.name}',
            'res_model': 'meeting.record',
            'views': [[False, 'list'], [False, 'form']],
            'domain': [('project_id', '=', self.id)],
            'context': {
                'default_project_id': self.id,
            },
        }
        if self.meeting_count == 1:
            action['res_id'] = self.meeting_record_ids.id
            action['views'] = [[False, 'form']]
        return action
