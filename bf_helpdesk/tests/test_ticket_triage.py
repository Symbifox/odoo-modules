import json
from unittest.mock import patch, MagicMock

from odoo.tests import TransactionCase, tagged
from odoo.exceptions import UserError


@tagged("bf_helpdesk", "bf_helpdesk_triage")
class TestTicketTriage(TransactionCase):
    """Triage IA via Anthropic Messages API."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.IConf = cls.env["ir.config_parameter"].sudo()
        cls.alias = cls.env["mail.alias"].create({
            "alias_name": "tr-test",
            "alias_model_id": cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id,
        })
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Triage Test Team",
            "alias_id": cls.alias.id,
        })

    def _ticket(self):
        return self.Ticket.create({
            "name": "Imprimante en panne au poste 3",
            "description": "<p>Bonjour, mon imprimante refuse de démarrer ce matin.</p>",
            "team_id": self.team.id,
        })

    def _patch_anthropic_ok(self, text="<p><strong>Catégorisation :</strong> matériel.</p>"):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "id": "msg_test",
            "model": "claude-test",
            "content": [{"type": "text", "text": text}],
        }).encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = lambda s, *a: False
        return patch(
            "odoo.addons.bf_helpdesk.models.helpdesk_ticket.urllib.request.urlopen",
            return_value=mock_resp,
        )

    def test_default_state_none(self):
        ticket = self._ticket()
        self.assertEqual(ticket.triage_state, "none")
        self.assertFalse(ticket.triage_suggestion_html)
        self.assertFalse(ticket.triage_last_run)

    def test_no_api_key_raises(self):
        self.IConf.set_param("bf_helpdesk.anthropic_api_key", "")
        self.IConf.set_param("bf_claude_chat.api_key_encrypted", "")
        ticket = self._ticket()
        with self.assertRaises(UserError):
            ticket.action_triage_with_claude()

    def test_triage_success_marks_done_and_stores_html(self):
        self.IConf.set_param("bf_helpdesk.anthropic_api_key", "sk-test-12345")
        ticket = self._ticket()
        with self._patch_anthropic_ok("<p>Suggestion HTML</p>") as mock_urlopen:
            ticket.action_triage_with_claude()
        self.assertEqual(ticket.triage_state, "done")
        self.assertIn("Suggestion HTML", ticket.triage_suggestion_html)
        self.assertTrue(ticket.triage_last_run)
        # Verify the request was a POST to the Anthropic endpoint with right headers
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.headers["X-api-key"], "sk-test-12345")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(len(body["messages"]), 1)
        self.assertEqual(body["messages"][0]["role"], "user")
        # Prompt mentions ticket subject + team name
        prompt = body["messages"][0]["content"]
        self.assertIn("Imprimante en panne", prompt)
        self.assertIn("Triage Test Team", prompt)

    def test_triage_failure_marks_error(self):
        self.IConf.set_param("bf_helpdesk.anthropic_api_key", "sk-test-12345")
        ticket = self._ticket()
        with patch(
            "odoo.addons.bf_helpdesk.models.helpdesk_ticket.urllib.request.urlopen",
            side_effect=ConnectionError("API unreachable"),
        ):
            ticket.action_triage_with_claude()
        self.assertEqual(ticket.triage_state, "error")
        self.assertIn("API unreachable", ticket.triage_suggestion_html)
