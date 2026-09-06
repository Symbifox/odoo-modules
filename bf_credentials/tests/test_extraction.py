"""Ce que l'extraction ne doit pas avoir laissé derrière.

Le déménagement se joue dans une passe de migration qui réattribue des
identifiants externes, pas dans le code. Une passe incomplète ne lève pas :
elle laisse un morceau du coffre sous le nom de l'ancien module, et Odoo
efface ce morceau à la fin du chargement suivant, sans un mot.

Ici s'ajoute une exigence que n'avait pas : les valeurs sont
CHIFFRÉES. La clé vit dans ``ir.config_parameter`` et ne déménage pas avec le
module — mais ça se prouve, ça ne se suppose pas.
"""

import os

from odoo.modules.module import get_manifest
from odoo.tests import TransactionCase

MODULE = 'bf_credentials'
SOCLE = 'project_knowledge_matrix'

MODELES = [
    'project.credential',
    'project.credential.type',
    'project.credential.rotate.wizard',
]

# Nés ici, jamais passés par le socle : la migration n'a rien à en faire, et
# les lister l'obligerait à prétendre déplacer un identifiant inexistant.
NES_ICI = {
    # Le bouton intelligent était DANS la vue du socle, pas dans une vue à lui.
    # Le socle a perdu le bouton ; ce module en crée un neuf, dans une vue qui
    # hérite de la sienne.
    'project_view_form_inherit_credentials',

    # Registre du deuxième facteur, 18.0.2.0.0. Le socle
    # n'a jamais rien porté de tout ça : le lot est né dans ce module.
    'vault_password_manager', 'vault_authenticator', 'vault_personal_device',
    'vault_hardware_token', 'vault_offline',
    'credential_vault_view_list', 'credential_vault_view_form',
    'credential_vault_view_search', 'credential_vault_action',
    'menu_credential_vaults',
    'access_credential_vault_user', 'access_credential_vault_manager',
    'rule_credential_vault_read', 'rule_credential_vault_manager',
    'report_action_cred_mfa_unknown', 'report_action_cred_mfa_at_risk',
}

REFLETES = (
    'ir.model', 'ir.model.fields', 'ir.model.fields.selection',
    'ir.model.inherit', 'ir.model.constraint', 'ir.model.relation',
)


