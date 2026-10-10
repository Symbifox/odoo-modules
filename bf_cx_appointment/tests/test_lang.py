"""The feedback email reads in each contact's language.

The template is written in English in the source and its French lives in
i18n/fr_CA.po: an English contact no longer receives it in French.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_cx.tests.common import CxBridgeCase


@tagged("post_install", "-at_install")
class TestFeedbackEmailLanguage(TransactionCase):

    def test_email_template_reads_in_each_language(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"]._load_module_terms(["bf_cx_appointment"], ["fr_CA"])
        template = self.env.ref("bf_cx_appointment.mail_template_booking_rating")
        self.assertIn("Hello", template.with_context(lang="en_US").body_html)
        self.assertIn("Bonjour", template.with_context(lang="fr_CA").body_html)
        self.assertEqual(
            template.with_context(lang="fr_CA").subject,
            "Comment s'est passé votre rendez-vous ?",
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
        cls.env["ir.module.module"]._load_module_terms(["bf_cx_appointment"], ["fr_CA"])
        cls.env.company.partner_id.lang = "fr_CA"
        cls.cx_partner.lang = False
        booking_type = cls.env["resource.booking.type"].create({"name": "Type langue CX"})
        resource = cls.env["resource.resource"].create({"name": "Ressource langue CX"})
        combination = cls.env["resource.booking.combination"].create(
            {"resource_ids": [(6, 0, resource.ids)]}
        )
        booking_type.combination_rel_ids = [(0, 0, {"combination_id": combination.id})]
        stop = fields.Datetime.now() - timedelta(days=1)
        cls.booking = cls.env["resource.booking"].create({
            "type_id": booking_type.id,
            "combination_id": combination.id,
            "combination_auto_assign": False,
            "partner_ids": [(6, 0, cls.cx_partner.ids)],
            "start": stop - timedelta(hours=1),
            "stop": stop,
        })
        cls.booking.state = "confirmed"

    def test_email_in_the_company_language(self):
        self.set_gate("bf_cx.appointment_feedback", True)
        # The scheduled job: no language in the context.
        self.env["resource.booking"].with_context(lang=None)._bf_cx_request_post_booking_feedback()
        mails = self.mails_to(self.cx_partner)
        self.assertEqual(len(mails), 1)
        self.assertIn("Bonjour", mails.body_html)
        self.assertNotIn("Hello", mails.body_html)
