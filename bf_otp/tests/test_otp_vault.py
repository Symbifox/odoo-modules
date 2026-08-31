"""Ce que le serveur doit refuser, et ce qu'il ne doit pas savoir.

Ce module fait une promesse forte : Odoo ne détient aucune graine lisible et ne
peut produire aucun code. Les tests ci-dessous ne vérifient pas le chiffrement,
qui vit dans le navigateur — ils vérifient que rien, côté serveur, ne peut
défaire la promesse.
"""

import os
import re

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import file_open


def _chiffre_credible():
    """Du base64 qui ne peut pas passer pour du base32 : minuscules et « + »."""
    return "vhpN7+X+ut2MHTsYL6BUA7A5F5Rf/aa="


@tagged('post_install', '-at_install')
class OtpCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.groupe = cls.env.ref('bf_otp.group_otp_user')
        cls.alice = cls.env['res.users'].create({
            'name': 'Alice', 'login': 'alice.otp@test.invalid',
            'groups_id': [(6, 0, [cls.groupe.id, cls.env.ref('base.group_user').id])],
        })
        cls.bob = cls.env['res.users'].create({
            'name': 'Bob', 'login': 'bob.otp@test.invalid',
            'groups_id': [(6, 0, [cls.groupe.id, cls.env.ref('base.group_user').id])],
        })

    def _coffre(self, user):
        return self.env['bf.otp.vault'].with_user(user).create_my_vault(
            'c2VsMTIzNDU2Nzg5MDEy', 600000, _chiffre_credible(), 'aXYxMjM0NTY3ODkw')

    def _jeton(self, user, **kw):
        vals = {
            'name': 'compte@exemple.com', 'issuer': 'Exemple',
            'secret_cipher': _chiffre_credible(), 'secret_iv': 'aXYxMjM0NTY3ODkw',
        }
        vals.update(kw)
        return self.env['bf.otp.token'].with_user(user).save_token(vals)


@tagged('post_install', '-at_install')
class TestTheServerRefusesAPlainSeed(OtpCase):
    """Si le navigateur cessait de chiffrer, la base se remplirait de graines
    lisibles et rien ne le dirait. Cette contrainte fait du bruit tout de suite.
    """

    FORMES = (
        ('graine base32 nue', 'JBSWY3DPEHPK3PXP'),
        ('graine base32 courte', 'JBSWY3DP'),
        ('graine avec remplissage', 'MZXW6YTBOIJBSWY3DPEHPK3PXPJBSWY3=='),
        ('adresse otpauth', 'otpauth://totp/x?secret=JBSWY3DPEHPK3PXP'),
        ('export Google Authenticator', 'otpauth-migration://offline?data=CjEK'),
    )

    def setUp(self):
        super().setUp()
        self._coffre(self.alice)

    def test_a_plain_seed_never_reaches_the_column(self):
        for libelle, valeur in self.FORMES:
            with self.subTest(forme=libelle):
                with self.assertRaises(ValidationError):
                    self._jeton(self.alice, secret_cipher=valeur)

    def test_a_plain_seed_is_refused_on_a_later_write_too(self):
        tid = self._jeton(self.alice)
        token = self.env['bf.otp.token'].with_user(self.alice).browse(tid)
        with self.assertRaises(ValidationError):
            token.write({'secret_cipher': 'JBSWY3DPEHPK3PXP'})

    def test_real_ciphertext_goes_through(self):
        """Le garde ne doit pas refuser ce que le navigateur produit vraiment."""
        for chiffre in ('vhpN7+X+ut2MHTsYL6BUA7A5F5Rf/aa=',
                        'YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo=',
                        'aBcD1234+/xyzXYZ=='):
            with self.subTest(chiffre=chiffre[:12]):
                tid = self._jeton(self.alice, secret_cipher=chiffre)
                self.assertTrue(tid)

    def test_the_module_never_imports_a_crypto_library(self):
        """La promesse se lit aussi dans ce que le code ne fait PAS.

        Le jour où quelqu'un ajoute une bibliothèque de chiffrement côté
        serveur, c'est que le déchiffrement y est remonté — et la propriété
        entière du module tombe. Ce test le refuse.
        """
        interdits = re.compile(r'\b(from|import)\s+(cryptography|Crypto|nacl|pyotp|hmac)\b')
        vus = []
        for racine, _dirs, fichiers in os.walk(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ):
            if 'tests' in racine or '__pycache__' in racine:
                continue
            for f in fichiers:
                if f.endswith('.py'):
                    chemin = os.path.join(racine, f)
                    with open(chemin, encoding='utf-8') as fh:
                        if interdits.search(fh.read()):
                            vus.append(f)
        self.assertFalse(
            vus,
            "Une bibliothèque de chiffrement est apparue côté serveur (%s) : "
            "ce module tient sa promesse en ne pouvant PAS déchiffrer." % vus,
        )


