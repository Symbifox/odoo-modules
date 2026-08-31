"""Le registre du deuxième facteur, et la promesse qu'il ne garde aucune graine.

Le module dit « le registre sait, le coffre garde ». Une promesse qui n'est
vérifiée nulle part finit par être fausse : ces tests l'exécutent.

Trois exigences distinctes.
1. Aucune graine n'entre, quelle que soit la forme sous laquelle on la colle.
2. L'état résume ce qu'on SAIT du facteur, pas la qualité du facteur.
3. Les deux compteurs neufs et leurs forages ramènent la même population, comme
   les trois qui les précèdent.
"""

import base64
import os

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval

from odoo.addons.bf_credentials.models.otp_secret_guard import otp_secret_reason

MODULE = 'bf_credentials'


class MfaCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Credential = cls.env['project.credential']
        cls.Vault = cls.env['project.credential.vault']
        cls.projet = cls.env['project.project'].create(
            {'name': 'Projet registre 2e facteur'})
        cls.type_id = cls.env['project.credential.type'].create(
            {'name': 'Type registre', 'code': 'TEST-MFA'})
        # ⚠️ Pas `env.user` : en test, l'environnement tourne en superutilisateur
        # et `res.users(1)` (la racine) est ARCHIVÉ. Un Many2many vers res.users
        # filtre les archivés à la lecture, donc le porteur serait invisible et
        # le test tomberait pour une raison qui n'a rien à voir avec le calcul.
        cls.porteuse = cls.env['res.users'].create({
            'name': 'Porteuse de facteur',
            'login': 'porteuse.facteur@test.invalid',
        })
        cls.coffre = cls.Vault.create({
            'name': 'Coffre de test',
            'kind': 'password_manager',
            'item_url_pattern': 'https://coffre.test/#/vault?itemId={ref}',
        })

    def _identifiant(self, nom='Identifiant', **kwargs):
        valeurs = {'name': nom, 'project_id': self.projet.id,
                   'type_id': self.type_id.id}
        valeurs.update(kwargs)
        return self.Credential.create(valeurs)


