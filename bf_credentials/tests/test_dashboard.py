"""Le bloc « Identifiants » du tableau de bord, et ses trois forages.

Deux exigences distinctes. Un compteur doit compter ce qu'il annonce, et le
forage derrière ce compteur doit ramener exactement la population comptée. Un
tableau de bord dont le chiffre et la liste se contredisent est pire qu'un
tableau de bord absent : il fait croire qu'on a vérifié.

Écrit dans ``project_knowledge_matrix``, déplacé ici au
avec ce qu'il éprouve. Les compteurs sont mesurés en ÉCART plutôt
qu'en valeur absolue : la version d'origine posait « expirant = 1 » et échouait
sur toute base qui portait déjà un identifiant expirant — c'est-à-dire sur
toute copie de la production, là où l'on éprouve justement les déménagements.
"""

from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import TransactionCase
from odoo.tools.safe_eval import datetime as safe_datetime, safe_eval

MODULE = 'bf_credentials'


class CredentialDashboardCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tableau = cls.env['knowledge.dashboard']
        cls.Credential = cls.env['project.credential']
        cls.aujourdhui = fields.Date.today()
        # Hors démonstration : les compteurs du parc écartent ces projets, et
        # tous les écarts mesurés ici vaudraient zéro sur une copie de la
        # production, où la démonstration existe.
        cls.projet = cls.env['project.project'].search(
            [('knowledge_is_demo', '=', False)], limit=1)
        if not cls.projet:
            cls.projet = cls.env['project.project'].create(
                {'name': 'Projet tableau de bord'})
        cls.type_id = cls.env['project.credential.type'].create({
            'name': 'Type tableau', 'code': 'TEST-DASH',
        })

    def _identifiant(self, nom, **kwargs):
        valeurs = {
            'name': nom, 'project_id': self.projet.id, 'type_id': self.type_id.id,
        }
        valeurs.update(kwargs)
        return self.Credential.create(valeurs)

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
        return safe_eval(domaine, {
            'context_today': lambda: fields.Date.context_today(action),
            'relativedelta': relativedelta,
            'datetime': safe_datetime,
        })


class TestCredentialMetrics(CredentialDashboardCase):
    """Le défaut : « Expirant bientôt : 0, Expirés : 0 », quoi qu'il y ait en base.

    Les compteurs cherchaient des identifiants ``state = 'active'`` dont la date
    d'expiration était passée ou proche. Or c'est la tâche quotidienne qui fait
    SORTIR ces identifiants de l'état actif, vers ``expiring`` et ``expired`` :
    le tableau de bord contredisait la comptabilité du module.
    """

    def test_the_counters_see_what_the_daily_job_wrote(self):
        avant = self.Tableau.get_credential_metrics()

        self._identifiant('Actif un')
        self._identifiant('Actif deux')
        self._identifiant('Expire bientôt',
                          expiration_date=self.aujourdhui + timedelta(days=10))
        self._identifiant('Expiré',
                          expiration_date=self.aujourdhui - timedelta(days=10))
        revoque = self._identifiant('Révoqué')

        # C'est la tâche planifiée qui déplace les statuts. On la joue.
        self.Credential._cron_check_expiring_credentials()
        revoque.action_revoke()

        apres = self.Tableau.get_credential_metrics()
        ecart = {cle: apres[cle] - avant[cle] for cle in avant}

        self.assertEqual(ecart['expiring_soon'], 1,
                         "L'identifiant qui expire dans 10 jours est invisible")
        self.assertEqual(ecart['expired'], 1,
                         "L'identifiant expiré depuis 10 jours est invisible")
        self.assertEqual(ecart['revoked'], 1)
        self.assertEqual(ecart['total'], 2,
                         'Seuls les actifs comptent dans le total')

    def test_the_four_counters_partition_the_whole_set(self):
        """Aucun identifiant compté deux fois, aucun oublié.

        Celui-ci se lit bien en absolu : il compare la somme des quatre
        compteurs au parc entier, quel que soit ce parc.
        """
        for i in range(3):
            self._identifiant(f'Actif {i}')
        self._identifiant('Bientôt', expiration_date=self.aujourdhui + timedelta(days=5))
        self._identifiant('Fini', expiration_date=self.aujourdhui - timedelta(days=5))
        self.Credential._cron_check_expiring_credentials()

        mesures = self.Tableau.get_credential_metrics()
        somme = (mesures['total'] + mesures['expiring_soon']
                 + mesures['expired'] + mesures['revoked'])
        # Le parc, au sens du bloc : les projets de démonstration n'en sont pas.
        parc = self.env['project.project']._demo_exclusion_domain()
        self.assertEqual(somme, self.Credential.search_count(parc))

    def test_the_block_is_filtered_by_project(self):
        """Le socle ne connaît plus ``project.credential``.

        ``_get_project_domain`` est surchargé par ce module. Sans la surcharge,
        le socle rend une liste vide pour ce modèle et le bloc compte TOUT le
        parc sous un filtre de projet — ce qui se lit comme un compte du projet.

        Le contrôle compare le compteur filtré au décompte direct du même
        projet. Il n'est mordant que si le projet ne détient pas tout le parc,
        d'où l'assertion préalable qui le dit.
        """
        self._identifiant('Actif du projet')
        Credential = self.Credential
        du_projet = Credential.search_count([
            ('project_id', '=', self.projet.id), ('state', '=', 'active'),
        ])
        partout = Credential.search_count([('state', '=', 'active')])
        self.assertTrue(du_projet, "Le projet d'essai ne porte aucun identifiant actif")

        mesures = self.Tableau.get_credential_metrics(project_id=self.projet.id)
        self.assertEqual(mesures['total'], du_projet)
        if partout == du_projet:
            self.skipTest(
                'Tout le parc appartient au projet filtré : le contrôle passe '
                'mais ne distingue rien.'
            )