@tagged('post_install', '-at_install')
class TestOneVaultPerPersonAndNobodyElsesBusiness(OtpCase):
    """Un coffre n'appartient qu'à une personne, et les autres ne le voient pas."""

    def test_a_second_vault_is_refused(self):
        """Écraser un coffre rendrait ses jetons illisibles sans le dire."""
        self._coffre(self.alice)
        with self.assertRaises(UserError):
            self._coffre(self.alice)

    def test_bob_does_not_see_alices_vault(self):
        self._coffre(self.alice)
        vu = self.env['bf.otp.vault'].with_user(self.bob).get_my_vault()
        self.assertFalse(vu, "Bob voit le coffre d'Alice")

    def test_an_absent_vault_answers_false_and_not_none(self):
        """⚠️ `False`, pas `None` : XML-RPC ne sait pas encoder None.

        Un client XML-RPC — un script, une future application mobile — recevrait
        « cannot marshal None unless allow_none is enabled », une erreur opaque
        qui ne dit rien du coffre. Le navigateur passe par JSON-RPC et s'en
        moquerait, d'où un défaut qui ne se serait vu qu'ailleurs.
        """
        self.assertIs(
            self.env['bf.otp.vault'].with_user(self.bob).get_my_vault(), False)

    def test_bob_does_not_see_alices_tokens(self):
        self._coffre(self.alice)
        self._jeton(self.alice, name='secret-alice@exemple.com')
        self._coffre(self.bob)
        chez_bob = self.env['bf.otp.token'].with_user(self.bob).load_my_tokens()
        self.assertEqual(chez_bob, [])

    def test_bob_cannot_read_alices_tokens_by_search(self):
        """La règle d'enregistrement doit tenir hors des façades, pas seulement
        dedans : une façade se contourne par un appel RPC direct au modèle."""
        self._coffre(self.alice)
        self._jeton(self.alice)
        trouves = self.env['bf.otp.token'].with_user(self.bob).search([])
        self.assertFalse(trouves)

    def test_bob_cannot_delete_a_token_of_alice(self):
        self._coffre(self.alice)
        tid = self._jeton(self.alice)
        self._coffre(self.bob)
        with self.assertRaises(ValidationError):
            self.env['bf.otp.token'].with_user(self.bob).delete_token(tid)

    def test_a_token_always_lands_in_the_callers_own_vault(self):
        """`save_token` ne doit pas laisser choisir le coffre de destination.

        Sans cette garantie, un appel RPC forgé déposerait un jeton dans le
        coffre de quelqu'un d'autre — inutile pour le lire, mais suffisant pour
        y semer du bruit ou faire croire à une fuite.
        """
        va = self._coffre(self.alice)
        self._coffre(self.bob)
        tid = self.env['bf.otp.token'].with_user(self.bob).save_token({
            'name': 'intrus', 'secret_cipher': _chiffre_credible(),
            'secret_iv': 'aXY=', 'vault_id': va,
        })
        jeton = self.env['bf.otp.token'].browse(tid)
        self.assertEqual(jeton.user_id, self.bob,
                         "Le vault_id envoyé par le client a été suivi")


