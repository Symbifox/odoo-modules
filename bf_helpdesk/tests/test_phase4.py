from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("bf_helpdesk", "bf_helpdesk_phase4")
class TestPhase4(TransactionCase):
    """SLA, macros, auto-tag, auto-ack — Phase 4 features."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.Tag = cls.env["helpdesk.ticket.tag"]
        cls.Macro = cls.env["helpdesk.macro"]
        cls.AutoTag = cls.env["helpdesk.auto.tag.rule"]
        cls.alias = cls.env["mail.alias"].create({
            "alias_name": "p4-test",
            "alias_model_id": cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id,
        })
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "P4 Test Team",
            "alias_id": cls.alias.id,
            "sla_response_hours": 4.0,
            "sla_resolve_hours": 48.0,
        })

    # -------------------------- SLA ----------------------------

    def test_sla_deadlines_computed(self):
        ticket = self.Ticket.create({
            "name": "SLA test",
            "description": "<p>x</p>",
            "team_id": self.team.id,
        })
        self.assertTrue(ticket.sla_response_deadline)
        self.assertTrue(ticket.sla_resolve_deadline)
        self.assertEqual(
            ticket.sla_response_deadline,
            ticket.create_date + timedelta(hours=4),
        )

    def test_sla_no_deadline_when_team_zero(self):
        team = self.env["helpdesk.ticket.team"].create({
            "name": "No SLA Team",
            "alias_id": self.env["mail.alias"].create({
                "alias_name": "no-sla",
                "alias_model_id": self.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id,
            }).id,
        })
        ticket = self.Ticket.create({
            "name": "no sla",
            "description": "<p>x</p>",
            "team_id": team.id,
        })
        self.assertFalse(ticket.sla_response_deadline)
        self.assertFalse(ticket.sla_resolve_deadline)

    def test_sla_response_breach_when_deadline_passed_no_response(self):
        ticket = self.Ticket.create({
            "name": "old ticket",
            "description": "<p>x</p>",
            "team_id": self.team.id,
        })
        # Force deadline into the past
        ticket.sla_response_deadline = fields.Datetime.now() - timedelta(hours=1)
        ticket.invalidate_recordset(["sla_response_breach"])
        self.assertTrue(ticket.sla_response_breach)

    # -------------------------- Macros -------------------------

    def test_macro_apply_posts_to_chatter(self):
        macro = self.Macro.create({
            "name": "Bonjour standard",
            "body_html": "<p>Bonjour, merci pour votre demande.</p>",
        })
        ticket = self.Ticket.create({
            "name": "macro test",
            "description": "<p>x</p>",
            "team_id": self.team.id,
        })
        wizard = self.env["helpdesk.macro.apply.wizard"].create({
            "ticket_id": ticket.id,
            "macro_id": macro.id,
        })
        wizard.action_apply()
        msgs = ticket.message_ids.filtered(
            lambda m: m.message_type == "comment" and "merci pour votre demande" in (m.body or "").lower()
        )
        self.assertTrue(msgs, "Macro body must be posted to the chatter")

    # -------------------------- Auto-tag -----------------------

    def test_auto_tag_applies_matching_rule(self):
        tag = self.Tag.create({"name": "P4-urgent"})
        self.AutoTag.create({
            "name": "urgent keyword",
            "team_id": self.team.id,
            "keyword_regex": r"\burgent\b",
            "tag_id": tag.id,
        })
        ticket = self.Ticket.create({
            "name": "Demande urgente — imprimante",
            "description": "<p>besoin urgent</p>",
            "team_id": self.team.id,
        })
        self.assertIn(tag, ticket.tag_ids, "Auto-tag rule should add the tag")

    def test_auto_tag_no_match_no_tag(self):
        tag = self.Tag.create({"name": "P4-not-applied"})
        self.AutoTag.create({
            "name": "different keyword",
            "team_id": self.team.id,
            "keyword_regex": r"\bxyz\b",
            "tag_id": tag.id,
        })
        ticket = self.Ticket.create({
            "name": "Demande standard",
            "description": "<p>standard</p>",
            "team_id": self.team.id,
        })
        self.assertNotIn(tag, ticket.tag_ids)

    def test_invalid_regex_raises_at_creation(self):
        tag = self.Tag.create({"name": "P4-bad-regex"})
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.AutoTag.create({
                "name": "bad regex",
                "team_id": self.team.id,
                "keyword_regex": r"[unclosed",
                "tag_id": tag.id,
            })

    # -------------------------- Auto-ack -----------------------

    def test_auto_ack_template_exists(self):
        tpl = self.env.ref("bf_helpdesk.mail_template_public_form_ack")
        self.assertEqual(tpl.model, "helpdesk.ticket")
