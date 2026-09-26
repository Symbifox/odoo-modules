"""Identite de liaison de l'annuaire, servie dans la politique.

Pourquoi ces tests existent. L'avant-poste LDAP d'Authentik NE SERT PAS les
recherches anonymes : sans compte de service, sssd ne resout aucun utilisateur
et la session est refusee avant meme qu'un mot de passe soit demande. Le mot de
passe de ce compte doit donc voyager jusqu'au poste — et ne jamais dormir en
clair dans la base, ni ressortir par une lecture de fiche.

Ce qui est garde ici :

- **Rien en clair dans la colonne.** Comme la phrase de passe de disque.
- **Le champ de saisie ne rend jamais la valeur.** Il prend, il ne donne pas.
- **Un enregistrement a vide n'efface pas.** Le formulaire renvoie toujours ""
  puisque le compute rend "" : traiter ca comme un effacement viderait le mot de
  passe a chaque sauvegarde de la fiche.
- **La politique servie porte les deux valeurs**, faute de quoi le poste ecrit
  un sssd.conf sans identite et n'ouvre aucune session.
"""

import os
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_policy.models import escrow

_MDP = "jeton-de-liaison-factice-0123456789"


def _a_key():
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


@tagged("post_install", "-at_install")
class TestLdapBind(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({
            "name": "Foxy LDAP", "website": "https://ldap.example",
        })
        cls.org = cls.env["bf.policy.org"].create({
            "company_id": cls.company.id, "domain": "ldap.example",
            "login_mode": "sssd",
            "ldap_uri": "ldaps://auth.example.com:636",
            "ldap_base_dn": "DC=example,DC=com",
            "ldap_bind_dn": "cn=svc,ou=users,DC=example,DC=com",
        })
        cls.user = cls.env["res.users"].create({
            "name": "Riley", "login": "riley@ldap.example",
            "company_id": cls.company.id, "company_ids": [(4, cls.company.id)],
        })
        cls.key = _a_key()

    def _with_key(self):
        return patch.dict(os.environ, {escrow.ENV_VAR: self.key})

    def test_la_colonne_ne_porte_pas_le_mot_de_passe(self):
        with self._with_key():
            self.org.ldap_bind_password = _MDP
            self.org.flush_recordset()
        self.env.cr.execute(
            "SELECT ldap_bind_password_enc FROM bf_policy_org WHERE id = %s",
            (self.org.id,))
        stocke = self.env.cr.fetchone()[0]
        self.assertTrue(stocke)
        self.assertNotIn(_MDP, stocke)

    def test_le_champ_de_saisie_ne_rend_jamais_la_valeur(self):
        with self._with_key():
            self.org.ldap_bind_password = _MDP
            self.org.invalidate_recordset()
            self.assertEqual(self.org.ldap_bind_password, "")

    def test_enregistrer_a_vide_n_efface_pas(self):
        with self._with_key():
            self.org.ldap_bind_password = _MDP
            avant = self.org.sudo().ldap_bind_password_enc
            self.org.ldap_bind_password = ""
            self.assertEqual(self.org.sudo().ldap_bind_password_enc, avant)

    def test_la_relecture_rend_le_clair(self):
        with self._with_key():
            self.org.ldap_bind_password = _MDP
            self.assertEqual(self.org._read_ldap_bind_password(), _MDP)

    def test_la_relecture_sans_depot_rend_vide(self):
        # Une organisation par societe (contrainte d'unicite) : il en faut donc
        # une seconde pour exercer le cas « aucun depot ».
        societe = self.env["res.company"].create({"name": "Foxy sans depot"})
        autre = self.env["bf.policy.org"].create({
            "company_id": societe.id, "domain": "vide.example",
        })
        self.assertEqual(autre._read_ldap_bind_password(), "")

    def test_la_politique_servie_porte_l_identite_de_liaison(self):
        with self._with_key():
            self.org.ldap_bind_password = _MDP
            login = self.org.get_policy_json(self.user)["install"]["login"]
        self.assertEqual(login["mode"], "sssd")
        self.assertEqual(login["bind_dn"], "cn=svc,ou=users,DC=example,DC=com")
        self.assertEqual(login["bind_password"], _MDP)
        self.assertEqual(login["ldap_uri"], "ldaps://auth.example.com:636")

    def test_une_cle_illisible_ne_casse_pas_la_politique(self):
        """Le poste a sa propre garde ; la politique doit quand meme partir."""
        with self._with_key():
            self.org.ldap_bind_password = _MDP
        # Plus de cle : le dechiffrement echoue, et on rend "" sans lever.
        with patch.dict(os.environ, {escrow.ENV_VAR: _a_key()}):
            login = self.org.get_policy_json(self.user)["install"]["login"]
        self.assertEqual(login["bind_password"], "")
        self.assertEqual(login["bind_dn"], "cn=svc,ou=users,DC=example,DC=com")
