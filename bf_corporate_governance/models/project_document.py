from odoo import api, fields, models


class ProjectDocument(models.Model):
    """Ce que la gouvernance corporative ajoute aux documents.

    Trois choses, toutes venues de ``project_knowledge_matrix`` 18.0.11.5.0 :
    la section du livre des minutes, les cinq chiffres corporatifs du rapport
    bimensuel, et les cinq actions de forage qui les rendent cliquables.

    Sans ce module, le rapport bimensuel ne porte simplement pas ces clés. Le
    gabarit du courriel les lit toutes en ``ctx.get(..., 0) > 0`` : une clé
    absente éteint son bloc, sans qu'il faille toucher au gabarit.
    """

    _inherit = 'project.document'

    # Le champ classait déjà les documents du livre des minutes, mais il vivait
    # dans le socle documentaire, où il ne veut rien dire : aucune vue ne le
    # porte, et la seule chose qui le lit est l'action « Livre des minutes » de
    # ce module. Il déménage donc avec elle. La migration 18.0.12.0.0 du socle
    # réattribue son identifiant externe : la colonne n'est pas recréée.
    minute_book_section = fields.Selection(
        selection=[
            ('charter', 'Statuts constitutifs'),
            ('bylaws', 'Reglements'),
            ('agreements', "Conventions d'actionnaires"),
            ('director_minutes', 'Proces-verbaux des administrateurs'),
            ('shareholder_minutes', 'Proces-verbaux des actionnaires'),
            ('forms_filed', 'Formulaires deposes'),
            ('financial_statements', 'Etats financiers'),
        ],
        string='Section du livre des minutes',
    )

    @api.model
    def _get_report_link_actions(self):
        actions = super()._get_report_link_actions()
        actions.update({
            'corp_active_directors':
                'bf_corporate_governance.report_action_corp_directors',
            'corp_active_officers':
                'bf_corporate_governance.report_action_corp_officers',
            'corp_adopted_resolutions':
                'bf_corporate_governance.report_action_corp_resolutions_adopted',
            'corp_overdue_compliance':
                'bf_corporate_governance.report_action_corp_compliance_overdue',
            'corp_due_soon_compliance':
                'bf_corporate_governance.report_action_corp_compliance_due_soon',
        })
        return actions

    @api.model
    def _get_dashboard_report_data(self):
        data = super()._get_dashboard_report_data()
        Director = self.env['corporate.director']
        Officer = self.env['corporate.officer']
        Compliance = self.env['corporate.compliance.event']
        Resolution = self.env['corporate.resolution']
        data.update({
            'corp_active_directors': Director.search_count([
                ('is_active', '=', True),
            ]),
            'corp_active_officers': Officer.search_count([
                ('is_active', '=', True),
            ]),
            'corp_overdue_compliance': Compliance.search_count([
                ('completed_date', '=', False),
                ('status', '=', 'overdue'),
            ]),
            'corp_due_soon_compliance': Compliance.search_count([
                ('completed_date', '=', False),
                ('status', '=', 'due_soon'),
            ]),
            'corp_adopted_resolutions': Resolution.search_count([
                ('status', '=', 'adopted'),
            ]),
        })
        return data
