from odoo import api, models


class KnowledgeDashboard(models.AbstractModel):
    """Le bloc « Identifiants » du tableau de bord des connaissances.

    Il vivait dans ``project_knowledge_matrix`` jusqu'à sa 18.0.12.0.0, où le
    socle interrogeait ``project.credential`` par son nom — une dépendance à
    l'envers. Elle est retournée ici.

    ``_get_project_domain`` du socle ne connaît plus ``project.credential`` :
    sans la surcharge ci-dessous, il rendrait une liste vide et le bloc
    compterait TOUT le parc sous un filtre de projet, ce qui se lirait comme
    un compte du projet.
    """

    _inherit = 'knowledge.dashboard'

    @api.model
    def _get_project_domain(self, project_id, model_name):
        if project_id and model_name == 'project.credential':
            return [('project_id', '=', project_id)]
        return super()._get_project_domain(project_id, model_name)

    @api.model
    def get_credential_metrics(self, project_id=False):
        """Répartition des identifiants par statut.

        Les deux compteurs « expirant » et « expiré » annonçaient zéro en
        permanence, quelles que soient les dates en base. Ils cherchaient des
        identifiants ``state = 'active'`` dont la date d'expiration était passée
        ou proche — or c'est précisément la tâche planifiée quotidienne qui fait
        SORTIR ces identifiants de l'état actif, vers ``expiring`` et ``expired``.

        Le statut est la comptabilité du module : on le lit tel quel.
        """
        pd = self._get_project_domain(project_id, 'project.credential')
        if not project_id:
            # Vue parc : les identifiants fictifs des projets de démonstration
            # n'ont rien à faire dans les totaux. Ils continuent de compter
            # quand on ouvre LEUR projet — la démonstration doit pouvoir montrer
            # ses identifiants expirés, c'est ce qui prouve que le suivi
            # d'expiration fonctionne.
            pd = pd + self.env['project.project']._demo_exclusion_domain()
        comptes = self._grouper_comptes('project.credential', pd, ['state'])

        # Le deuxième facteur se compte à part du cycle de vie : un identifiant
        # actif dont personne ne peut produire le code est un problème que la
        # colonne « actif » ne montre pas.
        #
        # ⚠️ Les révoqués sortent des deux chiffres. Un identifiant révoqué n'a
        # plus de deuxième facteur à documenter, et le laisser dedans ferait
        # grossir une liste de travail que personne ne peut vider.
        mfa = self._grouper_comptes(
            'project.credential',
            pd + [('state', '!=', 'revoked')],
            ['mfa_state'],
        )

        return {
            'total': comptes.get('active', 0),
            'expiring_soon': comptes.get('expiring', 0),
            'expired': comptes.get('expired', 0),
            'revoked': comptes.get('revoked', 0),
            'mfa_unknown': mfa.get('unknown', 0),
            'mfa_at_risk': mfa.get('at_risk', 0),
        }

    @api.model
    def get_dashboard_data(self, project_id=False):
        result = super().get_dashboard_data(project_id=project_id)
        result['credential_metrics'] = self.get_credential_metrics(
            project_id=project_id)
        return result
