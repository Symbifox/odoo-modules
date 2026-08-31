from odoo import api, fields, models


class ProjectProject(models.Model):
    """Le coffre, vu depuis le projet.

    Ces trois-là vivaient dans ``project_knowledge_matrix`` jusqu'à sa
    18.0.12.0.0. Le ``One2many`` est TYPÉ vers ``project.credential`` : tant
    qu'il restait dans le socle, le socle dépendait durement du coffre, et
    l'extraction rendait la dépendance circulaire. Il déménage donc avec ce
    qu'il désigne.

    La migration 18.0.13.0.0 du socle réattribue son identifiant externe ; la
    colonne n'existe pas en base — un ``One2many`` se lit depuis l'autre bout.
    """

    _inherit = 'project.project'

    credential_ids = fields.One2many(
        'project.credential',
        'project_id',
        string='Identifiants',
        help='Identifiants stockés pour ce projet',
    )
    credential_count = fields.Integer(
        string="Nombre d'identifiants",
        compute='_compute_credential_count',
    )

    @api.depends('credential_ids')
    def _compute_credential_count(self):
        for project in self:
            project.credential_count = len(project.credential_ids)

    def action_view_credentials(self):
        """Ouvrir les identifiants pour ce projet."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Identifiants - {self.name}',
            'res_model': 'project.credential',
            'views': [[False, 'list'], [False, 'kanban'], [False, 'form']],
            'domain': [('project_id', '=', self.id)],
            'context': {
                'default_project_id': self.id,
                'search_default_filter_active': 1,
            },
        }