class TestCredentialDrilldowns(CredentialDashboardCase):
    """Chaque chiffre doit ramener sa propre population.

    Le domaine d'une action de forage est écrit à la main, loin du compteur
    qu'il accompagne. Rien n'empêche les deux de diverger — sauf ce test, qui
    exécute le domaine et compare au chiffre.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for nom, jours in (('Actif A', None), ('Actif B', None),
                           ('Bientôt', 12), ('Fini', -12)):
            valeurs = {'name': nom, 'project_id': cls.projet.id,
                       'type_id': cls.type_id.id}
            if jours is not None:
                valeurs['expiration_date'] = cls.aujourdhui + timedelta(days=jours)
            cls.Credential.create(valeurs)
        cls.Credential._cron_check_expiring_credentials()
        cls.Credential.search([('name', '=', 'Actif B')]).action_revoke()

    def _compter_par_action(self, xmlid):
        action = self.env.ref(f'{MODULE}.{xmlid}')
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


class TestDemoProjectsAreOutOfTheCount(CredentialDashboardCase):
    """Les identifiants d'une démonstration ne pèsent pas sur les totaux du parc.

    La démonstration du coffre est montée dans la production Blue Fox, avec des
    identifiants fictifs qui portent de vraies dates. Ces dates finissent par
    passer, la tâche quotidienne les marque « expiré », et le tableau de bord
    réclame une régularisation qui n'a pas d'objet — trois fois, sur des
    identifiants qui n'existent pas.

    Le drapeau les sort des totaux SANS les sortir de la démonstration : ouvrir
    le projet doit encore montrer ses identifiants expirés, puisque c'est
    précisément ce que la démonstration a à démontrer.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.demo = cls.env['project.project'].create({
            'name': 'Démonstration du coffre', 'knowledge_is_demo': True,
        })

    def _identifiant_demo(self, nom, **kwargs):
        valeurs = {'name': nom, 'project_id': self.demo.id,
                   'type_id': self.type_id.id}
        valeurs.update(kwargs)
        return self.Credential.create(valeurs)

    def test_demo_credentials_do_not_move_the_fleet_counters(self):
        avant = self.Tableau.get_credential_metrics()

        self._identifiant_demo('Démo active')
        self._identifiant_demo('Démo expirée',
                               expiration_date=self.aujourdhui - timedelta(days=40))
        self._identifiant_demo('Démo qui approche',
                               expiration_date=self.aujourdhui + timedelta(days=8))
        self.Credential._cron_check_expiring_credentials()

        apres = self.Tableau.get_credential_metrics()
        self.assertEqual(apres, avant,
                         'Une démonstration a déplacé les compteurs du parc')

    def test_the_demo_project_still_counts_its_own_credentials(self):
        self._identifiant_demo('Démo expirée',
                               expiration_date=self.aujourdhui - timedelta(days=40))
        self.Credential._cron_check_expiring_credentials()

        mesures = self.Tableau.get_credential_metrics(project_id=self.demo.id)
        self.assertEqual(mesures['expired'], 1,
                         "Le projet de démonstration ne montre plus ses propres "
                         "identifiants expirés")

    def test_the_domain_keeps_the_records_that_have_no_project(self):
        """La traversée vers le drapeau n'apparie pas un ``Many2one`` vide.

        Sans objet pour un identifiant — ``project_id`` y est obligatoire, la
        branche ``= False`` du domaine y est inerte. Elle ne l'est pas pour un
        document : 213 des 215 documents de la production Blue Fox n'ont aucun
        projet. Le jour où ce domaine servira aux compteurs de documents, la
        branche manquante en effacerait 213 d'un coup — silencieusement, un
        compteur ne dit pas ce qu'il n'a pas vu.

        Le contrôle porte donc sur le domaine lui-même, sur le modèle où le
        cas existe.
        """
        Document = self.env['project.document']
        document = Document.create({
            'name': 'Document sans projet',
            'code': 'ESSAI-SANS-PROJET',
            'type_id': self.env['project.document.type'].search([], limit=1).id,
        })
        self.assertFalse(document.project_id, 'Le document a pris un projet')

        domaine = self.env['project.project']._demo_exclusion_domain()
        self.assertIn(document, Document.search(domaine),
                      "Le domaine écarte un document qui n'a aucun projet")

    def test_the_biweekly_report_agrees_with_the_dashboard(self):
        """Les deux surfaces comptent le même parc.

        Le rapport bimensuel a ses propres requêtes. S'il gardait la
        démonstration, le courriel réclamerait une régularisation que le tableau
        de bord dit déjà faite.
        """
        self._identifiant_demo('Démo expirée',
                               expiration_date=self.aujourdhui - timedelta(days=40))
        self.Credential._cron_check_expiring_credentials()

        mesures = self.Tableau.get_credential_metrics()
        rapport = self.env['project.document']._get_dashboard_report_data()
        self.assertEqual(rapport['credentials_expired'], mesures['expired'])
        self.assertEqual(rapport['credentials_expiring'], mesures['expiring_soon'])
        self.assertEqual(rapport['credentials_total'], mesures['total'])