@tagged('post_install', '-at_install')
class TestImportAndShape(OtpCase):

    def setUp(self):
        super().setUp()
        self._coffre(self.alice)

    def _entree(self, nom, issuer='Exemple'):
        return {'name': nom, 'issuer': issuer,
                'secret_cipher': _chiffre_credible(), 'secret_iv': 'aXY='}

    def test_import_creates_and_reports(self):
        res = self.env['bf.otp.token'].with_user(self.alice).import_tokens(
            [self._entree('un@x.com'), self._entree('deux@x.com')])
        self.assertEqual(res, {'created': 2, 'skipped': 0})

    def test_reimporting_the_same_export_does_not_double(self):
        """Le geste le plus probable de qui doute que ça ait marché : recommencer."""
        entrees = [self._entree('un@x.com'), self._entree('deux@x.com')]
        self.env['bf.otp.token'].with_user(self.alice).import_tokens(entrees)
        res = self.env['bf.otp.token'].with_user(self.alice).import_tokens(entrees)
        self.assertEqual(res, {'created': 0, 'skipped': 2})
        self.assertEqual(
            len(self.env['bf.otp.token'].with_user(self.alice).load_my_tokens()), 2)

    def test_a_duplicate_inside_one_import_is_caught_too(self):
        res = self.env['bf.otp.token'].with_user(self.alice).import_tokens(
            [self._entree('un@x.com'), self._entree('un@x.com')])
        self.assertEqual(res, {'created': 1, 'skipped': 1})

    def test_the_same_account_at_two_issuers_is_not_a_duplicate(self):
        res = self.env['bf.otp.token'].with_user(self.alice).import_tokens(
            [self._entree('compte@exemple.com', 'Google'),
             self._entree('compte@exemple.com', 'Microsoft')])
        self.assertEqual(res['created'], 2)

    def test_importing_without_a_vault_is_refused(self):
        with self.assertRaises(ValidationError):
            self.env['bf.otp.token'].with_user(self.bob).import_tokens(
                [self._entree('x@x.com')])

    def test_only_six_seven_or_eight_digits(self):
        for n in (4, 5, 9, 10):
            with self.subTest(chiffres=n):
                with self.assertRaises(ValidationError):
                    self._jeton(self.alice, digits=n, name='d%s' % n)

    def test_a_totp_period_must_be_positive(self):
        with self.assertRaises(ValidationError):
            self._jeton(self.alice, period=0)

    def test_deleting_a_vault_that_still_holds_tokens_is_refused(self):
        """Supprimer un coffre plein perd des graines sans le dire."""
        self._jeton(self.alice)
        coffre = self.env['bf.otp.vault'].with_user(self.alice).search([])
        with self.assertRaises(UserError):
            coffre.unlink()


