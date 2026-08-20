"""Coffre d'identifiants.

Le coffre garde des mots de passe et des clés d'API chiffrés. Ce qui doit
survivre à toute évolution du module, c'est la capacité à relire les secrets et
le verrou qui décide qui les voit.
"""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user


class TestCredentials(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.projet = cls.env['project.project'].create({'name': 'Projet d\'essai PKM'})
        cls.type_identifiant = cls.env['project.credential.type'].create({
            'name': 'Type d\'essai', 'code': 'TEST-CRED',
        })
        cls.gestionnaire = new_test_user(
            cls.env, login='pkm.gestionnaire',
            groups='base.group_user,project_knowledge_matrix.group_credential_manager',
        )
        cls.utilisateur = new_test_user(
            cls.env, login='pkm.utilisateur',
            groups='base.group_user,project_knowledge_matrix.group_credential_user',
        )
        # La règle « Identifiants : Membres du projet » filtre sur les abonnés
        # du projet. Sans cet abonnement, un utilisateur d'identifiants n'a même
        # pas le droit de lire — et les tests de masquage passeraient au vert
        # pour la mauvaise raison.
        cls.projet.message_subscribe(partner_ids=[
            cls.utilisateur.partner_id.id, cls.gestionnaire.partner_id.id,
        ])

    def _creer(self, **kwargs):
        valeurs = {
            'name': 'Identifiant d\'essai',
            'project_id': self.projet.id,
            'type_id': self.type_identifiant.id,
        }
        valeurs.update(kwargs)
        return self.env['project.credential'].create(valeurs)

    # ------------------------------------------------------------------
    # Chiffrement
    # ------------------------------------------------------------------

    def test_password_survives_a_round_trip(self):
        secret = 'mot-de-passe-très-secret-42'
        identifiant = self._creer(password=secret)
        identifiant.invalidate_recordset()
        self.assertEqual(identifiant.password, secret)

    def test_password_is_not_stored_in_clear(self):
        """Le champ stocké ne doit jamais contenir la valeur lisible.

        ``password`` n'est pas stocké ; c'est ``password_encrypted`` qui part en
        base, en sauvegarde et dans les copies de banc.
        """
        secret = 'mot-de-passe-très-secret-42'
        identifiant = self._creer(password=secret)
        chiffre = identifiant.sudo().password_encrypted
        self.assertTrue(chiffre)
        self.assertNotEqual(chiffre, secret)
        self.assertNotIn(secret, chiffre)

    def test_api_key_survives_a_round_trip(self):
        cle = 'sk-essai-0123456789'
        identifiant = self._creer(api_key=cle)
        identifiant.invalidate_recordset()
        self.assertEqual(identifiant.api_key, cle)
        self.assertNotEqual(identifiant.sudo().api_key_encrypted, cle)

    def test_a_rewrite_replaces_the_stored_secret(self):
        identifiant = self._creer(password='premier')
        premier_chiffre = identifiant.sudo().password_encrypted
        identifiant.password = 'second'
        identifiant.invalidate_recordset()
        self.assertEqual(identifiant.password, 'second')
        self.assertNotEqual(identifiant.sudo().password_encrypted, premier_chiffre)

    def test_the_mask_is_never_written_back_as_a_password(self):
        """Enregistrer un formulaire masqué ne doit pas écraser le secret.

        Un non-gestionnaire lit ``********``. Si ce jeton repartait à
        l'écriture, une simple sauvegarde de formulaire détruirait le mot de
        passe pour tout le monde.
        """
        identifiant = self._creer(password='secret-original', restricted=True)
        chiffre_avant = identifiant.sudo().password_encrypted

        identifiant.write({'password': '********'})
        identifiant.invalidate_recordset()

        self.assertEqual(identifiant.sudo().password_encrypted, chiffre_avant)
        self.assertEqual(
            identifiant.with_user(self.gestionnaire).password, 'secret-original')

    # ------------------------------------------------------------------
    # Verrou « Restreint »
    # ------------------------------------------------------------------

    def test_a_restricted_secret_is_masked_for_a_plain_user(self):
        identifiant = self._creer(password='secret-original', restricted=True)
        vu = identifiant.with_user(self.utilisateur).password
        self.assertEqual(vu, '********')

    def test_a_restricted_secret_stays_visible_to_a_manager(self):
        identifiant = self._creer(password='secret-original', restricted=True)
        vu = identifiant.with_user(self.gestionnaire).password
        self.assertEqual(vu, 'secret-original')

    def test_an_unrestricted_secret_is_visible_to_a_plain_user(self):
        identifiant = self._creer(password='secret-original', restricted=False)
        self.assertEqual(
            identifiant.with_user(self.utilisateur).password, 'secret-original')

    def test_a_plain_user_cannot_lift_the_restriction(self):
        """Sans ce verrou, un write suffit à se montrer le secret."""
        identifiant = self._creer(password='secret-original', restricted=True)
        with self.assertRaises(AccessError):
            identifiant.with_user(self.utilisateur).write({'restricted': False})

    def test_a_plain_user_cannot_set_the_restriction_either(self):
        identifiant = self._creer(password='secret-original', restricted=False)
        with self.assertRaises(AccessError):
            identifiant.with_user(self.utilisateur).write({'restricted': True})

    def test_rewriting_the_same_restriction_is_allowed(self):
        """Sauver un formulaire sans toucher au drapeau ne doit pas bloquer."""
        identifiant = self._creer(password='secret-original', restricted=True)
        identifiant.with_user(self.utilisateur).write({
            'restricted': True, 'reference': 'BILLET-1',
        })
        self.assertEqual(identifiant.reference, 'BILLET-1')

    def test_a_manager_can_lift_the_restriction(self):
        identifiant = self._creer(password='secret-original', restricted=True)
        identifiant.with_user(self.gestionnaire).write({'restricted': False})
        self.assertFalse(identifiant.restricted)

    # ------------------------------------------------------------------
    # Expiration
    # ------------------------------------------------------------------

    def test_expiration_status(self):
        aujourdhui = fields.Date.today()
        expire = self._creer(expiration_date=aujourdhui - timedelta(days=1))
        bientot = self._creer(expiration_date=aujourdhui + timedelta(days=5))
        loin = self._creer(expiration_date=aujourdhui + timedelta(days=365))

        self.assertTrue(expire.is_expired)
        self.assertTrue(bientot.is_expiring_soon)
        self.assertFalse(bientot.is_expired)
        self.assertFalse(loin.is_expiring_soon)
        self.assertFalse(loin.is_expired)

    def test_a_revoked_credential_reports_neither_expired_nor_expiring(self):
        """Un identifiant révoqué sort des deux compteurs, par conception.

        Fixer ce comportement ici rend visible tout changement d'arbitrage.
        """
        aujourdhui = fields.Date.today()
        revoque = self._creer(
            expiration_date=aujourdhui - timedelta(days=1), state='revoked')
        self.assertFalse(revoque.is_expired)
        self.assertFalse(revoque.is_expiring_soon)

    def test_a_credential_without_expiration_date_is_never_expiring(self):
        sans_date = self._creer()
        self.assertFalse(sans_date.is_expired)
        self.assertFalse(sans_date.is_expiring_soon)