@tagged('post_install', '-at_install')
class TestTheGuardRefusesSeeds(MfaCase):
    """Le garde-fou : ce qui ne peut être qu'une graine ne rentre pas."""

    FORMES_DE_GRAINE = (
        ('adresse otpauth', 'otpauth://totp/Exemple:compte?secret=JBSWY3DPEHPK3PXP&issuer=Exemple'),
        ('export Google Authenticator', 'otpauth-migration://offline?data=CjEKCkhlbGxv'),
        ('paramètre secret=', 'secret=JBSWY3DPEHPK3PXP'),
        ('graine nue', 'JBSWY3DPEHPK3PXP'),
        ('graine en groupes de quatre', 'JBSW Y3DP EHPK 3PXP'),
        ('graine séparée par des tirets', 'JBSW-Y3DP-EHPK-3PXP'),
        ('graine de 32', 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP'),
    )

    def test_the_reference_field_refuses_every_shape_of_seed(self):
        for libelle, valeur in self.FORMES_DE_GRAINE:
            with self.subTest(forme=libelle):
                with self.assertRaises(ValidationError):
                    self._identifiant(mfa_type='totp', mfa_reference=valeur)

    def test_the_other_holder_field_refuses_them_too(self):
        """Le garde ne sert à rien s'il ne couvre qu'un champ sur deux."""
        for libelle, valeur in self.FORMES_DE_GRAINE:
            with self.subTest(forme=libelle):
                with self.assertRaises(ValidationError):
                    self._identifiant(mfa_type='totp', mfa_holder_note=valeur)

    def test_a_seed_pasted_later_is_refused_too(self):
        """La contrainte doit tenir à l'écriture, pas seulement à la création."""
        cred = self._identifiant(mfa_type='totp', mfa_reference='Élément 4f2c')
        with self.assertRaises(ValidationError):
            cred.write({'mfa_reference': 'JBSWY3DPEHPK3PXP'})

    def test_the_refusal_names_what_it_recognised(self):
        """Un refus muet se contourne ; un refus qui explique se corrige."""
        with self.assertRaises(ValidationError) as capture:
            self._identifiant(mfa_type='totp',
                              mfa_reference='otpauth://totp/x?secret=JBSWY3DPEHPK3PXP')
        message = str(capture.exception)
        self.assertIn('otpauth', message)
        self.assertIn('Référence chez le porteur', message)

    def test_real_labels_still_go_through(self):
        """Un faux positif bloquerait la saisie sans porte de sortie."""
        etiquettes = [
            "Élément « Serveur de production » du coffre",
            'AWS-PROD', 'SERVEURPRODUCTION', 'AUTHENTIFICATION2FA',
            'item 4f2c-9ab1', 'COORDINATION2026', 'Yubikey #2',
            "Jeton matériel, tiroir de la réception",
            "TOTP sur le téléphone d'une personne",
        ]
        for etiquette in etiquettes:
            with self.subTest(etiquette=etiquette):
                cred = self._identifiant(mfa_type='totp', mfa_reference=etiquette)
                self.assertEqual(cred.mfa_reference, etiquette)

    def test_the_guard_catches_the_rate_its_docstring_claims(self):
        """Le garde est un filet, et son maillage est mesuré, pas supposé.

        Le module annonce qu'il laisse passer environ une graine sur cent : celles
        de 16 caractères qui, par hasard, ne portent aucun chiffre de 2 à 7. Si
        quelqu'un resserre ou relâche la règle, ce test le dit avec un chiffre
        plutôt qu'avec une opinion.
        """
        graines = [base64.b32encode(os.urandom(n)).decode()
                   for n in (10, 16, 20, 32) for _ in range(50)]
        ratees = [g for g in graines if not otp_secret_reason(g)]
        self.assertLess(
            len(ratees), len(graines) * 0.05,
            f"Le garde laisse passer {len(ratees)} graines sur {len(graines)} : "
            f"c'est au-dessus de ce que le module annonce.",
        )

    def test_every_free_text_mfa_field_is_covered_by_the_guard(self):
        """Le garde doit suivre les champs, sinon il rouille en silence.

        Ajouter demain un champ texte au registre sans l'inscrire à la
        contrainte rouvrirait la porte sans que rien ne le signale. Ce test
        tombe le jour où ça arrive.
        """
        gardes = set()
        for methode in self.Credential._constraint_methods:
            if methode.__name__ == '_check_no_otp_secret':
                gardes = set(methode._constrains)
        libres = {
            nom for nom, champ in self.Credential._fields.items()
            if nom.startswith('mfa_') and champ.type == 'char'
            and not champ.compute
        }
        self.assertFalse(
            libres - gardes,
            'Un champ texte du registre échappe au garde-fou : '
            f'{sorted(libres - gardes)}',
        )
        self.assertIn(
            'notes', gardes,
            "Les notes doivent rester surveillées : c'est là qu'on colle les "
            "instructions d'enrôlement en entier.")


@tagged('post_install', '-at_install')
class TestTheStateSummarisesWhatIsKnown(MfaCase):
    """L'état ne juge pas le facteur, il juge ce que le registre en sait."""

    def test_a_credential_that_was_never_asked_is_to_be_documented(self):
        """Le défaut ne doit pas inventer une réponse rassurante.

        Les identifiants qui existaient avant ce lot n'ont jamais été interrogés.
        Les déclarer « sans deuxième facteur » serait une réponse fausse dans le
        sens qui rassure.
        """
        self.assertEqual(self._identifiant().mfa_state, 'unknown')

    def test_declaring_no_factor_is_an_answer(self):
        self.assertEqual(
            self._identifiant(mfa_type='none').mfa_state, 'absent')

    def test_a_factor_nobody_is_named_to_produce_is_at_risk(self):
        cred = self._identifiant(mfa_type='totp', mfa_recovery='backup_codes')
        self.assertEqual(cred.mfa_state, 'at_risk')

    def test_a_factor_without_written_recovery_is_at_risk(self):
        cred = self._identifiant(mfa_type='totp', mfa_vault_id=self.coffre.id)
        self.assertEqual(cred.mfa_state, 'at_risk')

    def test_recovery_declared_absent_is_still_at_risk(self):
        """« Aucune reprise » est documenté ET risqué : les deux à la fois."""
        cred = self._identifiant(mfa_type='totp', mfa_vault_id=self.coffre.id,
                                 mfa_recovery='none')
        self.assertEqual(cred.mfa_state, 'at_risk')

    def test_a_holder_plus_a_recovery_is_covered(self):
        cred = self._identifiant(mfa_type='totp', mfa_vault_id=self.coffre.id,
                                 mfa_recovery='vault')
        self.assertEqual(cred.mfa_state, 'covered')

    def test_a_person_can_be_the_holder(self):
        cred = self._identifiant(mfa_type='app',
                                 mfa_holder_ids=[(4, self.porteuse.id)],
                                 mfa_recovery='second_holder')
        self.assertEqual(cred.mfa_state, 'covered')

    def test_archiving_the_only_holder_puts_the_factor_at_risk(self):
        """Le départ d'une personne doit se voir dans le registre, tout seul.

        C'est la raison d'être du lot. Un `Many2many` vers `res.users` filtre
        les comptes archivés à la lecture : le calcul cesse donc de voir la
        personne partie, sans qu'on ait à écrire quoi que ce soit. Ce n'est pas
        un effet de bord dont on s'accommode, c'est le comportement qu'on veut,
        et il est éprouvé ici pour qu'un futur `active_test=False` posé par
        commodité ne l'efface pas en silence.
        """
        cred = self._identifiant(mfa_type='app',
                                 mfa_holder_ids=[(4, self.porteuse.id)],
                                 mfa_recovery='second_holder')
        self.assertEqual(cred.mfa_state, 'covered')

        self.porteuse.action_archive()
        cred.invalidate_recordset()
        cred.modified(['mfa_holder_ids'])
        cred.flush_recordset()

        self.assertFalse(
            cred.mfa_holder_ids,
            "Un compte archivé ne doit plus compter comme porteur")
        self.assertEqual(cred.mfa_state, 'at_risk')

    def test_a_holder_outside_odoo_counts_as_a_holder(self):
        cred = self._identifiant(mfa_type='hardware',
                                 mfa_holder_note='Jeton au coffre-fort',
                                 mfa_recovery='provider')
        self.assertEqual(cred.mfa_state, 'covered')

    def test_backup_codes_alone_are_their_own_recovery(self):
        self.assertEqual(
            self._identifiant(mfa_type='backup_codes').mfa_state, 'covered')

    def test_the_state_follows_a_later_edit(self):
        """L'état est stocké : il doit se recalculer, pas se figer."""
        cred = self._identifiant(mfa_type='totp', mfa_vault_id=self.coffre.id)
        self.assertEqual(cred.mfa_state, 'at_risk')
        cred.mfa_recovery = 'vault'
        self.assertEqual(cred.mfa_state, 'covered')
        cred.mfa_vault_id = False
        self.assertEqual(cred.mfa_state, 'at_risk')


@tagged('post_install', '-at_install')
class TestTheLinkToTheHolder(MfaCase):
    """Le raccordement : Odoo sait où aller, il n'y va pas."""

    def test_the_link_is_built_from_the_pattern_and_the_reference(self):
        cred = self._identifiant(mfa_type='totp', mfa_vault_id=self.coffre.id,
                                 mfa_reference='4f2c9ab1')
        self.assertEqual(
            cred.mfa_item_url, 'https://coffre.test/#/vault?itemId=4f2c9ab1')

    def test_the_reference_is_encoded_for_the_url(self):
        """Une référence avec un espace ou un & casserait le lien, ou pire."""
        cred = self._identifiant(mfa_type='totp', mfa_vault_id=self.coffre.id,
                                 mfa_reference='Serveur & base')
        self.assertEqual(
            cred.mfa_item_url,
            'https://coffre.test/#/vault?itemId=Serveur%20%26%20base')

    def test_no_pattern_means_no_link(self):
        nu = self.Vault.create({'name': 'Coffre sans gabarit',
                                'kind': 'device'})
        cred = self._identifiant(mfa_type='totp', mfa_vault_id=nu.id,
                                 mfa_reference='4f2c')
        self.assertFalse(cred.mfa_item_url)

    def test_no_reference_means_no_link(self):
        """Mieux vaut pas de lien qu'un lien qui mène à la mauvaise fiche."""
        cred = self._identifiant(mfa_type='totp', mfa_vault_id=self.coffre.id)
        self.assertFalse(cred.mfa_item_url)

    def test_a_pattern_without_the_marker_yields_no_link(self):
        casse = self.Vault.create({
            'name': 'Coffre au gabarit sans marqueur', 'kind': 'other',
            'item_url_pattern': 'https://coffre.test/vault',
        })
        cred = self._identifiant(mfa_type='totp', mfa_vault_id=casse.id,
                                 mfa_reference='4f2c')
        self.assertFalse(cred.mfa_item_url)


@tagged('post_install', '-at_install')
class TestTheTwoNewCountersAndTheirDrilldowns(MfaCase):
    """Un chiffre et sa liste doivent ramener la même population.

    Même discipline que les trois compteurs qui précèdent : le domaine du
    forage est écrit à la main, en XML statique, loin du compteur. Rien
    n'empêche les deux de diverger, sauf ce test.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tableau = cls.env['knowledge.dashboard']
        cls._identifiant_de_classe('Jamais interrogé')
        cls._identifiant_de_classe('Sans facteur', mfa_type='none')
        cls._identifiant_de_classe('Facteur orphelin', mfa_type='totp')
        cls._identifiant_de_classe(
            'Facteur couvert', mfa_type='totp',
            mfa_vault_id=cls.coffre.id, mfa_recovery='vault')

    @classmethod
    def _identifiant_de_classe(cls, nom, **kwargs):
        valeurs = {'name': nom, 'project_id': cls.projet.id,
                   'type_id': cls.type_id.id}
        valeurs.update(kwargs)
        return cls.env['project.credential'].create(valeurs)

    def _compter_par_action(self, xmlid):
        """Exécute le domaine du forage, comme le ferait le client.

        `ir.actions.act_window.domain` est une CHAÎNE : c'est le navigateur qui
        l'évalue. Le passer tel quel à `search_count` lève, et le test tomberait
        pour une raison qui n'a rien à voir avec ce qu'il éprouve.
        """
        action = self.env.ref(f'{MODULE}.{xmlid}')
        domaine = action.domain
        if isinstance(domaine, str):
            domaine = safe_eval(domaine)
        return self.env[action.res_model].search_count(domaine or [])

    def test_each_mfa_drilldown_returns_its_counter(self):
        mesures = self.Tableau.get_credential_metrics()
        for cle, xmlid in (
            ('mfa_unknown', 'report_action_cred_mfa_unknown'),
            ('mfa_at_risk', 'report_action_cred_mfa_at_risk'),
        ):
            with self.subTest(compteur=cle):
                self.assertEqual(
                    self._compter_par_action(xmlid), mesures[cle],
                    f'Le forage « {xmlid} » ne ramène pas ce que « {cle} » annonce',
                )

    def test_revoking_a_credential_takes_it_out_of_both_counters(self):
        """Un identifiant révoqué n'a plus de facteur à documenter.

        Le laisser dedans ferait grossir une liste de travail que personne ne
        peut vider : on ne documente pas le deuxième facteur d'un accès qui
        n'existe plus.
        """
        avant = self.Tableau.get_credential_metrics()
        orphelin = self.Credential.search(
            [('name', '=', 'Facteur orphelin')], limit=1)
        orphelin.action_revoke()
        apres = self.Tableau.get_credential_metrics()
        self.assertEqual(apres['mfa_at_risk'], avant['mfa_at_risk'] - 1)

    def test_a_demo_credential_does_not_weigh_on_the_fleet_counters(self):
        """Les identifiants fictifs d'une démonstration ne créent pas de travail."""
        demo = self.env['project.project'].create(
            {'name': 'Démo registre', 'knowledge_is_demo': True})
        avant = self.Tableau.get_credential_metrics()
        self.Credential.create({
            'name': 'Identifiant de démonstration',
            'project_id': demo.id, 'type_id': self.type_id.id,
        })
        apres = self.Tableau.get_credential_metrics()
        self.assertEqual(apres['mfa_unknown'], avant['mfa_unknown'])

    def test_the_demo_project_still_counts_its_own(self):
        """Filtré SUR la démonstration, elle doit montrer ce qu'elle démontre."""
        demo = self.env['project.project'].create(
            {'name': 'Démo registre bis', 'knowledge_is_demo': True})
        self.Credential.create({
            'name': 'Identifiant de démonstration bis',
            'project_id': demo.id, 'type_id': self.type_id.id,
        })
        mesures = self.Tableau.get_credential_metrics(project_id=demo.id)
        self.assertEqual(mesures['mfa_unknown'], 1)


@tagged('post_install', '-at_install')
class TestTheViewsLoadForARealAccount(MfaCase):
    """Les vues doivent s'ouvrir sous un vrai compte, pas seulement en uid 1.

    ⚠️ Le superutilisateur n'appartient à AUCUN groupe : Odoo lui retire les
    attributs `groups` au lieu de les évaluer. Une vue qui charge en uid 1 peut
    donc très bien refuser de charger pour la personne à qui elle est destinée.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.utilisatrice = cls.env['res.users'].create({
            'name': 'Utilisatrice du coffre',
            'login': 'coffre.user@test.invalid',
            'groups_id': [(6, 0, [
                cls.env.ref('bf_credentials.group_credential_user').id,
            ])],
        })
        cls.gestionnaire = cls.env['res.users'].create({
            'name': 'Gestionnaire du coffre',
            'login': 'coffre.manager@test.invalid',
            'groups_id': [(6, 0, [
                cls.env.ref('bf_credentials.group_credential_manager').id,
            ])],
        })

    def test_the_credential_views_load_for_a_plain_user(self):
        vues = self.Credential.with_user(self.utilisatrice).get_views([
            (self.env.ref(f'{MODULE}.credential_view_form').id, 'form'),
            (self.env.ref(f'{MODULE}.credential_view_list').id, 'list'),
            (self.env.ref(f'{MODULE}.credential_view_kanban').id, 'kanban'),
            (self.env.ref(f'{MODULE}.credential_view_search').id, 'search'),
        ])
        self.assertEqual(len(vues['views']), 4)
        self.assertIn('mfa_type', vues['models']['project.credential']['fields'])

    def test_the_vault_views_load_for_a_manager(self):
        vues = self.Vault.with_user(self.gestionnaire).get_views([
            (self.env.ref(f'{MODULE}.credential_vault_view_form').id, 'form'),
            (self.env.ref(f'{MODULE}.credential_vault_view_list').id, 'list'),
        ])
        self.assertEqual(len(vues['views']), 2)

    def test_a_plain_user_can_read_the_vaults_but_not_write_them(self):
        """Le registre se lit par tous, il ne se réécrit que par un gestionnaire.

        Sans lecture, le champ « Porteur du facteur » serait vide pour tout le
        monde sauf le gestionnaire, et le registre ne servirait à personne.
        """
        vus = self.Vault.with_user(self.utilisatrice).search([])
        self.assertTrue(vus, "Un utilisateur d'identifiants doit voir les porteurs")
        with self.assertRaises(AccessError):
            self.Vault.with_user(self.utilisatrice).create(
                {'name': 'Coffre interdit', 'kind': 'other'})


@tagged('post_install', '-at_install')
class TestTheGuardWatchesTheNotesToo(MfaCase):
    """Les notes sont le vrai chemin de fuite, avec une sévérité moindre.

    Personne ne colle une graine dans « Référence chez le porteur ». On colle
    les instructions d'enrôlement EN ENTIER dans les notes, adresse otpauth://
    comprise. Mais les notes sont du champ de rédaction : un faux positif y
    bloquerait une sauvegarde sans porte de sortie, donc seules les deux règles
    certaines s'y appliquent.
    """

    def test_an_enrolment_uri_pasted_into_the_notes_is_refused(self):
        with self.assertRaises(ValidationError):
            self._identifiant(notes='<p>Pour réenrôler : '
                                    'otpauth://totp/Exemple:compte?secret=JBSWY3DPEHPK3PXP</p>')

    def test_it_is_caught_even_inside_a_link(self):
        """Le collage réel produit souvent un lien, pas du texte nu."""
        with self.assertRaises(ValidationError):
            self._identifiant(
                notes='<p>Voir <a href="otpauth://totp/x?secret=JBSWY3DPEHPK3PXP">'
                      'ce lien</a></p>')

    def test_a_secret_parameter_in_the_notes_is_refused(self):
        with self.assertRaises(ValidationError):
            self._identifiant(notes='<p>secret=JBSWY3DPEHPK3PXP</p>')

    def test_a_bare_base32_string_in_the_notes_goes_through(self):
        """Volontaire, et c'est le point délicat du lot.

        La règle du base32 nu ne s'applique PAS aux notes. Elle y ferait des
        faux positifs sur du texte libre, et bloquer une rédaction sans recours
        coûterait plus que ce que ça protège. Le champ « Référence », lui, la
        garde.
        """
        cred = self._identifiant(notes='<p>JBSWY3DPEHPK3PXP</p>')
        self.assertIn('JBSWY3DPEHPK3PXP', cred.notes)

    def test_ordinary_notes_are_untouched(self):
        cred = self._identifiant(
            notes="<p>Le jeton est dans le tiroir de la réception.</p>")
        self.assertIn('tiroir', cred.notes)


@tagged('post_install', '-at_install')
class TestTheReviewDateStampsItself(MfaCase):
    """`mfa_last_reviewed` doit se remplir seul, ou il ne se remplira jamais."""

    def test_touching_the_factor_stamps_the_review(self):
        cred = self._identifiant()
        self.assertFalse(cred.mfa_last_reviewed)
        cred.mfa_type = 'totp'
        self.assertEqual(cred.mfa_last_reviewed, fields.Date.context_today(cred))

    def test_a_save_that_changes_nothing_does_not_stamp(self):
        """Le piège : enregistrer un formulaire réécrit TOUS les champs.

        Estampiller sur la présence du champ dans les valeurs ferait passer
        pour une revue le simple fait d'avoir ouvert la fiche et cliqué
        « enregistrer ».
        """
        cred = self._identifiant(mfa_type='totp')
        cred.mfa_last_reviewed = False
        cred.write({'mfa_type': 'totp', 'mfa_recovery': 'unknown'})
        self.assertFalse(cred.mfa_last_reviewed)

    def test_changing_the_holders_stamps_too(self):
        cred = self._identifiant(mfa_type='totp')
        cred.mfa_last_reviewed = False
        cred.mfa_holder_ids = [(4, self.porteuse.id)]
        self.assertEqual(cred.mfa_last_reviewed, fields.Date.context_today(cred))

    def test_reordering_the_holders_is_not_a_change(self):
        """Un many2many réécrit dans un autre ordre n'est pas une revue."""
        deuxieme = self.env['res.users'].create({
            'name': 'Second porteur', 'login': 'second.porteur@test.invalid'})
        cred = self._identifiant(
            mfa_type='totp',
            mfa_holder_ids=[(6, 0, [self.porteuse.id, deuxieme.id])])
        cred.mfa_last_reviewed = False
        cred.write({'mfa_holder_ids': [(6, 0, [deuxieme.id, self.porteuse.id])]})
        self.assertFalse(cred.mfa_last_reviewed)

    def test_an_explicit_date_wins_over_the_automatic_one(self):
        """On doit pouvoir dater une revue faite hier."""
        hier = fields.Date.subtract(fields.Date.context_today(self.Credential),
                                    days=1)
        cred = self._identifiant()
        cred.write({'mfa_type': 'totp', 'mfa_last_reviewed': hier})
        self.assertEqual(cred.mfa_last_reviewed, hier)

    def test_editing_something_else_leaves_the_review_alone(self):
        cred = self._identifiant(mfa_type='totp')
        cred.mfa_last_reviewed = False
        cred.write({'name': 'Nom changé', 'username': 'quelquun'})
        self.assertFalse(cred.mfa_last_reviewed)


@tagged('post_install', '-at_install')
class TestTheTwoNewFilters(MfaCase):
    """Un filtre doit ramener ce que son libellé promet."""

    def _domaine_du_filtre(self, nom):
        from lxml import etree
        vue = self.env.ref(f'{MODULE}.credential_view_search')
        noeud = etree.fromstring(vue.arch).find(f".//filter[@name='{nom}']")
        self.assertIsNotNone(noeud, f'Filtre « {nom} » absent de la vue')
        return safe_eval(noeud.get('domain'))

    def test_the_covered_filter_returns_the_covered_ones(self):
        couvert = self._identifiant('Couvert', mfa_type='totp',
                                    mfa_vault_id=self.coffre.id,
                                    mfa_recovery='vault')
        self._identifiant('Orphelin', mfa_type='totp')
        trouves = self.Credential.search(self._domaine_du_filtre('filter_mfa_covered'))
        self.assertIn(couvert, trouves)
        self.assertTrue(all(c.mfa_state == 'covered' for c in trouves))

    def test_the_sole_holder_filter_finds_factors_nobody_else_can_open(self):
        """Deux des cinq porteurs semés ne sont pas partagés, par construction."""
        seul = self.Vault.create({'name': 'Téléphone de quelqu\'un',
                                  'kind': 'device', 'shared': False})
        chez_seul = self._identifiant('Chez un seul', mfa_type='totp',
                                      mfa_vault_id=seul.id)
        partage = self._identifiant('Chez tous', mfa_type='totp',
                                    mfa_vault_id=self.coffre.id)
        trouves = self.Credential.search(
            self._domaine_du_filtre('filter_mfa_sole_holder'))
        self.assertIn(chez_seul, trouves)
        self.assertNotIn(partage, trouves)

    def test_the_seeded_vaults_carry_the_distinction(self):
        """Le filtre ne sert à rien si tous les porteurs semés sont partagés."""
        non_partages = self.Vault.search([('shared', '=', False)])
        self.assertTrue(
            non_partages,
            'Aucun porteur à détenteur unique : le filtre ne montrerait jamais rien')