@tagged('post_install', '-at_install')
class TestTheComfortFields(OtpCase):
    """Favoris, usage, et le rattachement client/projet."""

    def setUp(self):
        super().setUp()
        self._coffre(self.alice)

    def test_a_new_token_is_not_a_favourite(self):
        tid = self._jeton(self.alice)
        self.assertFalse(self.env['bf.otp.token'].browse(tid).favorite)

    def test_the_star_toggles_both_ways(self):
        tid = self._jeton(self.alice)
        T = self.env['bf.otp.token'].with_user(self.alice)
        self.assertIs(T.toggle_favorite(tid), True)
        self.assertIs(T.toggle_favorite(tid), False)

    def test_bob_cannot_star_a_token_of_alice(self):
        tid = self._jeton(self.alice)
        with self.assertRaises(ValidationError):
            self.env['bf.otp.token'].with_user(self.bob).toggle_favorite(tid)

    def test_favourites_come_first_in_the_default_order(self):
        """L'ordre du modèle doit déjà les remonter, avant tout tri du client."""
        a = self._jeton(self.alice, name='aaa@x.com')
        z = self._jeton(self.alice, name='zzz@x.com')
        self.env['bf.otp.token'].with_user(self.alice).toggle_favorite(z)
        ordre = self.env['bf.otp.token'].with_user(self.alice).search([]).ids
        self.assertEqual(ordre[0], z, "Le favori n'est pas remonté en tête")
        self.assertIn(a, ordre)

    def test_using_a_token_stamps_the_date(self):
        tid = self._jeton(self.alice)
        T = self.env['bf.otp.token'].with_user(self.alice)
        self.assertFalse(T.browse(tid).last_used)
        T.touch_token(tid)
        self.assertTrue(T.browse(tid).last_used)

    def test_bob_cannot_stamp_a_token_of_alice(self):
        tid = self._jeton(self.alice)
        with self.assertRaises(ValidationError):
            self.env['bf.otp.token'].with_user(self.bob).touch_token(tid)

    def test_a_token_carries_its_client_and_project(self):
        client = self.env['res.partner'].create({'name': 'Client de test'})
        projet = self.env['project.project'].create({'name': 'Projet de test'})
        tid = self._jeton(self.alice, partner_id=client.id, project_id=projet.id)
        lu = self.env['bf.otp.token'].with_user(self.alice).load_my_tokens()
        ligne = [x for x in lu if x['id'] == tid][0]
        self.assertEqual(ligne['partner_id'][1], 'Client de test')
        self.assertEqual(ligne['project_id'][1], 'Projet de test')

    def test_deleting_the_client_does_not_take_the_token_with_it(self):
        """🔴 `ondelete='set null'` et pas `cascade`.

        Un jeton n'est pas une donnée du client : c'est un accès qu'on détient.
        Supprimer une fiche contact ne doit pas faire disparaître en silence le
        deuxième facteur d'un compte qui, lui, existe toujours.
        """
        client = self.env['res.partner'].create({'name': 'Client éphémère'})
        tid = self._jeton(self.alice, partner_id=client.id)
        client.unlink()
        jeton = self.env['bf.otp.token'].browse(tid)
        self.assertTrue(jeton.exists(), "Le jeton est parti avec le client")
        self.assertFalse(jeton.partner_id)


@tagged('post_install', '-at_install')
class TestTheLookupIsNotAUniversalSearch(OtpCase):
    """`name_search_targets` est appelable par RPC : sa liste blanche est tout.

    Sans elle, ce serait un `name_search` universel sur n'importe quel modèle,
    sous l'identité de qui appelle — un outil d'exploration offert à qui a un
    compte.
    """

    def test_the_two_allowed_models_answer(self):
        self.env['res.partner'].create({'name': 'Cible Alpha'})
        self.env['project.project'].create({'name': 'Cible Beta'})
        T = self.env['bf.otp.token'].with_user(self.alice)
        self.assertTrue(T.name_search_targets('res.partner', 'Cible Alpha'))
        self.assertTrue(T.name_search_targets('project.project', 'Cible Beta'))

    def test_every_other_model_is_refused(self):
        T = self.env['bf.otp.token'].with_user(self.alice)
        for modele in ('res.users', 'ir.config_parameter', 'bf.otp.token',
                       'bf.otp.vault', 'mail.message'):
            with self.subTest(modele=modele):
                with self.assertRaises(ValidationError):
                    T.name_search_targets(modele, 'a')


