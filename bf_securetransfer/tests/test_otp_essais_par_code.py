"""Le code à six chiffres a son propre compteur d'essais.

Le défi vit dans la session ; le seul frein était un limiteur en mémoire par
(IP, transfert). Un même cookie rejoué depuis vingt IP obtenait vingt fois
huit essais, et autant de fois le nombre de workers. Ce qui est rejoué ici :
une vraie demande de code, puis des essais ratés venus chacun d'une IP
différente (en-têtes de mandataire avec `proxy_mode`), puis le BON code.
"""
from unittest.mock import patch

from odoo.tests import HttpCase, tagged
from odoo.tools import config

from odoo.addons.bf_securetransfer.controllers import main as st_main

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"
TRANSFER = "odoo.addons.bf_securetransfer.models.secure_transfer.SecureTransfer"


@tagged("post_install", "-at_install")
class TestOtpEssaisParCode(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        cls.brand.sudo().write({"watermark_downloads": False})
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")
        icp.set_param("bf_securetransfer.require_recipient_otp", "0")
        icp.set_param("bf_securetransfer.require_sender_otp", "0")

    def setUp(self):
        super().setUp()
        # La base du banc n'a pas fr_CA ; la langue des courriels n'est pas
        # l'objet de cet essai.
        langue = patch(TRANSFER + "._lang_for_email", return_value="en_US")
        langue.start()
        self.addCleanup(langue.stop)
        for limiter in (st_main._token_fail_limiter, st_main._otp_fail_limiter,
                        st_main._otp_send_limiter, st_main._otp_cooldown_limiter):
            with limiter._lock:
                limiter._data.clear()
        self.transfer = self.env["secure.transfer"].api_create(
            self.brand, {
                "sender_name": "Test Sender",
                "sender_email": "sender@example.com",
                "recipient_emails": "dest@example.com",
                "message": "Bonjour",
                "retention_days": 7,
            }, "203.0.113.10", "test-suite/1.0", "en_US")
        self.transfer._register_file("doc.pdf", 4096)
        key = self.transfer.file_ids.s3_key
        with patch(S3_MOD + ".head_object",
                   return_value={"size": 4096, "etag": "etag"}):
            self.transfer.action_finalize()
        self.transfer.sudo().force_recipient_otp = True
        self.token = self.transfer.sudo().token
        self.env.flush_all()
        self.assertTrue(key)

    def _post(self, path, data, ip):
        hote = "127.0.0.1:%s" % self.http_port()
        with patch.dict(config.options, {"proxy_mode": True}):
            return self.url_open(path, data=data, allow_redirects=False,
                                 headers={"X-Forwarded-For": ip,
                                          "X-Forwarded-Host": hote})

    def test_vingt_ip_un_meme_cookie(self):
        codes = []
        with patch(TRANSFER + "._otp_email",
                   side_effect=lambda email, code, kind: codes.append(code)):
            demande = self._post("/s/%s/otp-request" % self.token,
                                 {"email": "dest@example.com"}, "198.51.100.1")
        self.assertEqual(demande.status_code, 303, demande.text[:600])
        self.assertEqual(len(codes), 1, "aucun code n'est parti")
        bon = codes[0]
        faux = "%06d" % ((int(bon) + 1) % 1_000_000)
        for i in range(20):
            self._post("/s/%s/otp-verify" % self.token, {"code": faux},
                       "198.51.100.%d" % (10 + i))
        rep = self._post("/s/%s/otp-verify" % self.token, {"code": bon},
                         "198.51.100.99")
        self.assertIn("otp_error", rep.headers.get("Location", ""),
                      "le bon code passe encore après vingt essais ratés "
                      "venus d'IP différentes")
        membre = self.transfer.sudo().audience_ids.filtered(
            lambda a: a.email == "dest@example.com")
        self.assertEqual(membre.otp_verify_fails, membre.MAX_OTP_VERIFY_FAILS)

    def test_le_bon_code_passe_sous_le_plafond(self):
        codes = []
        with patch(TRANSFER + "._otp_email",
                   side_effect=lambda email, code, kind: codes.append(code)):
            self._post("/s/%s/otp-request" % self.token,
                       {"email": "dest@example.com"}, "198.51.100.1")
        bon = codes[0]
        faux = "%06d" % ((int(bon) + 1) % 1_000_000)
        for i in range(4):
            self._post("/s/%s/otp-verify" % self.token, {"code": faux},
                       "198.51.100.%d" % (10 + i))
        rep = self._post("/s/%s/otp-verify" % self.token, {"code": bon},
                         "198.51.100.99")
        self.assertNotIn("otp_error", rep.headers.get("Location", ""))
