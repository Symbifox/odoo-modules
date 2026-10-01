from unittest.mock import patch

from odoo.tests import TransactionCase, new_test_user, tagged

BRIDGE = "odoo.addons.bf_ai_bridge.models.bf_ai_bridge.BfAiBridge"


class TriageCommon:
    @classmethod
    def _setup(cls):
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       mail_notify_force_send=False, lang="fr_CA"))
        cls.Ticket = cls.env["helpdesk.ticket"]
        model = cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        cls.agent = new_test_user(
            cls.env, login="agent-triage", name="Alex Triage", email="agent.triage@example.com",
            groups="base.group_user,helpdesk_mgmt.group_helpdesk_user")
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Triage", "ack_channel_ids": [(5, 0, 0)], "csat_mode": "none",
            "user_ids": [(6, 0, cls.agent.ids)],
            "alias_id": cls.env["mail.alias"].create({
                "alias_name": "triage-essai", "alias_model_id": model.id}).id,
        })
        cls.stages = cls.team._get_applicable_stages()
        cls.cat_print = cls.env["helpdesk.ticket.category"].create({"name": "Impression"})
        cls.org = cls.env["res.partner"].create({"name": "Cabinet Essai", "is_company": True})
        cls.client = cls.env["res.partner"].create({
            "name": "Sacha Essai", "email": "sacha@example.com", "parent_id": cls.org.id})

    def _ticket(self, name="L'imprimante ne sort rien", body="Rien ne sort depuis ce matin.", **vals):
        values = {"name": name, "description": f"<p>{body}</p>", "team_id": self.team.id,
                  "partner_id": self.client.id}
        values.update(vals)
        return self.Ticket.create(values)

    def _answer(self, **over):
        data = {
            "categorisation": "Panne d'imprimante", "stage": self.stages[1:2].name or self.stages[:1].name,
            "stage_motif": "", "assignation": "Alex Triage", "assignation_motif": "",
            "categorie": "Impression", "priorite": 2, "sentiment": "neutre", "langue": "fr",
            "reponse": "Bonjour,\nNous regardons l'imprimante.", "confiance": 82,
        }
        data.update(over)
        return {"data": data}


