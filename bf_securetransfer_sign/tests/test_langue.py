"""La preuve de l'entente se lit dans la langue de l'expéditeur.

La signature se constate depuis la page publique, dans la langue du visiteur.
Le journal d'accès et le fil du transfert, eux, se lisent par l'expéditeur :
dans la langue de qui a créé le transfert, sinon dans celle de la société.
Le titre de la page d'entente, qu'Odoo ne traduit jamais dans un <title>, se
compose dans la langue du visiteur.
"""
import base64
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from .test_nda_gate import _pdf_bytes, _png_b64


@tagged("post_install", "-at_install")
class TestLangueDeLEntente(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_US")
        cls.env.company.partner_id.lang = "fr_CA"
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        cls.brand.write({
            "allow_open_audience": True,
            "audience_max_default": 50,
            "audience_domains": False,
            "nda_required": False,
            "nda_document": base64.b64encode(_pdf_bytes()),
            "nda_filename": "entente.pdf",
        })
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.require_recipient_otp", "0")
        cls.anglophone = cls.env["res.users"].create({
            "name": "Expéditeur en_US", "login": "expediteur.nda@example.test",
            "lang": "en_US", "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })

    def _transfert(self, createur=None):
        Transfer = self.env["secure.transfer"]
        if createur:
            Transfer = Transfer.with_user(createur).sudo()
        rec = Transfer.api_create(
            self.brand,
            {"sender_name": "Sender", "sender_email": "sender@example.com",
             "recipient_emails": "", "message": "Contenu confidentiel", "retention_days": 7},
            "203.0.113.10", "test/1.0", "fr_CA")
        rec = self.env["secure.transfer"].browse(rec.id)
        rec.write({"audience_mode": "open", "audience_max": 10, "nda_required": True})
        return rec

    def _signer(self, transfer):
        """Le visiteur signe depuis la page publique, en anglais."""
        member = transfer._audience_join("email", "visiteur@example.com")
        transfer._audience_confirm(member)
        member = member.with_context(lang="en_US")
        request_rec = member._nda_ensure_request()
        with patch.object(
                type(self.env["ir.actions.report"]), "_render_qweb_pdf",
                return_value=(_pdf_bytes(), "pdf")):
            request_rec.register_signer_signature(
                request_rec.signer_ids[0], _png_b64(), False, True,
                ip="203.0.113.5", user_agent="qa")
        member._nda_ok()
        notes = " ".join(transfer.access_log_ids.mapped("note") + [""])
        fil = " ".join(c or "" for c in transfer.message_ids.mapped("body"))
        return notes, fil

    def test_sans_createur_humain_dans_la_langue_de_la_societe(self):
        notes, fil = self._signer(self._transfert())
        self.assertIn("Entente de confidentialité à signer", notes)
        self.assertIn("Entente de confidentialité signée", notes)
        self.assertIn("Entente de confidentialité signée par", fil)
        self.assertNotIn("NDA signed", notes + fil)

    def test_dans_la_langue_de_l_expediteur(self):
        notes, fil = self._signer(self._transfert(self.anglophone))
        self.assertIn("NDA to sign", notes)
        self.assertIn("NDA signed, SHA-256 fingerprint", notes)
        self.assertIn("NDA signed by", fil)

    def test_le_titre_de_la_page_suit_le_visiteur(self):
        from odoo.addons.bf_securetransfer_sign.controllers.main import SecureTransferSignPortal
        transfer = self._transfert()
        controleur = SecureTransferSignPortal()
        for lang, attendu in (("fr_CA", "Entente de confidentialité : "),
                              ("en_US", "Non-disclosure agreement: ")):
            ctx = controleur._nda_context(
                transfer.with_context(lang=lang), transfer.sudo().token, lang, False)
            self.assertTrue(ctx["page_title"].startswith(attendu), ctx["page_title"])
        # Et la page l'affiche : un <title> écrit en dur n'est jamais traduit.
        arch = self.env.ref("bf_securetransfer_sign.page_nda").arch_db
        self.assertIn('<title t-esc="page_title"/>', arch)
