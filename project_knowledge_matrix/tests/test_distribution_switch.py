"""L'interrupteur du sous-système de distribution.

L'essai de février se lit sans ambiguïté : quatre envois, deux jamais accusés
puis rappelés, deux marqués accusés à dix-sept secondes d'écart, soit une saisie
manuelle en une fois. Douze jours d'essai, puis plus rien. Le code reste écrit
et réactivable ; ce qui change, c'est qu'il ne s'affiche plus par défaut.

Ce que ce filet doit tenir, dans les deux sens :

* éteint, plus rien de la distribution ne sort : ni menu, ni passe d'entretien,
  ni bloc du tableau de bord, ni chiffre dans le rapport bimensuel ;
* allumé, tout revient, à l'identique ;
* et surtout : les DONNÉES ne bougent jamais. Un interrupteur qui effacerait
  quatre distributions serait une suppression déguisée en réglage.
"""

from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged


class DistributionSwitchCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Distribution = cls.env['project.document.distribution']
        cls.Tableau = cls.env['knowledge.dashboard']
        cls.porteur = cls.env.ref('project_knowledge_matrix.group_document_user')
        cls.interrupteur = cls.env.ref(
            'project_knowledge_matrix.group_document_distribution')

    def _allumer(self, allume=True):
        self.porteur.write({
            'implied_ids': [(4 if allume else 3, self.interrupteur.id)],
        })


class TestTheSwitchItself(DistributionSwitchCase):

    def test_a_fresh_install_ships_it_off(self):
        """C'est la décision, et elle doit se lire dans les données du module.

        Le groupe existe, il n'est simplement pas implicite. Si quelqu'un
        l'ajoutait aux ``implied_ids`` du fichier de sécurité, toute
        installation neuve rallumerait une fonction abandonnée sans que
        personne l'ait demandé. D'où ce test plutôt qu'un commentaire.
        """
        self.assertNotIn(self.interrupteur, self.porteur.implied_ids)
        self.assertFalse(self.Distribution._est_active())

    def test_the_setting_and_the_helper_answer_the_same_thing(self):
        """Une seule source de vérité, éprouvée des deux côtés.

        La case des paramètres écrit dans les groupes ; ``_est_active`` les
        relit. Un paramètre système en parallèle aurait pu diverger, et ce test
        échouerait si quelqu'un en réintroduisait un.
        """
        Parametres = self.env['res.config.settings']

        Parametres.create({'group_document_distribution': True}).execute()
        self.assertTrue(self.Distribution._est_active())
        self.assertTrue(Parametres.default_get(
            ['group_document_distribution'])['group_document_distribution'])

        Parametres.create({'group_document_distribution': False}).execute()
        self.assertFalse(self.Distribution._est_active())
        self.assertFalse(Parametres.default_get(
            ['group_document_distribution'])['group_document_distribution'])

    def test_the_three_menus_hang_on_the_switch_group(self):
        """Le lien menu -> groupe, vérifié sur les enregistrements réels.

        Les menus étaient sur « Documents / Utilisateur ». S'ils y revenaient,
        l'interrupteur ne masquerait plus rien et personne ne le verrait :
        éteindre la fonction laisserait les menus en place.
        """
        for xmlid in ('menu_distributions', 'menu_distributions_internal',
                      'menu_distributions_outdated'):
            with self.subTest(menu=xmlid):
                menu = self.env.ref(f'project_knowledge_matrix.{xmlid}')
                self.assertIn(self.interrupteur, menu.groups_id)
                self.assertNotIn(self.porteur, menu.groups_id)


class TestTheDailyMaintenancePasses(DistributionSwitchCase):

    def _passes_appelees(self):
        appelees = []
        Document = self.env['project.document']
        with patch.object(type(self.Distribution),
                          '_cron_check_pending_acknowledgments',
                          lambda self: appelees.append('accuses')), \
             patch.object(type(Document), '_cron_check_document_reviews',
                          lambda self: appelees.append('revisions')), \
             patch.object(type(Document), '_cron_check_outdated_client_docs',
                          lambda self: appelees.append('obsoletes')):
            Document._cron_document_maintenance()
        return appelees

    def test_off_only_the_review_pass_runs(self):
        self._allumer(False)
        self.assertEqual(self._passes_appelees(), ['revisions'])

    def test_on_the_three_passes_run_in_order(self):
        self._allumer()
        self.assertEqual(self._passes_appelees(),
                         ['accuses', 'revisions', 'obsoletes'])

    def test_the_review_pass_never_depends_on_the_switch(self):
        """Les rappels de révision ne lisent aucune distribution.

        Les accrocher à l'interrupteur par mégarde éteindrait, avec une fonction
        abandonnée, la seule passe qui travaille vraiment : 107 documents sur
        194 portent les deux dates.
        """
        for allume in (True, False):
            with self.subTest(allume=allume):
                self._allumer(allume)
                self.assertIn('revisions', self._passes_appelees())


