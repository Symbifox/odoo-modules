"""Clé de chiffrement lue hors de la base, ancienne clé en base encore lue."""
import os
from unittest.mock import patch

from cryptography.fernet import Fernet

from odoo.tests import TransactionCase, tagged

MODELES = ("privacy.docuseal.config",) + (("privacy.libresign.config",) if "privacy_consent" == "privacy_consent" else ())


@tagged("post_install", "-at_install")
class TestCleHorsBase(TransactionCase):

    def setUp(self):
        super().setUp()
        self.ICP = self.env["ir.config_parameter"].sudo()
        self.ancienne = Fernet.generate_key().decode()
        self.ICP.set_param("privacy_consent.encryption_key", self.ancienne)
        self.neuve = Fernet.generate_key().decode()

    def test_hors_base_chiffre_et_relit_l_ancien(self):
        for nom in MODELES:
            Config = self.env[nom]
            ancien_chiffre = Fernet(self.ancienne.encode()).encrypt(b"secret-avant").decode()
            with patch.dict(os.environ, {"BF_PRIVACY_CONSENT_FERNET_KEY": self.neuve}):
                self.assertEqual(Config._decrypt_value(ancien_chiffre), "secret-avant")
                neuf = Config._encrypt_value("secret-apres")
            # Le neuf se lit avec la seule clé hors base, pas avec celle de la base.
            self.assertEqual(Fernet(self.neuve.encode()).decrypt(neuf.encode()), b"secret-apres")
            with self.assertRaises(Exception):
                Fernet(self.ancienne.encode()).decrypt(neuf.encode())

    def test_aucune_cle_generee_en_base_quand_hors_base(self):
        self.ICP.search([("key", "=", "privacy_consent.encryption_key")]).unlink()
        with patch.dict(os.environ, {"BF_PRIVACY_CONSENT_FERNET_KEY": self.neuve}):
            self.env[MODELES[0]]._encrypt_value("x")
        self.assertFalse(self.ICP.get_param("privacy_consent.encryption_key"))

    def test_sans_cle_hors_base_rien_ne_change(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BF_PRIVACY_CONSENT_FERNET_KEY", None)
            chiffre = self.env[MODELES[0]]._encrypt_value("x")
        self.assertEqual(Fernet(self.ancienne.encode()).decrypt(chiffre.encode()), b"x")

    def test_odoo_conf_d_abord_cle_reprise_telle_quelle(self):
        from odoo.tools import config
        # La clé posée dans odoo.conf est celle du paramètre : l'ancien se lit, le neuf
        # se lit encore avec la clé de la base (rien à re-chiffrer, retour arrière sûr).
        ancien = Fernet(self.ancienne.encode()).encrypt(b"avant").decode()
        with patch.dict(config.options, {"privacy_consent_fernet_key": self.ancienne}):
            with self.assertNoLogs("odoo.addons.privacy_consent", level="WARNING"):
                Config = self.env[MODELES[0]]
                self.assertEqual(Config._decrypt_value(ancien), "avant")
                neuf = Config._encrypt_value("apres")
        self.assertEqual(Fernet(self.ancienne.encode()).decrypt(neuf.encode()), b"apres")

    def test_repli_sur_la_base_avertit(self):
        from odoo.tools import config
        with patch.dict(config.options, {"privacy_consent_fernet_key": ""}):
            os.environ.pop(self.env[MODELES[0]]._CLE_ENV, None)
            with self.assertLogs("odoo.addons.privacy_consent", level="WARNING") as journal:
                self.env[MODELES[0]]._encrypt_value("x")
        self.assertIn("privacy_consent.encryption_key", "\n".join(journal.output))
