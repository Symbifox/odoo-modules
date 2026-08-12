from odoo.tests import TransactionCase, tagged


@tagged("bf_helpdesk", "bf_helpdesk_imap")
class TestImapGateway(TransactionCase):
    """message_new must drop autoresponder loops before creating tickets."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.alias = cls.env["mail.alias"].create({
            "alias_name": "imap-test",
            "alias_model_id": cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id,
        })
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "IMAP Test Team",
            "alias_id": cls.alias.id,
        })

    def _msg(self, **overrides):
        msg = {
            "subject": "Real customer issue",
            "from": "customer@example.com",
            "to": "imap-test@example.com",
            "body": "<p>Hello, please help.</p>",
            "message_id": "<msg-1@example.com>",
            "custom_headers": {},
        }
        msg.update(overrides)
        return msg

    def test_autoresponder_auto_submitted_dropped(self):
        msg = self._msg(custom_headers={"Auto-Submitted": "auto-replied"})
        result = self.Ticket.with_context(default_team_id=self.team.id).message_new(msg)
        self.assertFalse(result, "Auto-Submitted: auto-replied must drop the message")

    def test_autoresponder_x_auto_dropped(self):
        msg = self._msg(custom_headers={"X-AutoReply": "yes"})
        result = self.Ticket.with_context(default_team_id=self.team.id).message_new(msg)
        self.assertFalse(result)

    def test_autoresponder_precedence_bulk_dropped(self):
        msg = self._msg(custom_headers={"Precedence": "bulk"})
        result = self.Ticket.with_context(default_team_id=self.team.id).message_new(msg)
        self.assertFalse(result)

    def test_autoresponder_subject_prefix_dropped(self):
        for subj in [
            "Out of office: I'm away",
            "Automatic reply: thanks",
            "Réponse automatique : absent",
            "Undeliverable: bounce",
        ]:
            with self.subTest(subject=subj):
                msg = self._msg(subject=subj)
                result = self.Ticket.with_context(default_team_id=self.team.id).message_new(msg)
                self.assertFalse(result, f"Subject {subj!r} must drop")

    def test_real_message_creates_ticket(self):
        msg = self._msg()
        result = self.Ticket.with_context(default_team_id=self.team.id).message_new(msg)
        self.assertTrue(result.id)
        self.assertEqual(result.partner_email, "customer@example.com")

    def test_auto_submitted_no_does_not_drop(self):
        msg = self._msg(custom_headers={"Auto-Submitted": "no"})
        result = self.Ticket.with_context(default_team_id=self.team.id).message_new(msg)
        self.assertTrue(result.id, "Auto-Submitted: no must NOT drop")
