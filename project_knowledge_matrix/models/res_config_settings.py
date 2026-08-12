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
