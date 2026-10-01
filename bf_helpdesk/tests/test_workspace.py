from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("bf_helpdesk", "bf_helpdesk_workspace")
class TestWorkspace(TransactionCase):
    """macros v2, vue 360 du client, file de l'équipe."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Ticket = cls.env["helpdesk.ticket"]
        model = cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Soutien 360",
            "alias_id": cls.env["mail.alias"].create({
                "alias_name": "soutien-360", "alias_model_id": model.id,
            }).id,
            "sla_resolve_hours": 16.0,
        })
        cls.agent = new_test_user(
            cls.env, login="ws-agent", name="Agente Essai",
            groups="base.group_user,helpdesk_mgmt.group_helpdesk_user_team",
        )
        cls.team.user_ids = [(4, cls.agent.id)]
        cls.company = cls.env["res.partner"].create({
            "name": "Coop Essai", "is_company": True,
        })
        cls.contact = cls.env["res.partner"].create({
            "name": "Jane Doe", "email": "jane@example.com",
            "parent_id": cls.company.id,
        })
        cls.stage_progress = cls.env["helpdesk.ticket.stage"].search(
            [("closed", "=", False)], order="sequence desc", limit=1,
        )
        cls.tag = cls.env["helpdesk.ticket.tag"].create({"name": "Facturation"})

    def _ticket(self, name="billet 360", partner=None):
        return self.Ticket.create({
            "name": name,
            "description": "<p>x</p>",
            "team_id": self.team.id,
            "partner_id": (partner or self.contact).id,
            "client_lang": "fr_CA",
        })

    # ------------------------------------------------ macros v2

    def test_macro_variables_rendered_and_escaped(self):
        ticket = self._ticket("Accès <VPN>")
        macro = self.env["helpdesk.macro"].create({
            "name": "Accusé",
            "body_html": "<p>{{ salutation }} billet {{numero}} : {{ sujet }}, "
                         "{{ agent }}, {{ inconnue }}</p>",
        })
        body = str(macro.with_user(self.agent)._bf_render(ticket))
        self.assertIn("Bonjour Jane,", body)
        self.assertIn(ticket.number, body)
        self.assertIn("Accès &lt;VPN&gt;", body)
        self.assertIn("Agente Essai", body)
        self.assertIn("{{ inconnue }}", body)

    def test_wizard_posts_edited_body_and_applies_actions(self):
        ticket = self._ticket()
        macro = self.env["helpdesk.macro"].create({
            "name": "Question au client",
            "body_html": "<p>{{ salutation }} pouvez-vous préciser?</p>",
            "set_stage_id": self.stage_progress.id,
            "add_tag_ids": [(4, self.tag.id)],
            "assign_to_me": True,
            "set_waiting_state": "client",
        })
        wizard = self.env["helpdesk.macro.apply.wizard"].with_user(self.agent).create({
            "ticket_id": ticket.id, "macro_id": macro.id,
        })
        self.assertIn("Bonjour Jane,", str(wizard.body_html))
        self.assertIn("Attente — Client.e", wizard.actions_summary)
        wizard.body_html = "<p>Texte retouché par l'agente.</p>"
        wizard.action_apply()
        last = ticket.message_ids.filtered(
            lambda m: "retouché" in (m.body or "")
        )
        self.assertEqual(len(last), 1)
        self.assertEqual(last.subtype_id, self.env.ref("mail.mt_comment"))
        self.assertEqual(ticket.stage_id, self.stage_progress)
        self.assertIn(self.tag, ticket.tag_ids)
        self.assertEqual(ticket.user_id, self.agent)
        self.assertEqual(ticket.waiting_state, "client")
        self.assertTrue(ticket.first_response_date)

    def test_macro_as_note_is_not_a_first_response(self):
        ticket = self._ticket()
        macro = self.env["helpdesk.macro"].create({
            "name": "Note", "body_html": "<p>Vérifier le contrat.</p>",
            "post_as_note": True,
        })
        wizard = self.env["helpdesk.macro.apply.wizard"].with_user(self.agent).create({
            "ticket_id": ticket.id, "macro_id": macro.id,
        })
        self.assertTrue(wizard.post_as_note)
        wizard.action_apply()
        self.assertFalse(ticket.first_response_date)

    def test_actions_only_macro_posts_nothing(self):
        ticket = self._ticket()
        before = len(ticket.message_ids)
        macro = self.env["helpdesk.macro"].create({
            "name": "Retirer l'attente", "body_html": "<p><br></p>",
            "set_waiting_state": "clear",
        })
        ticket.waiting_state = "external"
        self.env["helpdesk.macro.apply.wizard"].create({
            "ticket_id": ticket.id, "macro_id": macro.id,
        }).action_apply()
        self.assertFalse(ticket.waiting_state)
        self.assertEqual(len(ticket.message_ids), before)

    def test_collision_warns_once_before_posting(self):
        ticket = self._ticket()
        macro = self.env["helpdesk.macro"].create({
            "name": "Réponse", "body_html": "<p>Voici la solution.</p>",
        })
        wizard = self.env["helpdesk.macro.apply.wizard"].with_user(self.agent).create({
            "ticket_id": ticket.id, "macro_id": macro.id,
        })
        # Le client écrit pendant que l'agente rédige.
        ticket.message_post(
            body="Finalement, c'est réglé!", message_type="comment",
            subtype_xmlid="mail.mt_comment", author_id=self.contact.id,
        )
        result = wizard.action_apply()
        self.assertEqual(result.get("res_id"), wizard.id)
        self.assertIn("c'est réglé", wizard.collision_warning)
        self.assertFalse(ticket.message_ids.filtered(lambda m: "solution" in (m.body or "")))
        wizard.action_apply()
        self.assertTrue(ticket.message_ids.filtered(lambda m: "solution" in (m.body or "")))

    def test_no_collision_warning_for_own_messages(self):
        ticket = self._ticket()
        macro = self.env["helpdesk.macro"].create({
            "name": "Réponse", "body_html": "<p>Deuxième envoi.</p>",
        })
        wizard = self.env["helpdesk.macro.apply.wizard"].with_user(self.agent).create({
            "ticket_id": ticket.id, "macro_id": macro.id,
        })
        ticket.with_user(self.agent).message_post(
            body="Premier envoi", message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        result = wizard.action_apply()
        self.assertEqual(result, {"type": "ir.actions.act_window_close"})

    # ------------------------------------------------ vue 360

    def test_client360_counts_other_tickets_of_the_organisation(self):
        colleague = self.env["res.partner"].create({
            "name": "Alex Gagnon", "parent_id": self.company.id,
        })
        other = self._ticket("autre", colleague)
        ticket = self._ticket()
        self._ticket("hors organisation", self.env["res.partner"].create({"name": "Tiers"}))
        # La création laisse la liste vide en cache ; une fiche ouverte la recalcule.
        ticket.invalidate_recordset()
        self.assertIn(other, ticket.bf_client_ticket_ids)
        self.assertNotIn(ticket, ticket.bf_client_ticket_ids)
        self.assertEqual(ticket.bf_client_open_ticket_count, 1)
        self.assertFalse(ticket.bf_client_last_csat)

    def test_client360_readable_by_agent(self):
        ticket = self._ticket()
        ticket = ticket.with_user(self.agent)
        ticket.invalidate_recordset()
        # Aucun AccessError, même sans droits comptables.
        self.assertEqual(ticket.bf_client_overdue_count, 0)
        self.assertGreaterEqual(ticket.bf_client_hosting_count, 0)

    # ------------------------------------------------ file de l'équipe

    def test_queue_filter_keeps_only_my_teams_open_tickets(self):
        mine = self._ticket()
        other_team = self.env["helpdesk.ticket.team"].create({"name": "Autre équipe"})
        foreign = self.Ticket.create({
            "name": "ailleurs", "description": "<p>x</p>", "team_id": other_team.id,
        })
        domain = [("closed", "=", False), ("team_id.user_ids", "in", [self.agent.id])]
        found = self.Ticket.search(domain)
        self.assertIn(mine, found)
        self.assertNotIn(foreign, found)
