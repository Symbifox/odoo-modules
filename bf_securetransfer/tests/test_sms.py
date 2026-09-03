"""Couche SMS VoIP.ms : troncature en OCTETS UTF-8 et refus propre hors config.

VoIP.ms mesure le corps en octets, pas en caractères. Une troncature sur les
caractères laisse passer un corps français jusqu'à 320 octets, que l'API refuse
avec ``sms_toolong`` — c'est l'origine des échecs du journal d'envoi.
"""
from odoo.addons.bf_securetransfer.models import sms
from odoo.tests import TransactionCase, tagged

from .common import BaseNeuve


@tagged("post_install", "-at_install")
class TestSecureTransferSms(BaseNeuve, TransactionCase):

    # ------------------------------------------------------------- troncature
    def test_short_body_is_untouched(self):
        for body in ("", "Hello", "Consentement.Québec : code 123456"):
            self.assertEqual(sms.truncate_utf8(body), body)

    def test_none_body_becomes_empty_string(self):
        """send() passe le corps tel quel à urlencode : None y lèverait."""
        self.assertEqual(sms.truncate_utf8(None), "")

    def test_ascii_boundary_is_160_bytes(self):
        self.assertEqual(len(sms.truncate_utf8("a" * 160).encode("utf-8")), 160)
        self.assertEqual(len(sms.truncate_utf8("a" * 161).encode("utf-8")), 160)

    def test_accented_body_is_measured_in_bytes_not_characters(self):
        """160 « é » = 320 octets. L'ancien code les laissait passer intacts et
        VoIP.ms répondait sms_toolong ; le corps doit tomber à 160 octets."""
        body = "é" * 160
        self.assertEqual(len(body), 160)
        self.assertEqual(len(body.encode("utf-8")), 320)
        out = sms.truncate_utf8(body)
        self.assertEqual(len(out.encode("utf-8")), 160)
        self.assertEqual(out, "é" * 80)

    def test_truncation_never_splits_a_character(self):
        """Couper sur une tranche d'octets peut scinder un caractère multi-octet
        et produire un corps qui ne décode plus. L'octet de queue est écarté."""
        for body in ("a" * 159 + "é", "a" * 158 + "😀", "€" * 60):
            out = sms.truncate_utf8(body)
            self.assertLessEqual(len(out.encode("utf-8")), 160)
            # round-trip : le résultat est du texte valide, pas des octets orphelins
            self.assertEqual(out.encode("utf-8").decode("utf-8"), out)

    def test_real_otp_body_fits(self):
        """Le corps d'OTP réellement envoyé doit rester loin du plafond, sinon
        un code de vérification arriverait amputé."""
        text = ("Consentement.Québec : votre code de vérification est 123456 "
                "(valide 15 minutes).")
        self.assertLess(len(text.encode("utf-8")), 160)
        self.assertEqual(sms.truncate_utf8(text), text)

    # ---------------------------------------------------------- normalisation
    def test_normalize_na_accepts_human_formats(self):
        for raw in ("8198298888", "819-829-8888", "+1 (819) 829-8888",
                    "1-819-829-8888"):
            self.assertEqual(sms.normalize_na(raw), "8198298888")

    def test_normalize_na_refuses_non_nanp(self):
        for raw in ("", None, "12345", "+33 1 42 68 53 00", "81982988889"):
            self.assertIsNone(sms.normalize_na(raw))

    # --------------------------------------------------------------- garde-fou
    def test_send_returns_false_when_not_configured(self):
        """send() ne lève JAMAIS : l'appelant retombe sur le courriel, pour
        qu'un destinataire ne soit jamais enfermé dehors."""
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.sms_enabled", "0")
        self.assertFalse(sms.configured(self.env))
        self.assertFalse(sms.send(self.env, "8198298888", "test"))

    def test_send_refuses_an_invalid_destination(self):
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.sms_enabled", "1")
        icp.set_param("bf_securetransfer.sms_did", "8198298888")
        # Pas d'identifiants en test : configured() est faux, donc l'appel sort
        # avant tout réseau. Le numéro invalide est vérifié par normalize_na.
        self.assertIsNone(sms.normalize_na("allo"))
        self.assertFalse(sms.send(self.env, "allo", "test"))
