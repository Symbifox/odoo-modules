from odoo import api, models


class KnowledgeDashboard(models.AbstractModel):
    """Le bloc de gouvernance du tableau de bord des connaissances.

    Il vivait dans ``project_knowledge_matrix`` jusqu'à sa 18.0.11.5.0. Le
    tableau de bord y interrogeait quatre modèles corporatifs par leur nom :
    une dépendance à l'envers, du socle vers le sous-système. Elle est
    retournée ici — le socle ne connaît plus la gouvernance, c'est la
    gouvernance qui vient s'ajouter au tableau de bord.

    La clé ``corporate_metrics`` reste ABSENTE plutôt que rendue à zéro quand
    un projet est sélectionné, parce que ces chiffres ne sont pas
    projet-spécifiques. Même convention qu'au une clé absente dit
    « on ne compte pas », un zéro dirait « il n'y a rien ». Le gabarit s'en
    sert comme condition d'affichage.
    """

    _inherit = 'knowledge.dashboard'

    @api.model
    def get_corporate_metrics(self):
        """Indicateurs de gouvernance corporative — jamais filtrés par projet."""
        Director = self.env['corporate.director']
        Officer = self.env['corporate.officer']
        Compliance = self.env['corporate.compliance.event']
        Resolution = self.env['corporate.resolution']

        active_directors = Director.search_count([('is_active', '=', True)])
        active_officers = Officer.search_count([('is_active', '=', True)])

        overdue_compliance = Compliance.search_count([
            ('completed_date', '=', False),
            ('status', '=', 'overdue'),
        ])
        due_soon_compliance = Compliance.search_count([
            ('completed_date', '=', False),
            ('status', '=', 'due_soon'),
        ])
        upcoming_compliance = Compliance.search_count([
            ('completed_date', '=', False),
            ('status', '=', 'upcoming'),
        ])

        recent_resolutions = Resolution.search_count([
            ('status', '=', 'adopted'),
        ])

        return {
            'active_directors': active_directors,
            'active_officers': active_officers,
            'overdue_compliance': overdue_compliance,
            'due_soon_compliance': due_soon_compliance,
            'upcoming_compliance': upcoming_compliance,
            'adopted_resolutions': recent_resolutions,
        }

    @api.model
    def get_dashboard_data(self, project_id=False):
        result = super().get_dashboard_data(project_id=project_id)
        # Les chiffres corporatifs ne sont pas projet-spécifiques : les rendre
        # sous un filtre de projet donnerait les mêmes valeurs pour tous les
        # projets, ce qui se lirait comme un compte du projet.
        if not project_id:
            result['corporate_metrics'] = self.get_corporate_metrics()
        return result
