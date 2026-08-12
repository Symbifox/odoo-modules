from odoo import api, fields, models


class ProjectProject(models.Model):
    """Extension de project.project pour l'intégration de la matrice de connaissances."""
    _inherit = 'project.project'

    knowledge_matrix_ids = fields.One2many(
        'project.knowledge.matrix',
        'project_id',
        string='Matrices de connaissances',
        help='Matrices de connaissances pour ce projet',
    )
    knowledge_matrix_count = fields.Integer(
        string='Nombre de matrices',
        compute='_compute_knowledge_matrix_stats',
    )
    knowledge_progress = fields.Float(
        string='Progression des connaissances',
        compute='_compute_knowledge_matrix_stats',
        help='Pourcentage moyen de complétion de toutes les matrices',
    )

    # Champs identifiants
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

    # Champs éléments de matrice
    knowledge_item_ids = fields.One2many(
        'project.knowledge.item',
        'project_id',
        string='Éléments de matrice',
        help='Éléments de la matrice de connaissances pour ce projet',
    )

    # Champs documents
    document_ids = fields.One2many(
        'project.document',
        'project_id',
        string='Documents',
        help='Documents associés à ce projet',
    )
    document_count = fields.Integer(
        string='Nombre de documents',
        compute='_compute_document_count',
    )

    @api.depends('knowledge_matrix_ids', 'knowledge_matrix_ids.progress')
    def _compute_knowledge_matrix_stats(self):
        for project in self:
            matrices = project.knowledge_matrix_ids.filtered(lambda m: m.active)
            project.knowledge_matrix_count = len(matrices)
            if matrices:
                project.knowledge_progress = sum(matrices.mapped('progress')) / len(matrices)
            else:
                project.knowledge_progress = 0.0

    @api.depends('credential_ids')
    def _compute_credential_count(self):
        for project in self:
            project.credential_count = len(project.credential_ids)

    @api.depends('document_ids')
    def _compute_document_count(self):
        for project in self:
            project.document_count = len(project.document_ids)

    def action_view_knowledge_matrix(self):
        """Ouvrir les matrices de connaissances pour ce projet."""
        self.ensure_one()
        action = {
            'type': 'ir.actions.act_window',
            'name': f'Matrices de connaissances - {self.name}',
            'res_model': 'project.knowledge.matrix',
            'views': [[False, 'list'], [False, 'form']],
            'domain': [('project_id', '=', self.id)],
            'context': {
                'default_project_id': self.id,
                'default_name': f'{self.name} - Matrice de connaissances',
            },
        }
        # Si une seule matrice, l'ouvrir directement
        if self.knowledge_matrix_count == 1:
            action['res_id'] = self.knowledge_matrix_ids.id
            action['views'] = [[False, 'form']]
        return action

    def action_create_knowledge_matrix(self):
        """Action rapide pour créer une nouvelle matrice de connaissances."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Créer une matrice de connaissances',
            'res_model': 'project.knowledge.matrix',
            'views': [[False, 'form']],
            'target': 'current',
            'context': {
                'default_project_id': self.id,
                'default_name': f'{self.name} - Matrice de connaissances',
            },
        }

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

    def action_view_documents(self):
        """Ouvrir les documents pour ce projet."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Documents - {self.name}',
            'res_model': 'project.document',
            'views': [[False, 'list'], [False, 'kanban'], [False, 'form']],
            'domain': [('project_id', '=', self.id)],
            'context': {
                'default_project_id': self.id,
            },
        }

    def action_view_raci_stakeholder(self):
        """Ouvrir le résumé RACI pour ce projet."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'RACI - {self.name}',
            'res_model': 'knowledge.raci.stakeholder',
            'views': [[False, 'pivot'], [False, 'list']],
            'domain': [('project_id', '=', self.id)],
            'context': {},
        }

    def action_view_knowledge_dashboard(self):
        """Ouvrir le tableau de bord filtré pour ce projet."""
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'name': f'Tableau de bord - {self.name}',
            'tag': 'knowledge_dashboard',
            'context': {
                'default_project_id': self.id,
                'default_project_name': self.name,
            },
        }