@tagged('post_install', '-at_install')
class TestPasskeys(OtpCase):
    """Les clés d'accès : ce que le serveur en garde, et ce qu'il ne peut pas.

    Le serveur ne vérifie AUCUNE signature WebAuthn ici, et c'est voulu : il
    n'accorde aucun droit sur la foi d'une clé d'accès — la session Odoo fait
    déjà ce travail. Une clé qui ne serait pas la bonne ne déchiffrera rien,
    ce qui est le seul verrou dont on ait besoin.
    """

    def setUp(self):
        super().setUp()
        self._coffre(self.alice)

    def _cle(self, user, nom='MacBook', cid='Y3JlZC1hbGljZQ'):
        return self.env['bf.otp.vault'].with_user(user).add_credential(
            nom, cid, 'c2VsLXByZi0xMjM0NTY3OA',
            'vhpN7+X+ut2MHTsYL6BUA7A5F5Rf/aa=', 'aXYxMjM0NTY3ODkw')

    def test_a_passkey_lands_on_my_vault(self):
        rid = self._cle(self.alice)
        cred = self.env['bf.otp.credential'].browse(rid)
        self.assertEqual(cred.user_id, self.alice)

    def test_the_vault_hands_its_passkeys_to_the_browser(self):
        self._cle(self.alice, nom='Clé jaune')
        v = self.env['bf.otp.vault'].with_user(self.alice).get_my_vault()
        self.assertEqual(len(v['credentials']), 1)
        self.assertEqual(v['credentials'][0]['name'], 'Clé jaune')
        # Le scellé et le sel voyagent : le navigateur en a besoin, et ni l'un
        # ni l'autre n'ouvre quoi que ce soit sans l'authentificateur.
        self.assertTrue(v['credentials'][0]['wrapped_secret'])
        self.assertTrue(v['credentials'][0]['prf_salt'])

    def test_bob_does_not_see_alices_passkeys(self):
        self._cle(self.alice)
        self._coffre(self.bob)
        v = self.env['bf.otp.vault'].with_user(self.bob).get_my_vault()
        self.assertEqual(v['credentials'], [])
        self.assertFalse(self.env['bf.otp.credential'].with_user(self.bob).search([]))

    def test_bob_cannot_remove_a_passkey_of_alice(self):
        rid = self._cle(self.alice)
        self._coffre(self.bob)
        with self.assertRaises(UserError):
            self.env['bf.otp.vault'].with_user(self.bob).remove_credential(rid)
        self.assertTrue(self.env['bf.otp.credential'].browse(rid).exists())

    def test_the_same_passkey_cannot_be_registered_twice(self):
        """Sinon une même clé rendrait deux scellés, dont un périmé en silence."""
        self._cle(self.alice, cid='DUPLIQUEE')
        with self.assertRaises(Exception):
            self._cle(self.alice, nom='Encore', cid='DUPLIQUEE')

    def test_a_plain_vault_key_is_refused_in_the_wrapper(self):
        """Le scellement se fait dans le navigateur : rien de lisible n'entre."""
        with self.assertRaises(ValidationError):
            self.env['bf.otp.vault'].with_user(self.alice).add_credential(
                'Mauvaise', 'Y2lk', 'c2Vs', 'JBSWY3DPEHPK3PXP', 'aXY=')

    def test_removing_a_passkey_leaves_the_vault_and_its_tokens(self):
        """Retirer une clé d'accès ne doit rien coûter d'autre.

        C'est le geste qu'on fait quand on perd un appareil, souvent dans
        l'urgence : il ne doit surtout pas emporter le coffre avec lui.
        """
        tid = self._jeton(self.alice)
        rid = self._cle(self.alice)
        self.env['bf.otp.vault'].with_user(self.alice).remove_credential(rid)
        self.assertTrue(self.env['bf.otp.token'].browse(tid).exists())
        self.assertTrue(
            self.env['bf.otp.vault'].with_user(self.alice).get_my_vault())

    def test_deleting_the_vault_takes_its_passkeys(self):
        """Un scellé sans coffre n'est qu'un déchet qui ressemble à un secret."""
        rid = self._cle(self.alice)
        coffre = self.env['bf.otp.vault'].with_user(self.alice).search([])
        coffre.unlink()
        self.assertFalse(self.env['bf.otp.credential'].browse(rid).exists())

    def test_a_passkey_without_a_vault_is_refused(self):
        with self.assertRaises(UserError):
            self._cle(self.bob)
