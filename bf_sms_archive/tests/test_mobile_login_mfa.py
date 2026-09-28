# -*- coding: utf-8 -*-
"""La connexion mobile par mot de passe et le second facteur.

🔴 La route héritée ne regardait que `totp_enabled` : un compte à qui /web/login
aurait demandé un code par courriel (politique `auth_totp.policy`) recevait un
jeton porteur durable sur son seul mot de passe. Et le refus TOTP répondait 403
au lieu de 401 — ce qui confirmait le mot de passe.
"""
import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.bf_sms_archive.controllers import mobile_api

URL = "/bf_sms_archive/mobile/v1/login"


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestMobileLoginMfa(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="sms_mfa_user", password="sms_mfa_user_pw",
            groups="base.group_user,bf_sms_archive.group_sms_user")

    def setUp(self):
        super().setUp()
        # Le plafond de tentatives est en mémoire, par IP : on repart à zéro.
        mobile_api._login_ip_data.clear()
        mobile_api._login_id_data.clear()

    def _login(self, password="sms_mfa_user_pw"):
        r = self.url_open(URL, data=json.dumps(
            {"login": "sms_mfa_user", "password": password}),
            headers={"Content-Type": "application/json"})
        return r.status_code, r.json()

    def test_sans_second_facteur_le_jeton_est_remis(self):
        code, corps = self._login()
        self.assertEqual(code, 200)
        self.assertTrue(corps.get("token"))

    def test_un_second_facteur_exige_refuse_comme_un_mauvais_mot_de_passe(self):
        """Toute exigence que /web/login appliquerait — code par courriel compris."""
        faux = self._login(password="pas-le-bon")
        Users = type(self.env["res.users"])
        # Ce que auth_totp_mail_enforce rend quand la politique l'impose.
        with patch.object(Users, "_mfa_url", lambda self: "/web/login/totp"):
            code, corps = self._login()
        self.assertEqual(code, 401)
        self.assertNotIn("token", corps)
        self.assertEqual((code, corps), faux,
                         "la réponse ne doit pas confirmer le mot de passe")
        self.assertTrue(corps.get("auth_start"), "l'app doit savoir où apparier")
        self.assertFalse(self.env["sms.archive.mobile.device"].sudo().search(
            [("user_id", "=", self.user.id)]), "aucun appareil ne doit être émis")

    def test_un_totp_actif_repond_401_et_non_403_meme_avec_une_cle_d_api(self):
        """⚠️ Avec un TOTP, Odoo refuse déjà le MOT DE PASSE hors session
        (`_rpc_api_keys_only`) ; mais une clé d'API passe `authenticate()`. La
        route répondait alors 403 `mfa_required` — distinct du 401 — et une clé
        d'API ne doit pas ouvrir non plus un jeton porteur durable."""
        if "totp_secret" not in self.env["res.users"]._fields:
            self.skipTest("auth_totp n'est pas installé")
        self.user.sudo().totp_secret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
        cle = self.env["res.users.apikeys"].with_user(self.user)._generate(
            "rpc", "Banc MFA", fields.Datetime.now() + timedelta(days=1))
        faux = self._login(password="pas-le-bon")
        self.assertEqual(self._login(password=cle), faux)

    def test_la_politique_de_code_par_courriel_est_respectee(self):
        if not self.env["ir.module.module"].search_count([
                ("name", "=", "auth_totp_mail_enforce"), ("state", "=", "installed")]):
            self.skipTest("auth_totp_mail_enforce n'est pas installé")
        self.env["ir.config_parameter"].sudo().set_param(
            "auth_totp.policy", "employee_required")
        code, corps = self._login()
        self.assertEqual(code, 401)
        self.assertNotIn("token", corps)
