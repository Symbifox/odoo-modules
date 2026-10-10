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
        self.env["ir.module.module"]._load_module_terms(["bf_cx_hosting"], ["fr_CA"])
        template = self.env.ref("bf_cx_hosting.mail_template_hosting_maintenance_rating")
        self.assertIn("Hello", template.with_context(lang="en_US").body_html)
        self.assertIn("Bonjour", template.with_context(lang="fr_CA").body_html)
        self.assertEqual(
            template.with_context(lang="fr_CA").subject,
            "Comment s'est passée la maintenance de votre service ?",
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
        cls.env["ir.module.module"]._load_module_terms(["bf_cx_hosting"], ["fr_CA"])
        cls.env.company.partner_id.lang = "fr_CA"
        cls.cx_partner.lang = False
        software = cls.env["hosting.software"].create({
            "name": "Logiciel langue CX", "code": "cx_lang_test", "software_type": "self_hosted",
        })
        service = cls.env["hosting.service"].create({
            "name": "Service langue CX", "partner_id": cls.cx_partner.id,
            "software_id": software.id, "state": "active",
            "environment": "production", "version_policy": "latest",
        })
        cls.schedule = cls.env["hosting.maintenance.schedule"].create({
            "name": "Maintenance langue CX", "service_id": service.id,
            "frequency": "monthly", "maintenance_type": "security_patch",
        })

    def test_email_in_the_company_language(self):
        self.set_gate("bf_cx.hosting_feedback", True)
        self.schedule.with_context(lang=None)._bf_cx_maybe_request_feedback()
        mails = self.mails_to(self.cx_partner)
        self.assertEqual(len(mails), 1)
        self.assertIn("Bonjour", mails.body_html)
        self.assertNotIn("Hello", mails.body_html)