@tagged("bf_helpdesk", "bf_helpdesk_triage")
class TestTriage(TriageCommon, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup()

    def test_no_auto_triage_by_default(self):
        self.assertEqual(self._ticket().triage_state, "none")

    def test_auto_triage_queues_then_suggests(self):
        self.team.triage_auto = True
        ticket = self._ticket()
        self.assertEqual(ticket.triage_state, "queued")
        with patch(BRIDGE + ".check_available", return_value=True), \
                patch(BRIDGE + ".call", return_value=self._answer()) as call:
            self.Ticket._cron_triage_queue()
        self.assertIn("Impression", call.call_args[0][1]["categories"])
        self.assertEqual(ticket.triage_state, "done")
        self.assertEqual(ticket.triage_category_id, self.cat_print)
        self.assertEqual(ticket.triage_priority, "2")
        self.assertEqual(ticket.triage_user_id, self.agent)
        self.assertEqual(ticket.triage_confidence, 82)
        # Rien n'est appliqué sans l'agent.
        self.assertFalse(ticket.category_id)
        self.assertFalse(ticket.user_id)

    def test_unknown_values_are_not_suggested(self):
        ticket = self._ticket()
        with patch(BRIDGE + ".check_available", return_value=True), \
                patch(BRIDGE + ".call", return_value=self._answer(
                    assignation="Personne Inventée", categorie="Inexistante", stage="Nulle part")):
            ticket._bf_triage_run()
        self.assertFalse(ticket.triage_user_id)
        self.assertFalse(ticket.triage_category_id)
        self.assertFalse(ticket.triage_stage_id)

    def test_bridge_error_marks_ticket_and_moves_on(self):
        self.team.triage_auto = True
        t1, t2 = self._ticket(), self._ticket(name="Autre")
        replies = [RuntimeError("socket"), self._answer()]

        def fake(*a, **k):
            r = replies.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        with patch(BRIDGE + ".check_available", return_value=True), \
                patch(BRIDGE + ".call", side_effect=fake):
            self.Ticket._cron_triage_queue()
        self.assertEqual(t1.triage_state, "error")
        self.assertEqual(t2.triage_state, "done")

    def _suggested(self):
        ticket = self._ticket()
        with patch(BRIDGE + ".check_available", return_value=True), \
                patch(BRIDGE + ".call", return_value=self._answer()):
            ticket._bf_triage_run()
        return ticket

    def test_apply_all_is_accepted(self):
        ticket = self._suggested()
        wiz = self.env["helpdesk.triage.apply"].with_context(default_ticket_id=ticket.id).create({})
        self.assertTrue(wiz.apply_category and wiz.apply_user and wiz.apply_priority)
        wiz.action_apply()
        self.assertEqual(ticket.category_id, self.cat_print)
        self.assertEqual(ticket.user_id, self.agent)
        self.assertEqual(ticket.priority, "2")
        self.assertEqual(ticket.triage_log_ids.outcome, "accepted")
        self.assertEqual(ticket.triage_log_ids.accepted_value, 100)
        self.assertEqual(ticket.triage_log_ids.confidence_band, "high")

    def test_apply_part_is_modified_and_reject_is_logged(self):
        ticket = self._suggested()
        wiz = self.env["helpdesk.triage.apply"].with_context(default_ticket_id=ticket.id).create({})
        wiz.apply_user = False
        wiz.action_apply()
        self.assertFalse(ticket.user_id)
        self.assertEqual(ticket.triage_log_ids.outcome, "modified")
        other = self._suggested()
        other.action_triage_reject()
        self.assertEqual(other.triage_log_ids.outcome, "rejected")
        self.assertEqual(other.triage_state, "rejected")

    def test_reply_draft_opens_composer_without_sending(self):
        ticket = self._suggested()
        before = self.env["mail.mail"].search_count([])
        action = ticket.action_triage_use_reply()
        self.assertEqual(action["res_model"], "mail.compose.message")
        self.assertIn("Nous regardons", action["context"]["default_body"])
        self.assertEqual(self.env["mail.mail"].search_count([]), before)


@tagged("bf_helpdesk", "bf_helpdesk_triage")
class TestGuardAndSpike(TriageCommon, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup()

    def test_guard_words_escalate_without_ai(self):
        self.team.guard_words_enabled = True
        with patch(BRIDGE + ".call") as call:
            ticket = self._ticket(name="RANÇONGICIEL sur le serveur", body="Tous les fichiers sont chiffrés.")
            call.assert_not_called()
        self.assertEqual(ticket.priority, "3")
        self.assertIn("rancongiciel", ticket.guard_words_hit)
        self.assertIn("Escalade : mot de garde", ticket.tag_ids.mapped("name"))

    def test_guard_words_whole_words_only_and_opt_in(self):
        self.team.guard_words_enabled = True
        calm = self._ticket(name="Mise à jour de l'antivirus", body="Question sur la licence.")
        self.assertFalse(calm.guard_words_hit)
        self.team.guard_words_enabled = False
        off = self._ticket(name="Urgent : panne générale")
        self.assertFalse(off.guard_words_hit)
        self.assertNotEqual(off.priority, "3")

    def test_spike_alert_once_per_window(self):
        self.team.write({"spike_enabled": True, "spike_threshold": 3, "spike_window_hours": 1})
        colleague = self.env["res.partner"].create({"name": "Collègue", "parent_id": self.org.id})
        self._ticket()
        self._ticket(partner_id=colleague.id)
        self.assertFalse(self.env["helpdesk.spike.alert"].search([]))
        self._ticket(name="Encore")
        alerts = self.env["helpdesk.spike.alert"].search([])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts.count, 3)
        self.assertEqual(alerts.scope_label, "Cabinet Essai")
        self._ticket(name="Quatrième")
        self.assertEqual(len(self.env["helpdesk.spike.alert"].search([])), 1)
        notified = self.env["mail.message"].search([
            ("model", "=", "helpdesk.ticket.team"), ("res_id", "=", self.team.id),
            ("subject", "like", "Pic de billets")])
        self.assertEqual(len(notified), 1)
        self.assertIn(self.agent.partner_id, notified.partner_ids)


@tagged("bf_helpdesk", "bf_helpdesk_triage")
class TestThemes(TriageCommon, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup()

    def test_weekly_themes_assign_and_create(self):
        self.team.triage_auto = True
        existing = self.env["helpdesk.theme"].create({"name": "Imprimantes"})
        closed = self.stages.filtered("closed")[:1]
        t1 = self._ticket()
        t2 = self._ticket(name="VPN refusé", body="Le VPN refuse la connexion.")
        (t1 | t2).write({"stage_id": closed.id})
        answer = {"data": {"affectations": [
            {"numero": t1.number, "theme": "Imprimantes"},
            {"numero": t2.number, "theme": "Accès VPN"}], "nouveaux": ["Accès VPN"]}}
        with patch(BRIDGE + ".check_available", return_value=True), \
                patch(BRIDGE + ".call", return_value=answer) as call:
            self.Ticket._cron_weekly_themes()
        self.assertIn("Imprimantes", call.call_args[0][1]["themes"])
        self.assertEqual(t1.theme_id, existing)
        self.assertEqual(t2.theme_id.name, "Accès VPN")
        self.assertIn("Échantillon insuffisant", existing.trend)
        self.assertIn("n = 1", existing.trend)

    def test_themes_skip_teams_without_auto_triage(self):
        closed = self.stages.filtered("closed")[:1]
        self._ticket().stage_id = closed
        with patch(BRIDGE + ".call") as call:
            self.Ticket._cron_weekly_themes()
            call.assert_not_called()