class TestTheDashboardBlocks(DistributionSwitchCase):

    BLOCS = ('client_metrics', 'internal_metrics', 'distribution_activity')

    def test_off_the_three_blocks_are_absent_not_zeroed(self):
        """Absents, pas à zéro.

        Un bloc rendu à zéro se lit comme un fait : « aucune distribution en
        retard ». Une clé absente dit la vérité, qui est qu'on ne compte plus.
        Le gabarit s'en sert d'ailleurs comme condition d'affichage.
        """
        self._allumer(False)
        donnees = self.Tableau.get_dashboard_data()
        for bloc in self.BLOCS:
            with self.subTest(bloc=bloc):
                self.assertNotIn(bloc, donnees)

    def test_on_the_three_blocks_come_back(self):
        self._allumer()
        donnees = self.Tableau.get_dashboard_data()
        for bloc in self.BLOCS:
            with self.subTest(bloc=bloc):
                self.assertIn(bloc, donnees)
                self.assertIsNotNone(donnees[bloc])

    def test_the_other_blocks_never_move(self):
        """Éteindre la distribution ne doit rien retirer d'autre."""
        autres = ('document_overview', 'review_metrics', 'content_quality',
                  'matrix_metrics', 'decision_metrics')
        self._allumer(False)
        eteint = self.Tableau.get_dashboard_data()
        self._allumer(True)
        allume = self.Tableau.get_dashboard_data()
        for bloc in autres:
            with self.subTest(bloc=bloc):
                self.assertEqual(eteint[bloc], allume[bloc])


class TestTheBiweeklyReport(DistributionSwitchCase):
    """Le rapport bimensuel, éprouvé sur des distributions qui existent VRAIMENT.

    Sans jeu d'essai, tous les compteurs valent zéro parce que la table est
    vide, et « éteint, tout est à zéro » ne prouve alors rien du tout : une
    garde oubliée passerait au vert. Chaque chiffre vérifié ici est donc non nul
    quand l'interrupteur est mis.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        type_doc = cls.env['project.document.type'].create({
            'name': 'Type rapport', 'code': 'TEST-RAP',
        })
        cls.doc = cls.env['project.document'].create({
            'name': 'Document rapport', 'code': 'TEST-RAP-1',
            'type_id': type_doc.id, 'state': 'active',
        })
        # Deux versions : la v1 rend obsolètes les distributions qui la portent.
        v1 = cls.env['project.document.version'].create({
            'document_id': cls.doc.id, 'version_number': '1.0',
        })
        v2 = cls.env['project.document.version'].create({
            'document_id': cls.doc.id, 'version_number': '2.0',
        })
        client = cls.env['res.partner'].create({
            'name': 'Client rapport', 'is_company': True,
        })
        vieux = fields.Datetime.subtract(fields.Datetime.now(), days=40)

        Dist = cls.env['project.document.distribution']
        # Client, en attente, distribuée il y a 40 jours -> compte aussi dans
        # « accusés en retard (7+ jours) » et dans « mois dernier ».
        cls.dist_client = Dist.create({
            'version_id': v1.id, 'recipient_type': 'partner',
            'partner_id': client.id, 'state': 'pending',
            'distribution_date': vieux,
        })
        # Employé, accusée, ce mois-ci.
        cls.dist_employe = Dist.create({
            'version_id': v2.id, 'recipient_type': 'employee',
            'user_id': cls.env.user.id, 'state': 'acknowledged',
            'acknowledged_date': fields.Datetime.now(),
        })

    CHIFFRES = ('client_distributions', 'client_pending', 'client_outdated',
                'client_ack_rate', 'internal_distributions',
                'internal_pending', 'internal_compliance_rate',
                'distributions_this_month', 'distributions_last_month',
                'overdue_acknowledgments')

    def test_on_the_figures_are_not_all_zero(self):
        """Le garde-fou du jeu d'essai : sans lui, le test suivant ne prouve rien."""
        self._allumer()
        ctx = self.env['project.document']._get_dashboard_report_data()
        self.assertTrue(ctx['distribution_enabled'])
        non_nuls = [cle for cle in self.CHIFFRES if ctx[cle]]
        self.assertTrue(
            len(non_nuls) >= 6,
            f"jeu d'essai trop maigre pour éprouver l'extinction : {non_nuls}")

    def test_off_every_distribution_figure_is_zero_and_the_flag_is_false(self):
        """Le courriel ne doit pas annoncer des distributions qu'on n'affiche plus.

        Les deux encadrés se cachent sur ``distribution_enabled`` ; la ligne
        « Accusés en retard » du bloc d'alerte, elle, se cache déjà sur son
        propre « > 0 ». Les deux mécanismes sont éprouvés ici, sur des
        distributions que les compteurs verraient sans la garde.
        """
        self._allumer(False)
        ctx = self.env['project.document']._get_dashboard_report_data()
        self.assertFalse(ctx['distribution_enabled'])
        for cle in self.CHIFFRES:
            with self.subTest(cle=cle):
                self.assertEqual(ctx[cle], 0)

    def test_on_the_flag_is_true(self):
        self._allumer()
        ctx = self.env['project.document']._get_dashboard_report_data()
        self.assertTrue(ctx['distribution_enabled'])

    def test_the_non_distribution_figures_never_move(self):
        """Le rapport garde tout le reste, à l'identique."""
        Document = self.env['project.document']
        self._allumer(False)
        eteint = Document._get_dashboard_report_data()
        self._allumer(True)
        allume = Document._get_dashboard_report_data()
        for cle in ('total_documents', 'active_documents', 'overdue_review',
                    'expired_documents', 'review_0_30', 'docs_without_versions',
                    'decisions_total'):
            with self.subTest(cle=cle):
                self.assertEqual(eteint[cle], allume[cle])


