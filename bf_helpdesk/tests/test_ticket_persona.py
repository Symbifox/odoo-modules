from odoo.tests import TransactionCase, tagged


@tagged("bf_helpdesk", "bf_helpdesk_persona")
class TestTicketPersona(TransactionCase):
    """Persona panel on the ticket form: computed M2O + related fields + open action."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.Persona = cls.env["contact.persona"]
        cls.partner_with = cls.env["res.partner"].create({
            "name": "Persona Owner",
            "email": "po@test.invalid",
        })
        cls.partner_without = cls.env["res.partner"].create({
            "name": "No Persona",
            "email": "np@test.invalid",
        })
        cls.persona = cls.Persona.create({
            "partner_id": cls.partner_with.id,
            "addressing_style": "tu",
            "preferred_salutation": "Bonjour Pat",
            "closing_formula": "Bien à toi",
            "tone_summary": "warm",
            "our_tone_summary": "warm",
            "payer_quality": "excellent",
        })
        cls.alias = cls.env["mail.alias"].create({
            "alias_name": "persona-test",
            "alias_model_id": cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id,
        })
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Persona Test Team",
            "alias_id": cls.alias.id,
        })

    def _ticket(self, partner):
        return self.Ticket.create({
            "name": "persona test",
            "description": "<p>x</p>",
            "team_id": self.team.id,
            "partner_id": partner.id,
        })

    def test_persona_id_resolves_for_partner_with_persona(self):
        ticket = self._ticket(self.partner_with)
        self.assertEqual(ticket.persona_id, self.persona)

    def test_persona_id_empty_when_no_persona(self):
        ticket = self._ticket(self.partner_without)
        self.assertFalse(ticket.persona_id)

    def test_related_fields_mirror_persona(self):
        ticket = self._ticket(self.partner_with)
        self.assertEqual(ticket.persona_addressing_style, "tu")
        self.assertEqual(ticket.persona_preferred_salutation, "Bonjour Pat")
        self.assertEqual(ticket.persona_closing_formula, "Bien à toi")
        self.assertEqual(ticket.persona_tone_summary, "warm")
        self.assertEqual(ticket.persona_our_tone_summary, "warm")
        self.assertEqual(ticket.persona_payer_quality, "excellent")

    def test_action_open_persona_existing(self):
        ticket = self._ticket(self.partner_with)
        action = ticket.action_open_persona()
        self.assertEqual(action["res_model"], "contact.persona")
        self.assertEqual(action["res_id"], self.persona.id)

    def test_action_open_persona_create_when_missing(self):
        ticket = self._ticket(self.partner_without)
        action = ticket.action_open_persona()
        self.assertEqual(action["res_model"], "contact.persona")
        self.assertNotIn("res_id", action)
        self.assertEqual(action["context"]["default_partner_id"],
                         self.partner_without.id)

    def test_persona_id_not_stored(self):
        """Compute is non-stored — ensures swapping partners updates the field."""
        ticket = self._ticket(self.partner_with)
        self.assertEqual(ticket.persona_id, self.persona)
        ticket.partner_id = self.partner_without
        ticket.invalidate_recordset(["persona_id"])
        self.assertFalse(ticket.persona_id)
