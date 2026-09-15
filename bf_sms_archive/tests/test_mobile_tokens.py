# -*- coding: utf-8 -*-
"""Les jetons d'appareil après l'audit du 2026-09-08 (S-H1, S-M2, S-M3, S-M5).

Chaque essai porte la valeur qui ferait passer l'ancienne écriture : le jeton
en clair d'un dump, un compte archivé, un appareil muet depuis trois mois, un
gestionnaire qui frappe un jeton, un endpoint hors de notre ntfy.
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.bf_sms_archive.models.push_transport import ntfy_auth_allowed


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestMobileTokens(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="sms_tok_user",
            groups="base.group_user,bf_sms_archive.group_sms_user")
        cls.manager = new_test_user(
            cls.env, login="sms_tok_manager",
            groups="base.group_user,bf_sms_archive.group_sms_manager")
        cls.Device = cls.env["sms.archive.mobile.device"]

    def _issue(self, user=None):
        device = self.Device._issue((user or self.user).id, name="Banc jetons")
        return device, device.device_token

    def test_le_jeton_est_reconnu_par_son_empreinte_puis_scelle(self):
        device, raw = self._issue()
        self.assertEqual(device.token_hash, self.Device._hash_token(raw))
        self.assertEqual(self.Device._resolve(raw), device)
        self.assertFalse(self.Device._resolve(device.token_hash),
                         "L'empreinte elle-même ne doit pas ouvrir la porte.")
        device._seal()
        self.assertFalse(device.device_token, "Le clair devait être effacé.")
        self.assertEqual(self.Device._resolve(raw), device,
                         "Scellé, l'appareil se reconnaît toujours.")

    def test_une_ligne_d_avant_migre_a_la_premiere_reconnaissance(self):
        """Un dump d'avant la 5.14.0 : jeton en clair, pas d'empreinte."""
        legacy = self.Device.sudo().create({
            "user_id": self.user.id, "name": "Ancien",
            "device_token": "jeton-en-clair-d-avant", "token_hash": False})
        self.assertEqual(self.Device._resolve("jeton-en-clair-d-avant"), legacy)
        self.assertEqual(legacy.token_hash, self.Device._hash_token("jeton-en-clair-d-avant"))
        self.assertFalse(legacy.device_token)
        self.assertEqual(self.Device._resolve("jeton-en-clair-d-avant"), legacy)

    def test_un_usager_archive_est_refuse(self):
        """🔴 S-H1 : archiver le compte doit fermer le téléphone."""
        device, raw = self._issue()
        self.assertEqual(self.Device._resolve(raw), device)
        self.user.sudo().write({"active": False})
        self.assertFalse(self.Device._resolve(raw))

    def test_un_appareil_muet_depuis_trois_mois_est_revoque(self):
        device, raw = self._issue()
        device.sudo().write({"last_seen": fields.Datetime.now() - timedelta(days=91)})
        self.assertFalse(self.Device._resolve(raw))
        self.assertFalse(device.active, "Révoqué, pas seulement refusé.")
        recent, raw2 = self._issue()
        recent.sudo().write({"last_seen": fields.Datetime.now() - timedelta(days=89)})
        self.assertEqual(self.Device._resolve(raw2), recent)

    def test_un_gestionnaire_ne_frappe_pas_de_jeton(self):
        """🔴 S-M5 : ni créer un appareil pour autrui, ni poser un jeton."""
        device, _raw = self._issue()
        Manager = self.Device.with_user(self.manager)
        with self.assertRaises(AccessError):
            Manager.create({"user_id": self.user.id, "name": "Forgé",
                            "device_token": "choisi", "token_hash": "x"})
        with self.assertRaises(AccessError):
            Manager.browse(device.id).write({"token_hash": self.Device._hash_token("choisi")})
        with self.assertRaises(AccessError):
            Manager.browse(device.id).write({"user_id": self.manager.id})
        # Renommer ou révoquer reste permis : c'est le geste attendu d'un gestionnaire.
        Manager.browse(device.id).write({"name": "Renommé", "active": False})

    def test_le_code_expire_est_jete_au_prochain_appariement(self):
        code = self.Device._issue_pending(self.user.id, name="Abandonné", challenge="x")
        stale = self.Device.sudo().search([("pending_code", "=", code)])
        stale.write({"pending_code_expiry": fields.Datetime.now() - timedelta(minutes=1)})
        self.Device._issue_pending(self.user.id, name="Suivant", challenge="y")
        self.assertFalse(stale.exists(), "La ligne en attente expirée devait disparaître.")

    def test_le_jeton_ntfy_ne_part_que_vers_notre_hote(self):
        """🔴 S-M3 : le secret serveur ne suit pas un endpoint arbitraire."""
        base = "https://ntfy.example.test"
        self.assertTrue(ntfy_auth_allowed("https://ntfy.example.test/upAbc", base))
        self.assertTrue(ntfy_auth_allowed("https://NTFY.example.test/upAbc", base))
        self.assertFalse(ntfy_auth_allowed("https://hote-attaquant.example/upAbc", base))
        self.assertFalse(ntfy_auth_allowed("https://ntfy.example.test.evil.example/x", base))
        self.assertFalse(ntfy_auth_allowed("https://ntfy.example.test/upAbc", ""))
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_sms_archive.ntfy_publish_token", "tk-banc")
        icp.set_param("bf_sms_archive.ntfy_base_url", base)
        Push = self.env["sms.archive.unifiedpush"]
        self.assertIn("Authorization", Push._auth_headers("https://ntfy.example.test/upAbc"))
        self.assertNotIn("Authorization", Push._auth_headers("https://autre.example/upAbc"))
