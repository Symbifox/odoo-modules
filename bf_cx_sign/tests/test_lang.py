"""Languages of the post-signature feedback.

The cooldown note lands in the chatter, for the team: it is written in the
sender's language, never in the language of the signer whose signature
triggered it (often from the public signing page). The email itself is
rendered in the signer's language.
"""
from odoo.tests import tagged

from odoo.addons.bf_cx.tests.common import CxBridgeCase


@tagged("post_install", "-at_install")
class TestSignFeedbackLanguages(CxBridgeCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Both languages installed: a base may carry only one of them.
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_US")
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_cx_sign"], ["fr_CA"], overwrite=True
        )
        cls.sender = cls.env["res.users"].create({
            "name": "Sender fr_CA",
            "login": "sender.cx.sign@example.test",
            "lang": "fr_CA",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.cx_partner.lang = "en_US"
        # create_uid follows the environment's user, even in sudo.
        created = cls.env["bf.sign.request"].with_user(cls.sender).sudo().create({
            "name": "Entente langue CX",
            "signature_method": "native_ses",
            "signing_order": "parallel",
        })
        cls.request = cls.env["bf.sign.request"].browse(created.id)
        cls.env["bf.sign.signer"].create({
            "request_id": cls.request.id,
            "name": cls.cx_partner.name,
            "email": cls.cx_partner.email,
            "partner_id": cls.cx_partner.id,
            "sequence": 1,
        })
        cls.request.state = "signed"

    def test_cooldown_note_reads_in_the_sender_language(self):
        self.Param.set_param("bf_cx.solicitation_cooldown_days", "30")
        self.set_gate("bf_cx.sign_feedback", True)
        self.cx_partner._bf_cx_mark_solicited()
        self.assertEqual(self.request.create_uid, self.sender)
        # The last signer signs from the public page, in English.
        self.request.with_context(lang="en_US")._bf_cx_maybe_request_feedback()
        note = self.request.message_ids.sorted("id")[-1:]
        self.assertIn("Demande de feedback non envoyée", note.body)

    def test_reader_language_is_the_sender_language(self):
        self.assertEqual(self.request._bf_cx_reader_lang(), "fr_CA")
        self.sender.lang = "en_US"
        self.assertEqual(self.request._bf_cx_reader_lang(), "en_US")

    def test_email_template_reads_in_each_language(self):
        template = self.env.ref("bf_cx_sign.mail_template_sign_rating")
        self.assertIn("Hello", template.with_context(lang="en_US").body_html)
        self.assertIn("Bonjour", template.with_context(lang="fr_CA").body_html)
        self.assertEqual(
            template.with_context(lang="fr_CA").subject,
            "Comment s'est passée votre signature ?",
        )


@tagged("post_install", "-at_install")
class TestContactWithoutLanguage(CxBridgeCase):
    """A contact without a language reads the company's, not the source.

    In a scheduled job the context has no language, and the template would
    then render in the English source. Before the switch it rendered in
    French: the company's language keeps that.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["ir.module.module"]._load_module_terms(["bf_cx_sign"], ["fr_CA"])
        cls.env.company.partner_id.lang = "fr_CA"
        cls.cx_partner.lang = False
        cls.unsigned_lang_request = cls.env["bf.sign.request"].create({
            "name": "Entente sans langue CX",
            "signature_method": "native_ses",
            "signing_order": "parallel",
        })
        cls.env["bf.sign.signer"].create({
            "request_id": cls.unsigned_lang_request.id,
            "name": cls.cx_partner.name,
            "email": cls.cx_partner.email,
            "partner_id": cls.cx_partner.id,
            "sequence": 1,
        })
        cls.unsigned_lang_request.state = "signed"

    def test_email_in_the_company_language(self):
        self.set_gate("bf_cx.sign_feedback", True)
        self.unsigned_lang_request.with_context(lang=None)._bf_cx_maybe_request_feedback()
        mails = self.mails_to(self.cx_partner)
        self.assertEqual(len(mails), 1)
        self.assertIn("Bonjour", mails.body_html)
        self.assertNotIn("Hello", mails.body_html)