# Rendre ce gabarit lit report_brand_* sur res.company, un champ que
# `bluefox_branding` apporte — et qui n'est PAS une dépendance déclarée de ce
# module. Les tests d'installation tournent quand ce module-ci vient de se
# charger : rien n'oblige bluefox_branding à l'avoir précédé, et le rendu lève
# alors une AttributeError. Vert sur une base, rouge sur une autre, pour
# une raison qui n'a rien à voir avec ce que le test éprouve.
@tagged('post_install', '-at_install')
class TestTheReportActuallyRenders(DistributionSwitchCase):
    """Le gabarit du rapport, rendu pour de vrai, dans les deux états.

    Les gardes ajoutées au gabarit vivent dans un fichier ``noupdate="1"`` que
    la migration recrée. Vérifier le contenu du fichier ne dit rien de ce que
    l'abonné recevra : c'est le rendu qu'il faut regarder.
    """

    def _rendu(self):
        """Le même chemin que ``_send_dashboard_report``, à l'envoi près.

        Le gabarit lit ``ctx``, qui n'est pas une variable de rendu mais le
        CONTEXTE de l'environnement : l'envoi fait ``template.with_context(
        données)``. Rendre sans ce contexte donne un courriel de zéros où toutes
        les gardes semblent fermées, et un test écrit comme ça passerait au vert
        sans rien éprouver.
        """
        gabarit = self.env.ref(
            'project_knowledge_matrix.mail_template_document_dashboard_report')
        donnees = self.env['project.document']._get_dashboard_report_data()
        societe = self.env.company
        rendu = gabarit.with_context(**donnees)._render_field(
            'body_html', societe.ids)
        return str(rendu[societe.id])

    def test_off_the_distribution_sections_are_gone_from_the_html(self):
        self._allumer(False)
        html = self._rendu()
        self.assertNotIn('Documentation clients', html)
        self.assertNotIn('Conformité interne', html)
        self.assertNotIn('Distributions ce mois', html)
        # Le reste du courriel doit rester debout.
        self.assertIn('Identifiants', html)
        self.assertIn('Docs sans version', html)

    def test_on_they_come_back(self):
        self._allumer()
        html = self._rendu()
        self.assertIn('Documentation clients', html)
        self.assertIn('Conformité interne', html)
        self.assertIn('Distributions ce mois', html)


class TestTheDataSurvives(DistributionSwitchCase):

    def test_flipping_the_switch_touches_no_record(self):
        """L'interrupteur ne supprime rien. C'est toute sa raison d'être.

        Les accusés gardent une valeur Loi 25 : ils prouvent qu'un document a
        été porté à la connaissance de quelqu'un, à une date. Un « rangement »
        qui les effacerait serait une perte de preuve, pas une simplification.
        """
        type_doc = self.env['project.document.type'].create({
            'name': 'Type interrupteur', 'code': 'TEST-SW',
        })
        doc = self.env['project.document'].create({
            'name': 'Document interrupteur', 'code': 'TEST-SW-1',
            'type_id': type_doc.id, 'state': 'active',
        })
        version = self.env['project.document.version'].create({
            'document_id': doc.id, 'version_number': '1.0',
        })
        distribution = self.env['project.document.distribution'].create({
            'version_id': version.id,
            'recipient_type': 'employee',
            'user_id': self.env.user.id,
        })

        avant = distribution.read()[0]
        self._allumer(True)
        self._allumer(False)
        self._allumer(True)
        self._allumer(False)

        self.assertTrue(distribution.exists())
        self.assertEqual(distribution.read()[0], avant)
        self.assertEqual(
            self.env['project.document.distribution'].search_count(
                [('id', '=', distribution.id)]), 1)
