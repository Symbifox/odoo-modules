"""Tableau de bord — les chiffres doivent dire la vérité, et la liste aussi.

Deux exigences distinctes. Un compteur doit compter ce qu'il annonce, et le
forage derrière ce compteur doit ramener exactement la population comptée. Un
tableau de bord dont le chiffre et la liste se contredisent est pire qu'un
tableau de bord absent : il fait croire qu'on a vérifié.
"""

from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import TransactionCase
from odoo.tools.safe_eval import datetime as safe_datetime, safe_eval


class DashboardCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tableau = cls.env['knowledge.dashboard']
        cls.aujourdhui = fields.Date.today()

    def _evaluer_domaine(self, action):
        """Évalue le domaine d'une action comme le ferait le client.

        Les domaines des actions de forage sont des chaînes contenant
        ``context_today()`` et ``relativedelta`` : c'est le navigateur qui les
        évalue en usage normal. Ici on refait la même chose côté serveur pour
        pouvoir EXÉCUTER le domaine, seule façon de prouver qu'il ramène bien la
        population que son compteur annonce.
        """
        domaine = action.domain
        if not isinstance(domaine, str):
            return domaine or []
        # safe_eval refuse un module nu dans son contexte : on passe le module
        # datetime déjà emballé par Odoo, comme le fait le serveur lui-même.
        return safe_eval(domaine, {
            'context_today': lambda: fields.Date.context_today(action),
            'relativedelta': relativedelta,
            'datetime': safe_datetime,
        })


class TestCredentialMetrics(DashboardCase):
    """Le défaut : « Expirant bientôt : 0, Expirés : 0 », quoi qu'il y ait en base.

    Les compteurs cherchaient des identifiants ``state = 'active'`` dont la date
    d'expiration était passée ou proche. Or c'est la tâche quotidienne qui fait
    SORTIR ces identifiants de l'état actif, vers ``expiring`` et ``expired`` :
    le tableau de bord contredisait la comptabilité du module.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.projet = cls.env['project.project'].create({'name': 'Projet tableau de bord'})
        cls.type_id = cls.env['project.credential.type'].create({
            'name': 'Type tableau', 'code': 'TEST-DASH',
        })

    def _identifiant(self, nom, **kwargs):
        valeurs = {
            'name': nom, 'project_id': self.projet.id, 'type_id': self.type_id.id,
        }
        valeurs.update(kwargs)
        return self.env['project.credential'].create(valeurs)

    def test_the_counters_see_what_the_daily_job_wrote(self):
        self._identifiant('Actif un')
        self._identifiant('Actif deux')
        self._identifiant('Expire bientôt',
                          expiration_date=self.aujourdhui + timedelta(days=10))
        self._identifiant('Expiré',
                          expiration_date=self.aujourdhui - timedelta(days=10))
        self._identifiant('Révoqué')

        # C'est la tâche planifiée qui déplace les statuts. On la joue.
        self.env['project.credential']._cron_check_expiring_credentials()
        self.env['project.credential'].search([
            ('name', '=', 'Révoqué')]).action_revoke()

        mesures = self.Tableau.get_credential_metrics()

        self.assertEqual(mesures['expiring_soon'], 1,
                         "L'identifiant qui expire dans 10 jours est invisible")
        self.assertEqual(mesures['expired'], 1,
                         "L'identifiant expiré depuis 10 jours est invisible")
        self.assertEqual(mesures['revoked'], 1)
        self.assertEqual(mesures['total'], 2, 'Seuls les actifs comptent dans le total')

    def test_the_four_counters_partition_the_whole_set(self):
        """Aucun identifiant compté deux fois, aucun oublié."""
        for i in range(3):
            self._identifiant(f'Actif {i}')
        self._identifiant('Bientôt', expiration_date=self.aujourdhui + timedelta(days=5))
        self._identifiant('Fini', expiration_date=self.aujourdhui - timedelta(days=5))
        self.env['project.credential']._cron_check_expiring_credentials()

        mesures = self.Tableau.get_credential_metrics()
        somme = (mesures['total'] + mesures['expiring_soon']
                 + mesures['expired'] + mesures['revoked'])
        self.assertEqual(somme, self.env['project.credential'].search_count([]))


class TestDrilldownsMirrorTheirCounter(DashboardCase):
    """Chaque chiffre doit ramener sa propre population.

    Le domaine d'une action de forage est écrit à la main, loin du compteur
    qu'il accompagne. Rien n'empêche les deux de diverger — sauf ce test, qui
    exécute le domaine et compare au chiffre.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.projet = cls.env['project.project'].create({'name': 'Projet forage'})
        cls.type_id = cls.env['project.credential.type'].create({
            'name': 'Type forage', 'code': 'TEST-FORAGE',
        })
        Credential = cls.env['project.credential']
        for nom, jours in (('Actif A', None), ('Actif B', None),
                           ('Bientôt', 12), ('Fini', -12)):
            valeurs = {'name': nom, 'project_id': cls.projet.id,
                       'type_id': cls.type_id.id}
            if jours is not None:
                valeurs['expiration_date'] = cls.aujourdhui + timedelta(days=jours)
            Credential.create(valeurs)
        Credential._cron_check_expiring_credentials()
        Credential.search([('name', '=', 'Actif B')]).action_revoke()

    def _compter_par_action(self, xmlid):
        action = self.env.ref(f'project_knowledge_matrix.{xmlid}')
        return self.env[action.res_model].search_count(self._evaluer_domaine(action))

    def test_each_credential_drilldown_returns_its_counter(self):
        mesures = self.Tableau.get_credential_metrics()
        for cle, xmlid in (
            ('total', 'report_action_cred_active'),
            ('expiring_soon', 'report_action_cred_expiring'),
            ('expired', 'report_action_cred_expired'),
        ):
            with self.subTest(compteur=cle):
                self.assertEqual(
                    self._compter_par_action(xmlid), mesures[cle],
                    f'Le forage « {xmlid} » ne ramène pas ce que « {cle} » annonce',
                )

    def test_the_document_drilldowns_still_target_document_states(self):
        """Garde-fou : les états d'un document ne sont pas ceux d'un identifiant.

        ``project.document`` ne connaît que draft / active / archived. Un domaine
        de forage documentaire qui chercherait « expired » ou « expiring » ne
        ramènerait jamais rien, en silence.
        """
        etats_documents = set(dict(
            self.env['project.document']._fields['state'].selection))
        for xmlid in ('report_action_docs_expired', 'report_action_docs_expiring_30d',
                      'report_action_docs_active', 'report_action_docs_review_overdue'):
            action = self.env.ref(f'project_knowledge_matrix.{xmlid}',
                                  raise_if_not_found=False)
            if not action:
                continue
            with self.subTest(action=xmlid):
                self.assertEqual(action.res_model, 'project.document')
                for feuille in self._evaluer_domaine(action):
                    if isinstance(feuille, (list, tuple)) and feuille[0] == 'state':
                        self.assertIn(
                            feuille[2], etats_documents,
                            f'{xmlid} filtre sur un état que project.document '
                            f'ne connaît pas : {feuille[2]!r}',
                        )


