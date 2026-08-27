from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_sign_privacy")
class TestConsentBfSign(TransactionCase):
    """Un consentement doit pouvoir se signer avec la signature maison."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Sujet signataire",
            "email": "sujet@example.com",
        })
        cls.purpose = cls.env["privacy.purpose"].create({
            "code": "test_bf_sign",
            "name": "Finalité de test",
        })
        cls.consent = cls.env["privacy.consent"].create({
            "subject_partner_id": cls.partner.id,
            "purpose_id": cls.purpose.id,
        })

    def test_consent_inherits_the_sign_mixin(self):
        """Le consentement expose le compteur et l'action du mixin."""
        self.assertIn("sign_request_count", self.consent._fields)
        self.assertTrue(hasattr(self.consent, "action_send_for_signature"))

    def test_default_signer_is_the_subject(self):
        """Le signataire par défaut est la personne concernée.

        ⚠️ Le repli du mixin cherche `partner_id`, qui n'existe pas sur
        `privacy.consent` : sans cette surcharge, la demande partirait sans
        signataire.
        """
        signers = self.consent._sign_default_signers()
        self.assertEqual(len(signers), 1)
        self.assertEqual(signers[0]["partner_id"], self.partner.id)
        self.assertEqual(signers[0]["email"], "sujet@example.com")

    def test_report_ref_points_at_the_consent_certificate(self):
        ref = self.consent._sign_report_ref()
        self.assertTrue(self.env.ref(ref, raise_if_not_found=False))

    def test_withdrawn_consent_cannot_be_sent_for_signature(self):
        """La garde est au point d'ENVOI, pas au retour.

        Une fois la demande partie, le signataire a le lien : la refuser au
        retour ne rappellerait rien.
        """
        self.consent.write({"status": "withdrawn"})
        with self.assertRaises(UserError):
            self.consent.action_send_bf_sign()

    def test_subject_without_email_is_refused(self):
        self.partner.email = False
        with self.assertRaises(UserError):
            self.consent.action_send_bf_sign()

    def test_completion_grants_the_consent_and_files_evidence(self):
        """La complétion fait ce que fait la voie LibreSign, à l'identique."""
        request = self.env["bf.sign.request"].create({
            "name": "TEST-SIGN-1",
            "res_model": "privacy.consent",
            "res_id": self.consent.id,
        })
        attachment = self.env["ir.attachment"].create({
            "name": "consentement-signe.pdf",
            "datas": b"JVBERi0xLjQK",  # entête PDF en base64, suffisant ici
        })
        request.write({"signed_attachment_id": attachment.id})

        self.consent._sign_on_signed(request)

        self.assertEqual(self.consent.bf_sign_status, "completed")
        self.assertEqual(self.consent.collection_method, "signature")
        self.assertEqual(self.consent.status, "granted")
        evidence = self.env["privacy.consent.evidence"].search([
            ("consent_id", "=", self.consent.id),
            ("evidence_type", "=", "pdf_signed"),
        ])
        self.assertTrue(evidence)
        self.assertIn("bf_sign", evidence[0].note)

    def test_refusing_to_sign_does_not_refuse_the_consent(self):
        """Décliner la signature n'est pas refuser le consentement.

        La personne peut très bien l'accorder par le portail ensuite.
        """
        request = self.env["bf.sign.request"].create({
            "name": "TEST-SIGN-2",
            "res_model": "privacy.consent",
            "res_id": self.consent.id,
        })
        before = self.consent.status
        self.consent._sign_on_refused(request, self.env["bf.sign.signer"], "pas maintenant")
        self.assertEqual(self.consent.bf_sign_status, "refused")
        self.assertEqual(self.consent.status, before)

    def test_sign_models_are_classifiable(self):
        allowed = self.env["privacy.document.classification"]._privacy_classifiable_models()
        self.assertIn("bf.sign.request", allowed)
        self.assertIn("res.partner", allowed)
