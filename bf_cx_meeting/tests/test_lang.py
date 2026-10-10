"""The feedback email reads in each contact's language.

The template is written in English in the source and its French lives in
i18n/fr_CA.po: an English contact no longer receives it in French.
"""
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_cx.tests.common import CxBridgeCase


@tagged("post_install", "-at_install")
class TestFeedbackEmailLanguage(TransactionCase):

    def test_email_template_reads_in_each_language(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"]._load_module_terms(["bf_cx_meeting"], ["fr_CA"])
        template = self.env.ref("bf_cx_meeting.mail_template_meeting_rating")
        self.assertIn("Hello", template.with_context(lang="en_US").body_html)
        self.assertIn("Bonjour", template.with_context(lang="fr_CA").body_html)
        self.assertEqual(
            template.with_context(lang="fr_CA").subject,
            "Comment s'est passée notre rencontre ?",
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
        cls.env["ir.module.module"]._load_module_terms(["bf_cx_meeting"], ["fr_CA"])
        cls.env.company.partner_id.lang = "fr_CA"
        cls.cx_partner.lang = False
        project = cls.env["project.project"].create(
            {"name": "Projet langue CX", "partner_id": cls.cx_partner.id}
        )
        cls.meeting = cls.env["meeting.record"].create({
            "name": "Rencontre langue CX", "project_id": project.id,
            "date": "2026-07-20 14:00:00", "meeting_type": "video",
        })

    def test_email_in_the_company_language(self):
        self.set_gate("bf_cx.meeting_feedback", True)
        self.meeting.with_context(lang=None)._bf_cx_maybe_request_feedback()
        mails = self.mails_to(self.cx_partner)
        self.assertEqual(len(mails), 1)
        self.assertIn("Bonjour", mails.body_html)
        self.assertNotIn("Hello", mails.body_html)