class TestExtraction(TransactionCase):

    def _xmlids(self, module):
        return set(self.env['ir.model.data'].search([
            ('module', '=', module)]).mapped('name'))

    def _xmlids_declares(self, module):
        return set(self.env['ir.model.data'].search([
            ('module', '=', module),
            ('model', 'not in', list(REFLETES)),
        ]).mapped('name'))

    # ------------------------------------------------------------------
    # Le chiffrement — ce qui arrête le chantier si ça casse
    # ------------------------------------------------------------------

    def test_every_stored_secret_still_decrypts(self):
        """Le contrôle qui décide si l'extraction tient.

        Un jeton Fernet déchiffré avec la MAUVAISE clé lève ``InvalidToken``,
        et ``_decrypt_value`` se replie alors en rendant le jeton TEL QUEL —
        silencieusement. Un mot de passe illisible se lit donc à l'écran comme
        une longue chaîne « gAAAAA… » plutôt que comme une erreur.

        Le contrôle : pour chaque secret en base, la valeur déchiffrée doit
        DIFFÉRER de la valeur chiffrée. Aucun texte clair n'est comparé, ni
        journalisé.
        """
        Credential = self.env['project.credential']
        illisibles = []
        for cred in Credential.search([]):
            for champ in ('password_encrypted', 'api_key_encrypted'):
                chiffre = cred[champ]
                if not chiffre:
                    continue
                if Credential._decrypt_value(chiffre) == chiffre:
                    illisibles.append('%s.%s' % (cred.id, champ))
        self.assertFalse(
            illisibles,
            'Secrets devenus illisibles après le déménagement : %s' % illisibles,
        )

    def test_the_encryption_key_lives_in_a_system_parameter(self):
        """La clé ne déménage pas avec le module.

        Si elle était une donnée du module, une désinstallation l'emporterait
        et les 76 valeurs deviendraient illisibles d'un coup.
        """
        self.env['project.credential']._get_encryption_key()
        cle = self.env['ir.config_parameter'].sudo().get_param(
            'project_credential.encryption_key')
        self.assertTrue(cle, 'Aucune clé de chiffrement en paramètre système')
        self.assertFalse(
            self.env['ir.model.data'].search([
                ('module', 'in', [MODULE, SOCLE]),
                ('model', '=', 'ir.config_parameter'),
            ]),
            "La clé est devenue une donnée de module : une désinstallation "
            "l'emporterait avec elle.",
        )

    def test_a_secret_survives_a_round_trip(self):
        """Chiffrer puis déchiffrer rend la valeur d'origine, et la colonne
        stockée ne porte jamais le clair."""
        Credential = self.env['project.credential']
        # Un projet est OBLIGATOIRE sur un identifiant. Sur une installation
        # neuve sans données de démonstration il n'y en a aucun, et le test
        # tombait sur la contrainte NOT NULL — pas sur ce qu'il éprouve.
        projet = self.env['project.project'].search([], limit=1)
        if not projet:
            projet = self.env['project.project'].create(
                {'name': 'Projet aller-retour'})
        type_id = self.env['project.credential.type'].create({
            'name': 'Type aller-retour', 'code': 'TEST-AR',
        })
        secret = 'mot-de-passe-aller-retour-24658'
        cred = Credential.create({
            'name': 'Identifiant aller-retour',
            'project_id': projet.id,
            'type_id': type_id.id,
            'password': secret,
        })
        cred.invalidate_recordset()
        self.assertNotIn(secret, cred.password_encrypted or '',
                         'Le secret est stocké en clair dans la colonne')
        self.assertEqual(cred.password, secret)

    # ------------------------------------------------------------------
    # Rien du coffre ne reste sous le socle
    # ------------------------------------------------------------------

    def test_the_base_module_owns_nothing_of_the_vault_any_more(self):
        restes = self.env['ir.model.data'].search([
            ('module', '=', SOCLE),
            ('name', 'ilike', 'credential'),
        ])
        self.assertFalse(
            restes.mapped('name'),
            'Identifiants du coffre restés sous %s : %s'
            % (SOCLE, sorted(restes.mapped('name'))[:20]),
        )

    def test_the_three_models_belong_to_this_module(self):
        for nom in MODELES:
            with self.subTest(modele=nom):
                modele = self.env['ir.model'].search([('model', '=', nom)])
                self.assertTrue(modele, '%s a disparu du registre' % nom)
                self.assertTrue(
                    self.env['ir.model.data'].search([
                        ('model', '=', 'ir.model'),
                        ('res_id', '=', modele.id),
                        ('module', '=', MODULE),
                    ]),
                    "%s n'appartient pas à %s" % (nom, MODULE),
                )

    def test_the_project_one2many_belongs_to_this_module(self):
        """Le champ TYPÉ qui rendait la dépendance circulaire.

        Tant que ``project.project.credential_ids`` vivait dans le socle, le
        socle dépendait durement du coffre.
        """
        champ = self.env['ir.model.fields'].search([
            ('model', '=', 'project.project'), ('name', '=', 'credential_ids'),
        ])
        self.assertTrue(champ, 'credential_ids a disparu de project.project')
        self.assertEqual(
            self.env['ir.model.data'].search([
                ('model', '=', 'ir.model.fields'), ('res_id', '=', champ.id),
            ]).mapped('module'),
            [MODULE],
        )

    def test_the_base_module_no_longer_depends_on_cryptography(self):
        externes = get_manifest(SOCLE).get('external_dependencies', {})
        self.assertNotIn('cryptography', externes.get('python', []))
        self.assertIn(SOCLE, get_manifest(MODULE).get('depends', []))
        self.assertNotIn(MODULE, get_manifest(SOCLE).get('depends', []))

    # ------------------------------------------------------------------
    # La migration déplace tout ce que le module déclare
    # ------------------------------------------------------------------

    def test_the_migration_moved_everything_the_module_declares(self):
        import importlib.util

        from odoo.modules.module import get_module_path

        chemin = os.path.join(get_module_path(SOCLE), 'migrations',
                              '18.0.13.0.0', 'pre-migrate.py')
        self.assertTrue(os.path.exists(chemin), 'Passe de migration absente')
        spec = importlib.util.spec_from_file_location('pkm_pre_13', chemin)
        passe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(passe)

        a_lister = self._xmlids_declares(MODULE) - NES_ICI
        self.assertTrue(a_lister, 'Aucun enregistrement déclaré par %s' % MODULE)
        oublies = sorted(a_lister - set(passe.XMLIDS))
        self.assertFalse(
            oublies,
            'Déclarés par %s mais absents de XMLIDS dans la migration : %s'
            % (MODULE, oublies))
        surplus = sorted(set(passe.XMLIDS) - self._xmlids(MODULE))
        self.assertFalse(
            surplus,
            'Listés par la migration mais absents de %s : %s' % (MODULE, surplus))

    # ------------------------------------------------------------------
    # Ce que le module rend au tableau de bord et au rapport
    # ------------------------------------------------------------------

    def test_the_dashboard_gets_its_credential_block_back(self):
        donnees = self.env['knowledge.dashboard'].get_dashboard_data()
        self.assertIn('credential_metrics', donnees)
        for cle in ('total', 'expiring_soon', 'expired', 'revoked'):
            with self.subTest(cle=cle):
                self.assertIn(cle, donnees['credential_metrics'])

    def test_the_biweekly_report_gets_its_three_numbers_and_their_links(self):
        donnees = self.env['project.document']._get_dashboard_report_data()
        for cle in ('credentials_total', 'credentials_expiring',
                    'credentials_expired'):
            with self.subTest(cle=cle):
                self.assertIn(cle, donnees)
                self.assertIn(cle, donnees['links'])
                self.assertNotEqual(
                    donnees['links'][cle], donnees['dashboard_url'],
                    'Le lien de %s est retombé sur le tableau de bord : son '
                    "action de forage est introuvable." % cle)

    def test_the_base_links_survive_the_extension(self):
        actions = self.env['project.document']._get_report_link_actions()
        self.assertIn('active_documents', actions)
        self.assertTrue(
            actions['active_documents'].startswith(SOCLE + '.'),
            'Le lien du socle a changé de module : %s' % actions['active_documents'])

    # ------------------------------------------------------------------
    # Ce qui doit avoir survécu au déménagement
    # ------------------------------------------------------------------

    def test_the_smart_button_reads_a_field_that_exists(self):
        """Un champ absent d'une vue n'échoue pas au chargement du module qui
        la porte : il échoue à l'OUVERTURE de la fiche, chez l'utilisateur."""
        vue = self.env.ref(MODULE + '.project_view_form_inherit_credentials')
        self.assertEqual(vue.model, 'project.project')
        self.assertIn('credential_count', vue.arch)
        self.assertIn('credential_count', self.env['project.project']._fields)
        projet = self.env['project.project'].search([], limit=1)
        if projet:
            projet.action_view_credentials()

    def test_the_cron_points_at_a_method_that_exists(self):
        cron = self.env.ref(MODULE + '.ir_cron_check_expiring_credentials')
        self.assertTrue(cron.active)
        self.assertEqual(cron.model_id.model, 'project.credential')
        self.assertTrue(hasattr(self.env['project.credential'],
                                '_cron_check_expiring_credentials'))

    def test_the_menus_hang_under_the_knowledge_root(self):
        menu = self.env.ref(MODULE + '.menu_credentials')
        self.assertEqual(menu.parent_id,
                         self.env.ref(SOCLE + '.menu_knowledge_root'))
        self.assertEqual(menu.groups_id,
                         self.env.ref(MODULE + '.group_credential_user'))
        config = self.env.ref(MODULE + '.menu_credential_types')
        self.assertEqual(config.parent_id,
                         self.env.ref(SOCLE + '.menu_knowledge_config'))

    def test_the_manager_group_still_implies_the_user_group(self):
        gestionnaire = self.env.ref(MODULE + '.group_credential_manager')
        self.assertIn(self.env.ref(MODULE + '.group_credential_user'),
                      gestionnaire.implied_ids)
