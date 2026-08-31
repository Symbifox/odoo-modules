from odoo import api, models


class ProjectDocument(models.Model):
    """Les trois chiffres d'identifiants du rapport bimensuel, et leurs liens.

    Le gabarit du courriel les lit en ``ctx.get(..., 0)`` : sans ce module,
    les clés sont simplement absentes et les lignes valent zéro, sans qu'il
    faille toucher au gabarit.
    """

    _inherit = 'project.document'

    @api.model
    def _get_report_link_actions(self):
        actions = super()._get_report_link_actions()
        actions.update({
            'credentials_total': 'bf_credentials.report_action_cred_active',
            'credentials_expiring': 'bf_credentials.report_action_cred_expiring',
            'credentials_expired': 'bf_credentials.report_action_cred_expired',
        })
        return actions

    @api.model
    def _get_dashboard_report_data(self):
        data = super()._get_dashboard_report_data()
        # Lire le STATUT, pas la date : c'est la tâche quotidienne qui fait
        # sortir un identifiant de l'état actif. Chercher « actif ET expiré »
        # rendait zéro en permanence.
        # Le rapport parle du parc : il écarte les projets de démonstration,
        # comme le bloc du tableau de bord. Les deux surfaces doivent donner le
        # même chiffre, sans quoi le courriel réclamerait une régularisation
        # que le tableau de bord dit déjà faite.
        domaine = self.env['project.project']._demo_exclusion_domain()
        comptes = {
            etat: nombre
            for etat, nombre in self.env['project.credential']._read_group(
                domaine, groupby=['state'], aggregates=['__count'])
        }
        data.update({
            'credentials_total': comptes.get('active', 0),
            'credentials_expiring': comptes.get('expiring', 0),
            'credentials_expired': comptes.get('expired', 0),
        })
        return data
