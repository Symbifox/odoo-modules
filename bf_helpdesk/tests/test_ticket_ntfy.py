import json
from unittest.mock import patch, MagicMock

from odoo.tests import TransactionCase, tagged


@tagged("bf_helpdesk", "bf_helpdesk_ntfy")
class TestTicketNtfy(TransactionCase):
    """ntfy critical hook: per-team opt-in, threshold, escalation, no-op when URL missing."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.IConfig = cls.env["ir.config_parameter"].sudo()
        cls.alias = cls.env["mail.alias"].create({
            "alias_name": "ntfy-test",
            "alias_model_id": cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id,
        })
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Ntfy Test Team",
            "alias_id": cls.alias.id,
            "ntfy_critical_enabled": True,
            "ntfy_priority_threshold": "3",
        })
        cls.IConfig.set_param("bf_helpdesk.ntfy_webhook_url", "http://relay.test/hook/key")

    @classmethod
    def tearDownClass(cls):
        cls.IConfig.set_param("bf_helpdesk.ntfy_webhook_url", "")
        super().tearDownClass()

    def _patch_urlopen(self):
        mock_resp = MagicMock()
        mock_resp.status = 204
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = lambda s, *a: False
        return patch("odoo.addons.bf_helpdesk.models.helpdesk_ticket.urllib.request.urlopen",
                     return_value=mock_resp)

    def test_no_fire_when_team_disabled(self):
        team_off = self.env["helpdesk.ticket.team"].create({
            "name": "Ntfy Off",
            "alias_id": self.env["mail.alias"].create({
                "alias_name": "ntfy-off",
                "alias_model_id": self.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id,
            }).id,
            "ntfy_critical_enabled": False,
        })
        with self._patch_urlopen() as mock_urlopen:
            self.Ticket.create({
                "name": "low pri",
                "description": "<p>x</p>",
                "team_id": team_off.id,
                "priority": "3",
            })
        mock_urlopen.assert_not_called()

    def test_no_fire_below_threshold(self):
        with self._patch_urlopen() as mock_urlopen:
            self.Ticket.create({
                "name": "medium pri",
                "description": "<p>x</p>",
                "team_id": self.team.id,
                "priority": "1",
            })
        mock_urlopen.assert_not_called()

    def test_fires_at_threshold_priority_3(self):
        with self._patch_urlopen() as mock_urlopen:
            ticket = self.Ticket.create({
                "name": "very high",
                "description": "<p>x</p>",
                "team_id": self.team.id,
                "priority": "3",
            })
        self.assertEqual(mock_urlopen.call_count, 1)
        # Inspect payload
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "http://relay.test/hook/key")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["_id"], ticket.id)
        self.assertEqual(body["_model"], "helpdesk.ticket")
        self.assertEqual(body["_action"], "created")
        self.assertEqual(body["priority"], "3")
        self.assertEqual(body["team_id"], self.team.id)

    def test_fires_on_escalation_write(self):
        ticket = self.Ticket.create({
            "name": "starts low",
            "description": "<p>x</p>",
            "team_id": self.team.id,
            "priority": "1",
        })
        with self._patch_urlopen() as mock_urlopen:
            ticket.priority = "3"
        self.assertEqual(mock_urlopen.call_count, 1)
        body = json.loads(mock_urlopen.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(body["_action"], "escalated")

    def test_no_fire_on_unrelated_write(self):
        ticket = self.Ticket.create({
            "name": "no escalation",
            "description": "<p>x</p>",
            "team_id": self.team.id,
            "priority": "3",
        })
        # creation already fired once; reset and write something unrelated
        with self._patch_urlopen() as mock_urlopen:
            ticket.name = "renamed"
        mock_urlopen.assert_not_called()

    def test_no_fire_when_url_missing(self):
        self.IConfig.set_param("bf_helpdesk.ntfy_webhook_url", "")
        try:
            with self._patch_urlopen() as mock_urlopen:
                self.Ticket.create({
                    "name": "url missing",
                    "description": "<p>x</p>",
                    "team_id": self.team.id,
                    "priority": "3",
                })
            mock_urlopen.assert_not_called()
        finally:
            self.IConfig.set_param("bf_helpdesk.ntfy_webhook_url", "http://relay.test/hook/key")

    def test_relay_failure_swallowed(self):
        """Network failure on relay must not break ticket creation."""
        with patch(
            "odoo.addons.bf_helpdesk.models.helpdesk_ticket.urllib.request.urlopen",
            side_effect=ConnectionRefusedError("relay down"),
        ):
            ticket = self.Ticket.create({
                "name": "relay down",
                "description": "<p>x</p>",
                "team_id": self.team.id,
                "priority": "3",
            })
        self.assertTrue(ticket.id)
