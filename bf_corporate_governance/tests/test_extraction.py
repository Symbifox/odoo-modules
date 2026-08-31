"""Ce que l'extraction ne doit pas avoir laissé derrière.

Le déménagement se joue dans une passe de migration qui réattribue des
identifiants externes, pas dans le code. Une passe incomplète ne lève pas :
elle laisse un morceau du sous-système sous le nom de l'ancien module, et Odoo
efface ce morceau à la fin du chargement suivant, sans un mot.

Ces contrôles regardent l'état de la base APRÈS le déménagement. Sur une
installation neuve ils passent aussi, pour une autre raison — le module a
simplement créé ses enregistrements sous son propre nom. C'est voulu : le même
contrôle doit valoir dans les deux cas, sinon il ne dirait plus rien le jour où
un locataire neuf reçoit les deux modules d'un coup.
"""

from odoo import fields
from odoo.modules.module import get_manifest
from odoo.tests import TransactionCase

MODULE = 'bf_corporate_governance'
SOCLE = 'project_knowledge_matrix'

MODELES = [
    'corporate.resolution',
    'corporate.resolution.signatory',
    'corporate.director',
    'corporate.officer',
    'corporate.compliance.event',
]


class TestExtraction(TransactionCase):

    # Ce qu'Odoo engendre en reflétant le code : la migration les prend par
    # motif, pas un par un. C'est aussi là que vivent `model_project_document`
    # et `model_knowledge_dashboard` — deux modèles que ce module HÉRITE et ne
    # possède pas : les déplacer les arracherait au socle.
    REFLETES = (
        'ir.model', 'ir.model.fields', 'ir.model.fields.selection',
        'ir.model.inherit', 'ir.model.constraint', 'ir.model.relation',
    )

    def _xmlids(self, module):
        return set(self.env['ir.model.data'].search([
            ('module', '=', module),
        ]).mapped('name'))

    def _xmlids_declares(self, module):
        return set(self.env['ir.model.data'].search([
            ('module', '=', module),
            ('model', 'not in', list(self.REFLETES)),
        ]).mapped('name'))

    # ------------------------------------------------------------------
    # Rien de corporatif ne reste sous le socle
    # ------------------------------------------------------------------

    def test_the_base_module_owns_nothing_corporate_any_more(self):
        """Le contrôle qu'exécute aussi la migration, rejoué depuis l'ORM.

        Volontairement plus large que la liste déplacée : un contrôle trop
        large qui passe prouve plus qu'un contrôle taillé sur sa propre liste.
        """
        restes = self.env['ir.model.data'].search([
            ('module', '=', SOCLE),
            '|', '|', '|',
            ('name', 'ilike', 'corporate'),
            ('name', 'ilike', 'minute_book'),
            ('name', '=like', 'compliance\\_%'),
            ('name', '=', 'paperformat_resolution'),
        ])
        self.assertFalse(
            restes.mapped('name'),
            "Identifiants corporatifs restés sous %s : %s"
            % (SOCLE, sorted(restes.mapped('name'))[:20]),
        )

    def test_the_five_models_belong_to_this_module(self):
        for nom in MODELES:
            with self.subTest(modele=nom):
                modele = self.env['ir.model'].search([('model', '=', nom)])
                self.assertTrue(modele, '%s a disparu du registre' % nom)
                data = self.env['ir.model.data'].search([
                    ('model', '=', 'ir.model'),
                    ('res_id', '=', modele.id),
                    ('module', '=', MODULE),
                ])
                self.assertTrue(
                    data, "%s n'appartient pas à %s" % (nom, MODULE))

    def test_the_base_module_no_longer_names_the_corporate_models(self):
        """Un `self.env['corporate.…']` oublié dans le socle rendrait la
        dépendance circulaire : le socle ne doit rien savoir de la gouvernance.
        """
        depends = get_manifest(MODULE).get('depends', [])
        self.assertIn(SOCLE, depends)
        self.assertNotIn(MODULE, get_manifest(SOCLE).get('depends', []))

    # ------------------------------------------------------------------
    # La migration déplace tout ce que le module déclare
    # ------------------------------------------------------------------

    def test_the_migration_moved_everything_the_module_declares(self):
        """La liste de la migration est le miroir des fichiers de données.

        Ajouter une vue au module sans l'ajouter à la migration ne se voit pas
        sur une base neuve — seulement sur une base qui déménage, c'est-à-dire
        exactement chez le locataire qui a des données.
        """
        import importlib.util
        import os

        from odoo.modules.module import get_module_path

        chemin = os.path.join(
            get_module_path(SOCLE),
            'migrations', '18.0.12.0.0', 'pre-migrate.py',
        )
        self.assertTrue(os.path.exists(chemin), 'Passe de migration absente')
        spec = importlib.util.spec_from_file_location('pkm_pre_migrate', chemin)
        passe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(passe)

        a_lister = self._xmlids_declares(MODULE)
        # Garde-fou : un inventaire vide passerait au vert sans rien éprouver.
        self.assertTrue(a_lister, 'Aucun enregistrement déclaré par %s' % MODULE)
        oublies = sorted(a_lister - set(passe.XMLIDS))
        self.assertFalse(
            oublies,
            "Déclarés par %s mais absents de XMLIDS dans la migration : %s"
            % (MODULE, oublies),
        )

        # Et l'inverse : un identifiant listé dans la migration mais que le
        # module ne déclare plus resterait sous l'ancien nom sans que rien ne
        # le dise, jusqu'à ce qu'Odoo l'efface.
        surplus = sorted(set(passe.XMLIDS) - self._xmlids(MODULE))
        self.assertFalse(
            surplus,
            "Listés par la migration mais absents de %s : %s"
            % (MODULE, surplus),
        )

    # ------------------------------------------------------------------
    # Ce que le module rend au tableau de bord et au rapport
    # ------------------------------------------------------------------

    def test_the_dashboard_gets_its_corporate_block_back(self):
        donnees = self.env['knowledge.dashboard'].get_dashboard_data()
        self.assertIn('corporate_metrics', donnees)
        for cle in ('active_directors', 'active_officers', 'adopted_resolutions',
                    'overdue_compliance', 'due_soon_compliance',
                    'upcoming_compliance'):
            with self.subTest(cle=cle):
                self.assertIn(cle, donnees['corporate_metrics'])

    def test_the_corporate_block_stays_out_when_a_project_is_selected(self):
        """Ces chiffres ne sont pas projet-spécifiques : rendus sous un filtre,
        ils donneraient les mêmes valeurs pour tous les projets."""
        # Un projet existant plutôt qu'un projet neuf : sur un locataire, la
        # création d'un project.project traverse les champs obligatoires que
        # ses propres modules y ont ajoutés, et le test échouerait pour une
        # raison qui n'a rien à voir avec le bloc corporatif.
        projet = self.env['project.project'].search([], limit=1)
        if not projet:
            projet = self.env['project.project'].create({'name': 'Projet du bloc'})
        donnees = self.env['knowledge.dashboard'].get_dashboard_data(
            project_id=projet.id)
        self.assertNotIn('corporate_metrics', donnees)

    def test_the_biweekly_report_gets_its_five_numbers_and_their_links(self):
        """Cinq chiffres, cinq liens. Un chiffre sans son action retombe
        muettement sur le tableau de bord : le lecteur clique et n'arrive pas
        sur ce qu'il a lu."""
        donnees = self.env['project.document']._get_dashboard_report_data()
        cles = ('corp_active_directors', 'corp_active_officers',
                'corp_adopted_resolutions', 'corp_overdue_compliance',
                'corp_due_soon_compliance')
        for cle in cles:
            with self.subTest(cle=cle):
                self.assertIn(cle, donnees)
                self.assertIn(cle, donnees['links'])
                self.assertNotEqual(
                    donnees['links'][cle], donnees['dashboard_url'],
                    "Le lien de %s est retombé sur le tableau de bord : son "
                    "action de forage est introuvable." % cle,
                )

    def test_the_base_links_survive_the_extension(self):
        """Un attribut de classe redéclaré aurait MASQUÉ les liens du socle.
        C'est pour ça que c'est une méthode.

        Aucun total n'est posé ici. Les tests d'installation d'un module
        tournent quand CE module vient de se charger, pas quand tout est
        chargé : un autre module qui ajoute ses propres liens peut n'avoir pas
        encore été lu, et un compte attendu échouerait pour une raison qui n'a
        rien à voir avec ce qu'il éprouve. On vérifie donc la survie, pas le
        nombre.
        """
        actions = self.env['project.document']._get_report_link_actions()
        du_socle = {c: v for c, v in actions.items() if v.startswith(SOCLE + '.')}
        self.assertGreaterEqual(
            len(du_socle), 25,
            'Les liens du socle ont disparu de la table : %s' % sorted(actions))
        for cle in ('active_documents', 'expired_documents', 'overdue_review',
                    'decisions_total', 'docs_without_versions'):
            with self.subTest(cle=cle):
                self.assertIn(cle, du_socle)
        for cle in ('corp_active_directors', 'corp_active_officers',
                    'corp_adopted_resolutions', 'corp_overdue_compliance',
                    'corp_due_soon_compliance'):
            with self.subTest(cle=cle):
                self.assertTrue(actions[cle].startswith(MODULE + '.'))

    # ------------------------------------------------------------------
    # Ce qui doit avoir survécu au déménagement
    # ------------------------------------------------------------------

    def test_the_resolution_sequence_still_exists_and_keeps_counting(self):
        """La séquence ne se recrée pas : une séquence neuve repartirait à 1 et
        la 38e résolution reprendrait le numéro de la première."""
        sequence = self.env['ir.sequence'].search([
            ('code', '=', 'corporate.resolution'),
        ])
        self.assertEqual(len(sequence), 1, 'Séquence absente ou dédoublée')
        data = self.env['ir.model.data'].search([
            ('model', '=', 'ir.sequence'), ('res_id', '=', sequence.id),
        ])
        self.assertEqual(data.mapped('module'), [MODULE])

    def test_the_manager_group_still_implies_the_document_manager(self):
        groupe = self.env.ref(MODULE + '.group_corporate_manager')
        self.assertIn(
            self.env.ref(SOCLE + '.group_document_manager'),
            groupe.implied_ids,
        )

    def test_the_printed_report_answers_under_its_new_name(self):
        rapport = self.env.ref(MODULE + '.action_report_corporate_resolution')
        self.assertEqual(
            rapport.report_name, MODULE + '.report_corporate_resolution')
        self.assertTrue(
            self.env.ref(rapport.report_name, raise_if_not_found=False),
            'Le gabarit ne répond pas sous le nom que porte son action',
        )

    def test_the_report_action_keeps_its_paper_format(self):
        """Le format papier voyage avec l'action, pas avec le gabarit.

        Trois enregistrements distincts font le PDF de résolution : l'action,
        le gabarit qu'elle nomme, et le format papier qu'elle porte. Le
        gabarit est éprouvé par le rendu QWeb des tests de signataires;
        l'action et son format papier, ici. Le binaire lui-même n'est PAS
        rendu : wkhtmltopdf va chercher les feuilles de style par HTTP, et une
        passe de test tourne sans serveur.
        """
        rapport = self.env.ref(MODULE + '.action_report_corporate_resolution')
        self.assertEqual(
            rapport.paperformat_id,
            self.env.ref(MODULE + '.paperformat_resolution'),
        )
        self.assertEqual(rapport.paperformat_id.format, 'Letter')
        self.assertEqual(rapport.report_type, 'qweb-pdf')
        self.assertEqual(
            rapport.binding_model_id,
            self.env['ir.model']._get('corporate.resolution'),
            "Le rapport ne s'accroche plus au menu Imprimer de la résolution",
        )
        resolution = self.env['corporate.resolution'].create({
            'name': 'Résolution du contrôle de rendu',
            'resolution_type': 'board',
            'meeting_date': fields.Date.today(),
        })
        rendu = str(self.env['ir.qweb']._render(
            rapport.report_name, {'docs': resolution, 'env': self.env}))
        self.assertIn(resolution.sequence, rendu)

    def test_the_cron_points_at_a_method_that_exists(self):
        """Un cron dont la méthode a été renommée échoue en silence, une fois
        par jour. Même contrôle qu', sur le cron déménagé."""
        cron = self.env.ref(MODULE + '.cron_corporate_compliance_check')
        self.assertTrue(cron.active)
        self.assertEqual(cron.model_id.model, 'corporate.compliance.event')
        self.assertTrue(
            hasattr(self.env['corporate.compliance.event'],
                    '_cron_check_compliance_deadlines'),
        )

    def test_the_menus_hang_under_the_knowledge_root(self):
        parent = self.env.ref(MODULE + '.menu_corporate_parent')
        self.assertEqual(parent.parent_id, self.env.ref(SOCLE + '.menu_knowledge_root'))
        self.assertEqual(
            parent.groups_id, self.env.ref(MODULE + '.group_corporate_manager'))
        for nom in ('resolutions', 'directors', 'officers', 'compliance',
                    'minute_book'):
            with self.subTest(menu=nom):
                menu = self.env.ref('%s.menu_corporate_%s' % (MODULE, nom))
                self.assertEqual(menu.parent_id, parent)
                self.assertTrue(menu.action, 'Menu sans action : %s' % nom)