class TestAggregatedBlocks(DashboardCase):
    """Les blocs repliés en une requête doivent rendre les mêmes chiffres."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.type_interne = cls.env['project.document.type'].create({
            'name': 'Interne tableau', 'code': 'TEST-DASH-INT', 'is_internal': True,
        })
        cls.type_client = cls.env['project.document.type'].create({
            'name': 'Client tableau', 'code': 'TEST-DASH-CLI', 'is_internal': False,
        })
        Document = cls.env['project.document']
        cls.attendu = {
            ('active', True): 3, ('active', False): 2,
            ('draft', False): 1, ('archived', True): 1,
        }
        compteur = 0
        for (etat, interne), nombre in cls.attendu.items():
            for _ in range(nombre):
                compteur += 1
                Document.create({
                    'name': f'Doc tableau {compteur}',
                    'code': f'TEST-DASH-{compteur}',
                    'type_id': (cls.type_interne if interne else cls.type_client).id,
                    'state': etat,
                })

    def test_document_overview_counts_by_state_and_nature(self):
        apercu = self.Tableau.get_document_overview()
        Document = self.env['project.document']

        # Comparé à la vérité comptée autrement : un search_count par case.
        self.assertEqual(apercu['total'], Document.search_count([]))
        for cle, domaine in (
            ('active', [('state', '=', 'active')]),
            ('draft', [('state', '=', 'draft')]),
            ('archived', [('state', '=', 'archived')]),
            ('internal', [('state', '=', 'active'), ('is_internal', '=', True)]),
            ('client', [('state', '=', 'active'), ('is_internal', '=', False)]),
        ):
            with self.subTest(compteur=cle):
                self.assertEqual(apercu[cle], Document.search_count(domaine))

    def test_internal_and_client_add_up_to_active(self):
        apercu = self.Tableau.get_document_overview()
        self.assertEqual(apercu['internal'] + apercu['client'], apercu['active'])

    def test_an_empty_scope_returns_zeros_not_a_crash(self):
        """Un projet sans rien doit rendre des zéros, pas une erreur de clé."""
        projet_vide = self.env['project.project'].create({'name': 'Projet vide'})
        apercu = self.Tableau.get_document_overview(project_id=projet_vide.id)
        self.assertEqual(
            apercu,
            {'total': 0, 'active': 0, 'draft': 0, 'archived': 0,
             'internal': 0, 'client': 0},
        )

    def test_the_whole_dashboard_still_answers_every_block(self):
        """Distribution allumée, le tableau de bord rend ses onze blocs.

        Les trois blocs de distribution sont conditionnels depuis la 11.5.0 :
        le test les exige donc avec l'interrupteur mis, et
        ``TestDistributionSwitch`` couvre l'autre moitié.
        """
        porteur = self.env.ref('project_knowledge_matrix.group_document_user')
        porteur.write({'implied_ids': [(4, self.env.ref(
            'project_knowledge_matrix.group_document_distribution').id)]})

        donnees = self.Tableau.get_dashboard_data()
        for bloc in ('document_overview', 'review_metrics', 'client_metrics',
                     'internal_metrics', 'distribution_activity', 'content_quality',
                     'matrix_metrics', 'credential_metrics', 'decision_metrics',
                     'corporate_metrics'):
            with self.subTest(bloc=bloc):
                self.assertIn(bloc, donnees)
                self.assertIsNotNone(donnees[bloc])


class TestMatrixAndDecisionMetrics(DashboardCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.projet = cls.env['project.project'].create({'name': 'Projet mesures'})
        cls.section = cls.env['project.knowledge.section'].create({
            'name': 'Section mesures', 'code': 'TESTMES',
        })
        cls.matrice = cls.env['project.knowledge.matrix'].create({
            'name': 'Matrice mesures', 'project_id': cls.projet.id,
        })

    def _element(self, decision_id, **kwargs):
        valeurs = {
            'decision_id': decision_id, 'name': f'Élément {decision_id}',
            'matrix_id': self.matrice.id, 'section_id': self.section.id,
        }
        valeurs.update(kwargs)
        return self.env['project.knowledge.item'].create(valeurs)

    def test_matrix_metrics_counted_by_hand(self):
        """Sept éléments, dont un S/O hors du dénominateur."""
        self._element('M1', state='done')
        self._element('M2', state='accepted')
        self._element('M3', state='in_progress')
        self._element('M4', state='pending')
        self._element('M5', state='pending')
        self._element('M6', state='na')
        self._element('M7', state='pending',
                      deadline=self.aujourdhui - timedelta(days=3))

        mesures = self.Tableau.get_matrix_metrics(project_id=self.projet.id)

        self.assertEqual(mesures['total_items'], 6)
        self.assertEqual(mesures['completed_items'], 2)
        self.assertEqual(mesures['in_progress_items'], 1)
        self.assertEqual(mesures['overdue_items'], 1)
        self.assertAlmostEqual(mesures['completion_rate'], 33.3, places=1)

    def test_decision_metrics_counted_by_hand(self):
        self._element('D1', item_type='decision', state='accepted')
        self._element('D2', item_type='decision', state='proposed')
        self._element('D3', item_type='decision', state='rejected')
        self._element('D4', item_type='decision', state='pending',
                      impact_level='high')
        self._element('D5', item_type='decision', state='proposed',
                      impact_level='high')
        self._element('D6', item_type='decision', state='accepted',
                      impact_level='high')
        self._element('D7', item_type='info', state='pending')

        mesures = self.Tableau.get_decision_metrics(project_id=self.projet.id)

        self.assertEqual(mesures['total'], 6, "L'élément d'information ne compte pas")
        self.assertEqual(mesures['accepted'], 2)
        self.assertEqual(mesures['proposed'], 2)
        self.assertEqual(mesures['rejected'], 1)
        self.assertEqual(mesures['high_impact_pending'], 2,
                         'Seuls les impacts forts encore ouverts')
