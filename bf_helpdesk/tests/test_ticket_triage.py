from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

# Path to the value-object method we patch to avoid real network calls.
_CHAT_PATH = "odoo.addons.bf_llm.models.bf_llm._ResolvedProvider.chat"


def _envelope(**overrides):
    """Build a normalized bf_llm envelope, overridable per test."""
    env = {
        "ok": True,
        "text": "",
        "data": None,
        "tool_calls": [],
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "model": "claude-opus-4-8",
        "provider": "anthropic",
        "stop_reason": "end_turn",
        "degraded": [],
        "error": None,
        "raw": None,
    }
    env.update(overrides)
    return env


@tagged("bf_helpdesk", "bf_helpdesk_triage")
class TestTicketTriage(TransactionCase):
    """Triage IA routed through the bf_llm gateway."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.Provider = cls.env["bf.llm.provider"].sudo()
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

    def _make_default_provider(self):
        """Create the single enabled default LLM provider for the call to resolve."""
        # Drop any seeded/leftover default so the write-time single-default guard
        # leaves ours as the unique default.
        self.Provider.search([("is_default", "=", True)]).write({"is_default": False})
        return self.Provider.create({
            "name": "Test Provider",
            "provider": "anthropic",
            "model": "claude-opus-4-8",
            "model_triage": "claude-opus-4-8",
            "enabled": True,
            "is_default": True,
        })

    def test_default_state_none(self):
        ticket = self._ticket()
        self.assertEqual(ticket.triage_state, "none")
        self.assertFalse(ticket.triage_suggestion_html)
        self.assertFalse(ticket.triage_last_run)

    def test_no_provider_soft_notice(self):
        # Ensure there is no enabled default provider to resolve.
        self.Provider.search([]).write({"is_default": False, "enabled": False})
        ticket = self._ticket()
        result = ticket.action_triage_with_claude()
        # Graceful degradation: a soft client notification, no state change.
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("tag"), "display_notification")
        self.assertEqual(result["params"]["type"], "warning")
        self.assertEqual(ticket.triage_state, "none")
        self.assertFalse(ticket.triage_last_run)

    def test_triage_success_marks_done_and_stores_html(self):
        self._make_default_provider()
        ticket = self._ticket()
        env = _envelope(text="<p>Suggestion HTML</p>")
        with patch(_CHAT_PATH, return_value=env) as mock_chat:
            ticket.action_triage_with_claude()
        self.assertEqual(ticket.triage_state, "done")
        self.assertIn("Suggestion HTML", ticket.triage_suggestion_html)
        self.assertTrue(ticket.triage_last_run)
        # The gateway received a single user message carrying the triage prompt.
        self.assertEqual(mock_chat.call_count, 1)
        _args, kwargs = mock_chat.call_args
        messages = kwargs.get("messages") or (_args[0] if _args else None)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")
        prompt = messages[0]["content"]
        self.assertIn("Imprimante en panne", prompt)
        self.assertIn("Triage Test Team", prompt)

    def test_triage_failure_marks_error(self):
        self._make_default_provider()
        ticket = self._ticket()
        env = _envelope(
            ok=False, text="", error="API unreachable", stop_reason="error",
        )
        with patch(_CHAT_PATH, return_value=env):
            ticket.action_triage_with_claude()
        self.assertEqual(ticket.triage_state, "error")
        self.assertIn("API unreachable", ticket.triage_suggestion_html)
        self.assertTrue(ticket.triage_last_run)
