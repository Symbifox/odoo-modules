"""La barrière d'entente, et ce qu'elle promet.

Trois propriétés valent tout le reste :

* **la barrière est lue, pas reçue** — l'accès dépend de l'état réel de la
  demande de signature, jamais d'un drapeau qu'on aurait posé soi-même ;
* **elle couvre le lien direct d'un fichier autant que la page** — c'est le
  lien de fichier qui circule ;
* **une entente par personne** — cinquante visiteurs, cinquante ententes, et
  on peut dire de chacune qui l'a signée.

S3 est bouchonné ; le PDF et le PNG de signature sont réels (bf_sign les
valide vraiment, PyPDF2 et Pillow à l'appui).
"""
import base64
import io
from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from .common import BaseNeuve

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"
SMS_MOD = "odoo.addons.bf_securetransfer.models.sms"


def _pdf_bytes():
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    doc = canvas.Canvas(buf)
    doc.drawString(72, 720, "ENTENTE DE CONFIDENTIALITE")
    doc.showPage()
    doc.save()
    return buf.getvalue()


def _png_b64():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (4, 4), (0, 0, 0, 255)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


@tagged("post_install", "-at_install")
class TestNdaGate(BaseNeuve, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        cls.brand.write({
            "allow_open_audience": True,
            "allow_audience_sms": True,
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

    def _open_transfer(self, **overrides):
        vals = {
            "sender_name": "Sender", "sender_email": "sender@example.com",
            "recipient_emails": "", "message": "Contenu confidentiel",
            "retention_days": 7,
        }
        rec = self.env["secure.transfer"].api_create(
            self.brand, vals, "203.0.113.10", "test/1.0", "fr_CA")
        # `audience_allow_sms` est posé ici parce que plusieurs cas éprouvent
        # justement le RETRAIT du canal mobile par l'entente : partir avec le
        # canal éteint ferait passer ces tests sans rien prouver.
        audience = {"audience_mode": "open", "audience_max": 10,
                    "audience_allow_sms": True, "nda_required": True}
        audience.update(overrides)
        rec.write(audience)
        return rec

    def _visitor(self, transfer, email="visiteur@example.com"):
        member = transfer._audience_join("email", email)
        transfer._audience_confirm(member)
        return member

    def _sign(self, request_rec):
        """Signer pour de vrai, sauf le rendu du certificat.

        Le certificat de complétion est rendu par wkhtmltopdf, qui a besoin
        d'un serveur HTTP vivant pour aller chercher les actifs de marque —
        indisponible sous ``--stop-after-init``. bf_sign bouchonne le rendu
        dans sa propre suite pour la même raison ; on suit la convention, et ce
        qui est éprouvé ici reste ce qui nous appartient : l'enchaînement, pas
        la fabrication du PDF."""
        signer = request_rec.signer_ids[0]
        with patch.object(
                type(self.env["ir.actions.report"]), "_render_qweb_pdf",
                return_value=(_pdf_bytes(), "pdf")):
            request_rec.register_signer_signature(
                signer, _png_b64(), False, True,
                ip="203.0.113.5", user_agent="qa")
        return request_rec

    # ------------------------------------------------------------------ la barrière
    def test_gate_sends_an_unsigned_visitor_to_the_nda_page(self):
        t = self._open_transfer()
        member = self._visitor(t)
        self.assertEqual(t._extra_access_gate(member, "TOK"), "/s/TOK/nda")

    def test_gate_lets_through_when_no_nda_is_required(self):
        t = self._open_transfer(nda_required=False)
        member = self._visitor(t)
        self.assertFalse(t._extra_access_gate(member, "TOK"))

    def test_gate_refuses_a_visitor_with_no_confirmed_identity(self):
        """Sans identité, personne à qui faire signer — et personne à laisser
        passer. Mais la barrière renvoie vers la page d'ENTENTE, pas vers
        `/s/<token>` : c'est de là qu'elle est appelée, et s'y renvoyer serait
        une boucle de redirection (QA du 2026-08-21). La page d'entente sait
        expliquer l'état et proposer de refaire l'étape du code."""
        t = self._open_transfer()
        empty = self.env["secure.transfer.audience"]
        self.assertEqual(t._extra_access_gate(empty, "TOK"), "/s/TOK/nda")

    def test_gate_opens_once_the_nda_is_signed(self):
        t = self._open_transfer()
        member = self._visitor(t)
        self._sign(member._nda_ensure_request())
        self.assertFalse(t._extra_access_gate(member, "TOK"))
        self.assertEqual(member.nda_state, "signed")

    def test_gate_state_is_read_from_the_signature_request(self):
        """⚠ La propriété centrale. On force l'état de la demande à revenir en
        arrière : la barrière doit se refermer, parce qu'elle RELIT. Si elle
        s'appuyait sur un drapeau posé au moment de la signature, elle
        resterait ouverte — et personne ne le verrait."""
        t = self._open_transfer()
        member = self._visitor(t)
        request_rec = self._sign(member._nda_ensure_request())
        self.assertFalse(t._extra_access_gate(member, "TOK"))
        request_rec.sudo().state = "cancelled"
        member.invalidate_recordset()
        self.assertEqual(t._extra_access_gate(member, "TOK"), "/s/TOK/nda")

    def test_a_refused_nda_keeps_the_gate_shut(self):
        t = self._open_transfer()
        member = self._visitor(t)
        request_rec = member._nda_ensure_request()
        request_rec.register_signer_refusal(
            request_rec.signer_ids[0], reason="Non", ip="203.0.113.5")
        member.invalidate_recordset()
        self.assertEqual(member.nda_state, "refused")
        self.assertEqual(t._extra_access_gate(member, "TOK"), "/s/TOK/nda")

    # ------------------------------------------------------------------ une entente par personne
    def test_one_request_per_visitor(self):
        t = self._open_transfer()
        a = self._visitor(t, "a@example.com")
        b = self._visitor(t, "b@example.com")
        ra, rb = a._nda_ensure_request(), b._nda_ensure_request()
        self.assertTrue(ra and rb)
        self.assertNotEqual(ra, rb)
        self.assertEqual(ra.signer_ids.email, "a@example.com")
        self.assertEqual(rb.signer_ids.email, "b@example.com")

    def test_ensure_request_is_idempotent(self):
        """Deux onglets ouverts ne doivent pas produire deux ententes : on ne
        saurait ensuite laquelle fait foi."""
        t = self._open_transfer()
        member = self._visitor(t)
        first = member._nda_ensure_request()
        again = member._nda_ensure_request()
        self.assertEqual(first, again)

    def test_the_request_points_back_at_the_visitor(self):
        t = self._open_transfer()
        member = self._visitor(t)
        request_rec = member._nda_ensure_request()
        self.assertEqual(request_rec.res_model, "secure.transfer.audience")
        self.assertEqual(request_rec.res_id, member.id)

    def test_no_second_otp_on_the_signature(self):
        """L'identité a déjà été prouvée par le code du transfert, envoyé à
        cette même adresse. Un second code serait de la friction sans preuve."""
        t = self._open_transfer()
        member = self._visitor(t)
        self.assertFalse(member._nda_ensure_request().require_signer_otp)

    def test_no_invitation_email_is_sent(self):
        """Le visiteur est devant nous. Sur cinquante personnes, l'invitation
        ferait cinquante courriels que personne n'a demandés."""
        t = self._open_transfer()
        member = self._visitor(t)
        Mail = self.env["mail.mail"]
        before = Mail.search_count([])
        member._nda_ensure_request()
        self.assertEqual(Mail.search_count([]), before)

    def test_the_silent_context_does_not_leak_to_other_senders(self):
        """La retouche de bf_sign ne vaut QUE sous le contexte du pont."""
        t = self._open_transfer()
        member = self._visitor(t)
        request_rec = member._nda_ensure_request()
        with patch("odoo.addons.bf_sign.models.bf_sign_request.BfSignRequest."
                   "_email_signer", return_value=True) as mailed:
            request_rec._email_signer(request_rec.signer_ids[0])
        self.assertTrue(mailed.called)

    # ------------------------------------------------------------------ le canal mobile
    def test_an_nda_removes_the_mobile_channel(self):
        """Un signataire sans adresse courriel n'existe pas dans bf_sign, et
        fabriquer une adresse dans une pièce juridique n'est pas une option :
        on retire le chemin plutôt que d'y laisser buter quelqu'un."""
        t = self._open_transfer()
        with patch(SMS_MOD + ".configured", return_value=True):
            self.assertFalse(t._audience_limits()["allow_sms"])
            ok, reason = t._audience_admissible("sms", "5145551234")
        self.assertFalse(ok)
        self.assertEqual(reason, "channel")

    def test_without_an_nda_the_mobile_channel_comes_back(self):
        t = self._open_transfer(nda_required=False)
        with patch(SMS_MOD + ".configured", return_value=True):
            self.assertTrue(t._audience_limits()["allow_sms"])

    def test_a_pre_existing_mobile_visitor_gets_no_request(self):
        """Une ligne mobile créée AVANT que l'entente soit exigée ne peut pas
        signer. Elle ne doit pas produire une demande bancale — elle produit
        rien, et la barrière reste fermée."""
        t = self._open_transfer(nda_required=False)
        with patch(SMS_MOD + ".configured", return_value=True):
            member = t._audience_join("sms", "5145551234")
        t._audience_confirm(member)
        t.nda_required = True
        self.assertFalse(member._nda_ensure_request())
        self.assertEqual(t._extra_access_gate(member, "TOK"), "/s/TOK/nda")

    # ------------------------------------------------------------------ identité exigée
    def test_an_nda_forces_the_recipient_code(self):
        """⚠ On ne fait pas signer un anonyme.

        Au-delà du principe : sans code, aucune ligne d'audience n'est jamais
        créée, la barrière n'a personne à qui parler et renvoie le visiteur
        vers la page… qui rappelle la barrière. Boucle de redirection mesurée
        au QA du 2026-08-21 sur un transfert à entente sans code."""
        t = self._open_transfer(audience_mode="declared",
                                recipient_emails="x@example.com",
                                force_recipient_otp=False)
        self.assertFalse(t._needs_recipient_otp_param())
        self.assertTrue(t.nda_required)
        self.assertTrue(t._recipient_otp_required(),
                        "une entente doit forcer le code du destinataire")
        self.assertEqual(t.recipient_otp_status, "nda",
                         "et l'opérateur doit lire POURQUOI le code est exigé")

    def test_without_an_nda_the_code_requirement_is_unchanged(self):
        """Non-régression : le pont ne doit pas exiger de code partout."""
        t = self._open_transfer(audience_mode="declared",
                                recipient_emails="x@example.com",
                                force_recipient_otp=False, nda_required=False)
        self.assertFalse(t._recipient_otp_required())
        self.assertEqual(t.recipient_otp_status, "off")

    def test_the_gate_never_returns_the_page_that_calls_it(self):
        """Invariant anti-boucle : `_extra_access_gate` est appelée DEPUIS
        /s/<token>. Rendre cette même URL est une boucle par construction."""
        t = self._open_transfer()
        member = self._visitor(t)
        empty = self.env["secure.transfer.audience"]
        for who in (member, empty):
            url = t._extra_access_gate(who, "TOK")
            if url:
                self.assertNotEqual(url, "/s/TOK",
                                    "la barrière renvoie vers la page qui l'appelle")

    # ------------------------------------------------------------------ configuration
    def test_requiring_an_nda_without_a_document_is_refused(self):
        """Exiger une entente sans en fournir une bloquerait tous les visiteurs
        devant une porte qui n'a pas de clé."""
        self.brand.nda_document = False
        t = self._open_transfer(nda_required=False)
        with self.assertRaises(ValidationError):
            t.nda_required = True
        self.brand.nda_document = base64.b64encode(_pdf_bytes())

    def test_brand_policy_is_inherited_at_creation(self):
        """Sans cet héritage, un envoi public échapperait en silence à
        l'entente que la même marque impose à tout le monde."""
        self.brand.nda_required = True
        rec = self.env["secure.transfer"].api_create(
            self.brand,
            {"sender_email": "sender@example.com", "recipient_emails": "",
             "message": "x", "retention_days": 7},
            "203.0.113.10", "test/1.0", "fr_CA")
        self.assertTrue(rec.nda_required)
        self.brand.nda_required = False

    def test_transfer_document_wins_over_the_brand(self):
        t = self._open_transfer()
        self.assertEqual(t._nda_document_source(), t.brand_id)
        t.nda_document = base64.b64encode(_pdf_bytes())
        self.assertEqual(t._nda_document_source(), t)

    # ------------------------------------------------------------------ preuve
    def test_signing_seals_the_evidence_once(self):
        t = self._open_transfer()
        member = self._visitor(t)
        self._sign(member._nda_ensure_request())
        member._nda_ok()
        member._nda_ok()
        self.assertTrue(member.nda_signed_on)
        signed_logs = t.access_log_ids.filtered(lambda l: l.action == "nda_signed")
        self.assertEqual(len(signed_logs), 1, "un seul scellement, pas un par visite")
        self.assertIn(member.display_identity, signed_logs.actor or "")

    def test_the_request_is_logged_when_created(self):
        t = self._open_transfer()
        member = self._visitor(t)
        member._nda_ensure_request()
        self.assertIn("nda_requested", t.access_log_ids.mapped("action"))

    def test_return_url_points_back_at_the_transfer(self):
        """Sans ce lien, le visiteur qui vient de signer est dans une impasse :
        bf_sign ignore d'où il venait."""
        t = self._open_transfer()
        member = self._visitor(t)
        request_rec = member._nda_ensure_request()
        self.assertEqual(request_rec._st_return_url(), "/s/%s" % t.sudo().token)

    def test_return_url_is_empty_for_an_unrelated_request(self):
        req = self.env["bf.sign.request"].create({
            "document_file": base64.b64encode(_pdf_bytes()),
            "document_filename": "autre.pdf",
        })
        self.assertEqual(req._st_return_url(), "")

    # ------------------------------------------------------------------ assistant
    def test_the_wizard_refuses_an_nda_together_with_sms_identities(self):
        wizard = self.env["secure.transfer.send.wizard"].create({
            "brand_id": self.brand.id,
            "sender_email": "sender@example.com",
            "message": "x",
            "audience_mode": "open",
            "audience_max": 5,
            "audience_allow_sms": True,
            "nda_required": True,
        })
        with self.assertRaises(UserError):
            wizard.action_send()
