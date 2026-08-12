"""Shared fixtures for the CX bridge tests.

Every opt-in bridge answers the same question: while its switch is off,
nothing whatsoever reaches the client. That invariant is the expensive
one to break - a regression there mails real clients - so it gets a
uniform harness here and each bridge test stays about its own hook.

The helpers deliberately count what LEFT (rating requests, survey
answers, queued mails) rather than what the bridge recorded internally:
a bridge that sets its own flag but still queues a mail would pass a
flag-based assertion and fail these.
"""
from odoo.tests import TransactionCase


class CxBridgeCase(TransactionCase):
    """Base case for the opt-in bridges of the CX ecosystem."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Param = cls.env["ir.config_parameter"].sudo()
        # One guard at a time: the cooldown is neutral by default and
        # re-armed explicitly by the tests that are about it.
        cls.Param.set_param("bf_cx.solicitation_cooldown_days", "0")
        cls.cx_partner = cls.env["res.partner"].create(
            {"name": "Client Pont CX", "email": "pont-cx@example.com"}
        )

    # ------------------------------------------------------------------
    # Switches
    # ------------------------------------------------------------------
    def set_gate(self, key, enabled):
        """Set a Boolean opt-in switch the way the settings screen does.

        Settings checkboxes store the *string* 'True'/'False', never
        '1'/'0' - see param_is_true() in bf_cx.
        """
        self.Param.set_param(key, "True" if enabled else "False")

    def set_program_gate(self, key, program):
        """Set a program-valued switch (empty string means off)."""
        self.Param.set_param(key, str(program.id) if program else "")

    # ------------------------------------------------------------------
    # What actually left
    # ------------------------------------------------------------------
    def ratings_of(self, record):
        """Rating requests attached to a record (the 3-emoji rail)."""
        return self.env["rating.rating"].search(
            [("res_model", "=", record._name), ("res_id", "in", record.ids)]
        )

    def answers_of(self, program):
        """Survey answers created for a program (the invitation rail)."""
        if not program.survey_id:
            return self.env["survey.user_input"].browse()
        return self.env["survey.user_input"].search(
            [("survey_id", "=", program.survey_id.id)]
        )

    def mails_to(self, partner):
        """Mails queued or sent towards a contact, any rail."""
        return self.env["mail.mail"].search(
            [("recipient_ids", "in", partner.ids)]
        )

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------
    def assertNothingSent(self, record, partner=None, msg=None):
        """Assert no outbound ask was produced for a record/contact."""
        partner = partner or self.cx_partner
        self.assertFalse(
            self.ratings_of(record),
            msg or "no rating request may be created while the gate is off",
        )
        self.assertFalse(
            self.mails_to(partner),
            msg or "no mail may be queued while the gate is off",
        )
        self.assertFalse(
            partner.bf_cx_last_solicited,
            "an ask that never left must not stamp the solicitation date",
        )

    def assertHookIsolated(self, record, gate_method, host_call):
        """Assert the host flow survives a failing CX hook.

        Each bridge wraps its hook in try/except precisely so a CX
        hiccup can never block sending a report, losing a deal or
        marking a maintenance done. This drives that path by making the
        hook raise, then asserts the host call still returns.
        """
        from unittest.mock import patch

        boom = patch.object(
            type(record),
            gate_method,
            side_effect=RuntimeError("boom"),
            autospec=True,
        )
        with boom as mocked:
            host_call()
        self.assertTrue(
            mocked.called, "the host hook must call the CX gate"
        )
