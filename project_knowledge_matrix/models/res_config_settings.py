from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # Dashboard report settings
    dashboard_report_recipients = fields.Char(
        string='Destinataires du rapport',
        config_parameter='project_knowledge_matrix.dashboard_report_recipients',
        help='Adresses courriel séparées par des virgules pour le rapport du tableau de bord',
    )
    dashboard_report_enabled = fields.Boolean(
        string='Rapport automatique activé',
        config_parameter='project_knowledge_matrix.dashboard_report_enabled',
        default=True,
        help='Envoyer le rapport du tableau de bord automatiquement toutes les 2 semaines',
    )

    # Interrupteur du sous-système de distribution.
    #
    # L'état vit dans les groupes, et nulle part ailleurs. Un paramètre système
    # en parallèle aurait fait deux sources pour une même question, et c'est
    # celle des groupes qui décide de l'affichage des menus : elle aurait gagné
    # en silence chaque fois que les deux auraient divergé.
    group_document_distribution = fields.Boolean(
        string='Distribution et accusés de réception',
        group='project_knowledge_matrix.group_document_user',
        implied_group='project_knowledge_matrix.group_document_distribution',
        help="Distribuer les documents à des clients ou à des employés et suivre "
             "leurs accusés de réception. Éteint, le module garde les "
             "distributions existantes et leurs accusés : il cesse simplement de "
             "les afficher, de créer leurs activités et de les compter dans le "
             "tableau de bord et le rapport bimensuel.",
    )

    def action_send_dashboard_report_now(self):
        """Manually trigger the dashboard report email."""
        self.env['project.document']._send_dashboard_report()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Rapport envoyé',
                'message': 'Le rapport du tableau de bord a été envoyé aux destinataires configurés.',
                'type': 'success',
                'sticky': False,
            }
        }
