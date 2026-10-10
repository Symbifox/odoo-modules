"""Language of an SMS invitation: the recipient's, not the sender's.

Before the source switched to English, every invitation went out in French.
The text is now chosen per recipient. The provider is never reached: the
send is captured where the bridge hands it over.
"""
from types import SimpleNamespace

from odoo.tests import tagged

from odoo.addons.bf_cx.tests.common import CxBridgeCase


@tagged("post_install", "-at_install")
class TestSmsInviteLanguage(CxBridgeCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Both languages installed: a base may carry only one of them.
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_US")
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_cx_sms"], ["fr_CA"], overwrite=True
        )
        cls.program = cls.env.ref("bf_cx.program_nps_default")
        cls.english = cls.env["res.partner"].create(
            {"name": "SMS English", "mobile": "+15145550101", "lang": "en_US"}
        )
        cls.french = cls.env["res.partner"].create(
            {"name": "SMS Français", "mobile": "+15145550102", "lang": "fr_CA"}
        )
        cls.wave = cls.env["bf.cx.wave"].create({
            "name": "Vague SMS langue",
            "program_id": cls.program.id,
            "partner_ids": [(6, 0, (cls.english | cls.french).ids)],
        })

    def setUp(self):
        super().setUp()
        self.sent = {}
        sent = self.sent

        def capture(_self, line_id, number, body):
            sent[number] = body

        self.patch(type(self.env["sms.archive.message"]), "action_send", capture)
        self.patch(
            type(self.env["bf.cx.wave"]), "_bf_cx_sms_get_line",
            lambda _self: SimpleNamespace(id=0, label="Test line"),
        )
        self.set_gate("bf_cx.sms_invite", True)
        self.program.cooldown_days = 0

    def test_each_recipient_reads_their_language(self):
        # The sender works in French: that must not decide the SMS language.
        self.wave.with_context(lang="fr_CA").action_send_sms_invites()
        self.assertTrue(self.sent["+15145550101"].startswith("Hello SMS English,"))
        self.assertTrue(self.sent["+15145550102"].startswith("Bonjour SMS Français,"))

    def test_a_language_that_is_not_installed_falls_back_to_the_sender(self):
        self.assertEqual(
            self.wave.with_context(lang="fr_CA")._bf_cx_sms_lang(
                SimpleNamespace(lang="xx_XX")
            ),
            "fr_CA",
        )
