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
