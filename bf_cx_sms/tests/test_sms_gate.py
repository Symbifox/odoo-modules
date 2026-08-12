"""SMS invitations: the switch and the per-click cap.

These tests deliberately stop short of a successful send. Unlike mail,
which queues into mail.mail and can be inspected offline, the SMS rail
calls VoIP.ms for real the moment it reaches action_send - so the send
path is exercised only up to the last guard before the provider. What
is pinned here is everything that decides whether that call happens at
all.
"""
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.bf_cx.tests.common import CxBridgeCase
from odoo.addons.bf_cx_sms.models.bf_cx_wave import MAX_SMS_PER_CLICK


@tagged("post_install", "-at_install")
class TestSmsInviteGate(CxBridgeCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.program = cls.env.ref("bf_cx.program_nps_default")
        # A contact reachable by SMS only: no email, one mobile number.
        cls.sms_partner = cls.env["res.partner"].create(
            {"name": "Contact SMS CX", "mobile": "+15145550199"}
        )
        cls.wave = cls.env["bf.cx.wave"].create(
            {
                "name": "Vague SMS pont CX",
                "program_id": cls.program.id,
                "partner_ids": [(6, 0, cls.sms_partner.ids)],
            }
        )

    def test_gate_off_refuses_loudly(self):
        """Off must be an explicit refusal, not a silent no-op."""
        self.set_gate("bf_cx.sms_invite", False)
        with self.assertRaises(UserError):
            self.wave.action_send_sms_invites()
        self.assertFalse(self.wave.user_input_ids)

    def test_absent_parameter_is_off(self):
        self.Param.search([("key", "=", "bf_cx.sms_invite")]).unlink()
        with self.assertRaises(UserError):
            self.wave.action_send_sms_invites()

    def test_contacts_with_email_are_not_texted(self):
        """The SMS rail exists for contacts the email rail cannot reach."""
        self.set_gate("bf_cx.sms_invite", True)
        self.sms_partner.email = "contact-sms@example.com"
        with self.assertRaises(UserError):
            self.wave.action_send_sms_invites()
        self.assertFalse(self.wave.user_input_ids)

    def test_cooldown_blocks_before_the_provider(self):
        self.set_gate("bf_cx.sms_invite", True)
        self.Param.set_param("bf_cx.solicitation_cooldown_days", "30")
        self.program.cooldown_days = 0
        self.sms_partner._bf_cx_mark_solicited()
        with self.assertRaises(UserError):
            self.wave.action_send_sms_invites()
        self.assertFalse(self.wave.user_input_ids)

    def test_per_click_cap_stays_under_the_provider_limit(self):
        """VoIP.ms throttles around 27 SMS a day: the cap must stay small.

        A silent bump of this constant would spend the whole daily
        allowance of a line that also carries 1:1 client conversations.
        """
        self.assertLessEqual(MAX_SMS_PER_CLICK, 5)
