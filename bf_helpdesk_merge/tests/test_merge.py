import base64
from datetime import timedelta

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged


class MergeCommon:
    @classmethod
    def _setup(cls):
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       mail_notify_force_send=False, lang="fr_CA"))
        cls.env.company.partner_id.lang = "fr_CA"
        cls.Ticket = cls.env["helpdesk.ticket"]
        model = cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Fusion", "ack_channel_ids": [(5, 0, 0)], "csat_mode": "native",
            "csat_delay_hours": 24,
            "alias_id": cls.env["mail.alias"].create({
                "alias_name": "fusion-essai", "alias_model_id": model.id,
            }).id,
        })
        cls.stages = cls.team._get_applicable_stages()
        cls.org = cls.env["res.partner"].create({"name": "Clinique Essai", "is_company": True})
        cls.client = cls.env["res.partner"].create({
            "name": "Kim Essai", "email": "kim@example.com", "lang": "fr_CA",
            "parent_id": cls.org.id})
        cls.colleague = cls.env["res.partner"].create({
            "name": "Lou Essai", "email": "lou@example.com", "lang": "fr_CA",
            "parent_id": cls.org.id})
        cls.stranger = cls.env["res.partner"].create({
            "name": "Autre Client", "email": "autre@example.com", "lang": "fr_CA"})
        cls.agent = new_test_user(
            cls.env, login="agent-fusion", email="agent.fusion@example.com",
            groups="base.group_user,helpdesk_mgmt.group_helpdesk_user")

    def _ticket(self, name="L'imprimante du 3e étage n'imprime plus",
                body="Plus rien ne sort de l'imprimante du 3e étage depuis ce matin.",
                partner=None, **vals):
        values = {"name": name, "description": f"<p>{body}</p>",
                  "team_id": self.team.id, "partner_id": (partner or self.client).id,
                  "stage_id": self.stages.filtered(lambda s: not s.closed)[:1].id}
        values.update(vals)
        ticket = self.Ticket.create(values)
        ticket.message_subscribe(partner_ids=ticket.partner_id.ids)
        return ticket


@tagged("bf_helpdesk", "bf_helpdesk_merge")
class TestMerge(MergeCommon, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup()

    def _merge(self, keep, others):
        wizard = self.env["helpdesk.ticket.merge"].with_context(
            active_model="helpdesk.ticket", active_ids=(keep | others).ids,
        ).create({})
        wizard.dst_ticket_id = keep
        return wizard.merge_tickets()

    def test_merge_moves_everything_and_notes_are_internal(self):
        keep, dup = self._ticket(), self._ticket(name="Imprimante 3e")
        tag = self.env["helpdesk.ticket.tag"].create({"name": "Impression"})
        dup.tag_ids = tag
        dup.message_subscribe(partner_ids=self.colleague.ids)
        att = self.env["ir.attachment"].create({
            "name": "capture.png", "datas": base64.b64encode(b"x"),
            "res_model": "helpdesk.ticket", "res_id": dup.id})
        project = self.env["project.project"].create({"name": "Banque essai", "allow_timesheets": True})
        employee = self.env["hr.employee"].create({"name": "Agent", "user_id": self.agent.id})
        line = self.env["account.analytic.line"].create({
            "name": "Diagnostic", "project_id": project.id, "ticket_id": dup.id,
            "unit_amount": 0.5, "employee_id": employee.id})
        dup.message_post(body="Détail envoyé par Kim.", author_id=self.client.id,
                         message_type="email", subtype_xmlid="mail.mt_comment")
        mails_before = self.env["mail.mail"].search_count([("recipient_ids", "in", self.client.ids)])

        self._merge(keep, dup)

        self.assertFalse(dup.active)
        self.assertEqual(dup.merged_into_id, keep)
        self.assertEqual(keep.merged_count, 1)
        self.assertEqual(line.ticket_id, keep)
        self.assertEqual(att.res_id, keep.id)
        self.assertIn(tag, keep.tag_ids)
        self.assertIn(self.colleague, keep.message_partner_ids)
        self.assertIn("Détail envoyé par Kim.", "".join(keep.message_ids.mapped("body")))
        self.assertIn("Description du billet", keep.description)
        # Avis de fusion : notes internes, des deux côtés, sans courriel au client.
        src_note = dup.with_context(active_test=False).message_ids.filtered(
            lambda m: "fusionné dans" in (m.body or ""))
        self.assertEqual(len(src_note), 1)
        self.assertTrue(src_note.subtype_id.internal)
        self.assertTrue(keep.message_ids.filtered(lambda m: "Fusion :" in (m.body or "")))
        self.assertEqual(
            self.env["mail.mail"].search_count([("recipient_ids", "in", self.client.ids)]),
            mails_before)

    def test_merge_cancels_source_survey(self):
        keep, dup = self._ticket(), self._ticket(name="Imprimante 3e")
        dup.stage_id = self.stages.filtered("closed")[:1]
        self.assertEqual(dup.csat_ids.state, "scheduled")
        self._merge(keep, dup)
        self.assertEqual(dup.with_context(active_test=False).csat_ids.state, "cancelled")

    def test_merge_refuses_two_companies(self):
        other_co = self.env["res.company"].create({"name": "Autre société"})
        model = self.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        other_team = self.env["helpdesk.ticket.team"].create({
            "name": "Autre équipe", "company_id": other_co.id,
            "ack_channel_ids": [(5, 0, 0)], "csat_mode": "none",
            "alias_id": self.env["mail.alias"].create({
                "alias_name": "autre-societe", "alias_model_id": model.id}).id})
        keep = self._ticket()
        dup = self._ticket(company_id=other_co.id, team_id=other_team.id)
        self.assertNotEqual(keep.company_id, dup.company_id)
        with self.assertRaises(UserError):
            self._merge(keep, dup)


@tagged("bf_helpdesk", "bf_helpdesk_merge")
class TestDuplicates(MergeCommon, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup()

    def test_same_contact_similar_is_suggested_for_merge(self):
        first = self._ticket()
        second = self._ticket(name="Imprimante du 3e étage : rien ne s'imprime")
        dup = second.duplicate_ids
        self.assertEqual(dup.candidate_id, first)
        self.assertTrue(dup.same_contact)
        self.assertIn("Fusionner", dup.recommendation)
        self.assertEqual(second.duplicate_open_count, 1)

    def test_same_org_other_contact_suggests_incident(self):
        self._ticket()
        second = self._ticket(partner=self.colleague,
                              name="Imprimante du 3e étage ne répond plus")
        self.assertFalse(second.duplicate_ids.same_contact)
        self.assertIn("incident", second.duplicate_ids.recommendation)

    def test_unrelated_or_other_client_or_old_or_closed_not_suggested(self):
        self._ticket()
        self.assertFalse(self._ticket(name="Accès VPN refusé",
                                      body="Le VPN refuse mon mot de passe.").duplicate_ids)
        self.assertFalse(self._ticket(partner=self.stranger).duplicate_ids)
        closed = self._ticket(partner=self.stranger, name="Scanner en panne", body="Le scanner ne marche plus.")
        closed.stage_id = self.stages.filtered("closed")[:1]
        self.assertFalse(self._ticket(partner=self.stranger, name="Scanner en panne encore",
                                      body="Le scanner ne marche plus.").duplicate_ids.filtered(
            lambda d: d.candidate_id == closed))
        with freeze_time(fields.Datetime.now() + timedelta(days=9)):
            later = self._ticket(name="L'imprimante du 3e étage n'imprime plus du tout")
            self.assertFalse(later.duplicate_ids)

    def test_dismiss_and_merge_outcomes_are_logged(self):
        first = self._ticket()
        second = self._ticket(name="Imprimante 3e étage n'imprime plus")
        dup = second.duplicate_ids
        dup.action_dismiss()
        self.assertEqual(second.duplicate_open_count, 0)
        self.assertEqual(dup.state, "dismissed")
        third = self._ticket(name="Imprimante du 3e : plus rien ne sort")
        suggestion = third.duplicate_ids.filtered(lambda d: d.candidate_id == first)
        action = suggestion.action_merge()
        wizard = self.env["helpdesk.ticket.merge"].with_context(**action["context"]).create({})
        self.assertEqual(wizard.dst_ticket_id, first)
        wizard.with_context(**action["context"]).merge_tickets()
        self.assertEqual(suggestion.state, "merged")


@tagged("bf_helpdesk", "bf_helpdesk_merge")
class TestIncidents(MergeCommon, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup()

    def _link(self, tickets, **vals):
        wizard = self.env["helpdesk.incident.link"].with_context(
            default_ticket_ids=[(6, 0, tickets.ids)]).create(vals)
        action = wizard.action_link()
        return self.Ticket.browse(action["res_id"])

    def test_link_new_incident_and_grouped_reply(self):
        a = self._ticket()
        b = self._ticket(partner=self.colleague, name="Imprimante 3e")
        b._bf_set_muted(self.colleague, True)
        parent = self._link(a | b, mode="new", name="Panne imprimante 3e")
        self.assertEqual(a.parent_incident_id, parent)
        self.assertEqual(parent.child_incident_count, 2)
        closed = self.stages.filtered("closed")[:1]
        reply = self.env["helpdesk.incident.reply"].create({
            "parent_id": parent.id, "body": "<p>Pièce remplacée, tout fonctionne.</p>",
            "close_children": True, "close_stage_id": closed.id})
        reply.action_send()
        self.assertTrue(all(t.stage_id.closed for t in a | b))
        for ticket, partner in ((a, self.client), (b, self.colleague)):
            mails = self.env["mail.mail"].search([
                ("model", "=", "helpdesk.ticket"), ("res_id", "=", ticket.id),
                ("recipient_ids", "in", partner.ids)])
            self.assertTrue(mails.filtered(lambda m: "Pièce remplacée" in m.body_html),
                            "chaque client reçoit la réponse dans son fil, même coupé à la résolution")
            other = self.colleague if partner == self.client else self.client
            self.assertNotIn(other, mails.recipient_ids)
        self.assertTrue(parent.message_ids.filtered(lambda m: "Réponse envoyée à 2" in (m.body or "")))

    def test_link_to_existing_and_depth(self):
        a, b, c = self._ticket(), self._ticket(name="x imprimante"), self._ticket(name="y imprimante")
        parent = self._link(a | b, mode="new", name="Incident")
        self._link(c, mode="existing", parent_id=parent.id)
        self.assertEqual(parent.child_incident_count, 3)
        with self.assertRaises(UserError):
            parent.parent_incident_id = a

    def test_theme_create_problem_links_recent_tickets(self):
        theme = self.env["helpdesk.theme"].create({"name": "Imprimantes essai"})
        a, b = self._ticket(), self._ticket(name="z imprimante", partner=self.stranger)
        (a | b).write({"theme_id": theme.id})
        action = theme.action_create_problem()
        self.assertEqual(action["res_model"], "helpdesk.incident.link")
        wizard = self.env["helpdesk.incident.link"].with_context(**action["context"]).create({})
        self.assertEqual(wizard.name, "Problème : Imprimantes essai")
        parent = self.Ticket.browse(wizard.action_link()["res_id"])
        self.assertEqual((a | b).parent_incident_id, parent)


@tagged("bf_helpdesk", "bf_helpdesk_merge")
class TestMergeClients(MergeCommon, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup()

    def test_merge_refuses_two_clients(self):
        keep = self._ticket()
        other = self._ticket(partner=self.stranger, name="Imprimante")
        wizard = self.env["helpdesk.ticket.merge"].with_context(
            active_model="helpdesk.ticket", active_ids=(keep | other).ids).create({})
        with self.assertRaises(UserError):
            wizard.merge_tickets()
        self.assertTrue(other.active)

    def test_agent_can_merge(self):
        """Un agent (pas administrateur) fusionne : les messages suivent."""
        self.team.user_ids = [(4, self.agent.id)]
        keep = self._ticket()
        other = self._ticket(name="Imprimante encore")
        other.message_post(body="Message du second billet", message_type="comment")
        wizard = self.env["helpdesk.ticket.merge"].with_user(self.agent).with_context(
            active_model="helpdesk.ticket", active_ids=(keep | other).ids).create({})
        wizard.dst_ticket_id = keep
        wizard.merge_tickets()
        self.assertFalse(other.active)
        self.assertIn("Message du second billet", "".join(keep.message_ids.mapped("body")))

    def test_related_tab_label_in_french(self):
        arch = self.Ticket.get_views([(False, "form")])["views"]["form"]["arch"]
        self.assertIn('string="Billets liés"', arch)
        self.assertNotIn("Related tickets", arch)

    def test_find_duplicates_refused_to_portal(self):
        """Appelable par RPC et exécutée en sudo : un usager du portail ne la déclenche pas."""
        from odoo.exceptions import AccessError
        ticket = self._ticket()
        portal = new_test_user(self.env, login="portail-fusion", email="portail.fusion@example.com",
                               groups="base.group_portal")
        with self.assertRaises(AccessError):
            ticket.with_user(portal).action_find_duplicates()

    def test_context_duplicate_outside_merge_untouched(self):
        """Un doublon passé par le contexte mais étranger aux billets fusionnés reste ouvert."""
        keep = self._ticket()
        other = self._ticket(name="Imprimante encore")
        a, b = self._ticket(name="Autre A"), self._ticket(name="Autre B")
        foreign = self.env["helpdesk.ticket.duplicate"].sudo().create(
            {"ticket_id": a.id, "candidate_id": b.id})
        state = foreign.state
        wizard = self.env["helpdesk.ticket.merge"].with_context(
            active_model="helpdesk.ticket", active_ids=(keep | other).ids,
            bf_hd_duplicate_id=foreign.id).create({})
        wizard.dst_ticket_id = keep
        wizard.merge_tickets()
        self.assertEqual(foreign.state, state)

    def test_duplicates_scoped_by_team(self):
        """Une paire dont un billet est hors des équipes de l'agent lui reste invisible."""
        other_team = self.env["helpdesk.ticket.team"].create({"name": "Autre équipe fusion"})
        mine = self._ticket()
        theirs = self._ticket(name="Ailleurs", team_id=other_team.id)
        dup = self.env["helpdesk.ticket.duplicate"].sudo().create(
            {"ticket_id": mine.id, "candidate_id": theirs.id})
        agent = new_test_user(self.env, login="agent-equipe-fusion", email="equipe.fusion@example.com",
                              groups="base.group_user,helpdesk_mgmt.group_helpdesk_user_team")
        self.team.user_ids = [(4, agent.id)]
        self.assertFalse(self.env["helpdesk.ticket.duplicate"].with_user(agent).search([("id", "=", dup.id)]))

    def test_candidate_fields_not_read_in_sudo(self):
        """Un onchange sur un candidat invisible ne rend ni son sujet ni son client."""
        from odoo.exceptions import AccessError
        other_team = self.env["helpdesk.ticket.team"].create({"name": "Équipe étrangère candidat"})
        theirs = self._ticket(name="Sujet confidentiel", team_id=other_team.id)
        agent = new_test_user(self.env, login="agent-candidat-fusion", email="candidat.fusion@example.com",
                              groups="base.group_user,helpdesk_mgmt.group_helpdesk_user_team")
        self.team.user_ids = [(4, agent.id)]
        Dup = self.env["helpdesk.ticket.duplicate"].with_user(agent)
        try:
            res = Dup.onchange({"candidate_id": theirs.id}, ["candidate_id"],
                               {"candidate_id": {}, "candidate_name": {}, "candidate_number": {}})
            self.assertNotIn("Sujet confidentiel", str(res))
        except AccessError:
            pass

    def test_onchange_refuses_hidden_candidate(self):
        """Le nom d'un billet invisible ne sort pas d'un onchange."""
        from odoo.exceptions import AccessError
        other_team = self.env["helpdesk.ticket.team"].create({"name": "Équipe étrangère onchange"})
        theirs = self._ticket(name="Sujet caché", team_id=other_team.id)
        agent = new_test_user(self.env, login="agent-onchange-fusion", email="onchange.fusion@example.com",
                              groups="base.group_user,helpdesk_mgmt.group_helpdesk_user_team")
        self.team.user_ids = [(4, agent.id)]
        with self.assertRaises(AccessError):
            self.env["helpdesk.ticket.duplicate"].with_user(agent).onchange(
                {"candidate_id": theirs.id}, ["candidate_id"],
                {"candidate_id": {"fields": {"display_name": {}}}})

    def test_incident_wizard_rows_belong_to_their_creator(self):
        mine = self._ticket()
        agent = new_test_user(self.env, login="agent-incident-a", email="incident.a@example.com",
                              groups="base.group_user,helpdesk_mgmt.group_helpdesk_user_team")
        other = new_test_user(self.env, login="agent-incident-b", email="incident.b@example.com",
                              groups="base.group_user,helpdesk_mgmt.group_helpdesk_user_team")
        self.team.user_ids = [(4, agent.id), (4, other.id)]
        link = self.env["helpdesk.incident.link"].with_user(agent).create(
            {"ticket_ids": [(6, 0, mine.ids)], "mode": "new", "name": "Panne"})
        self.assertFalse(self.env["helpdesk.incident.link"].with_user(other).search([("id", "=", link.id)]))

    def test_portal_cannot_read_merge_links(self):
        from odoo.exceptions import AccessError
        portal = new_test_user(self.env, login="portail-fusion", email="portail.fusion@example.com",
                               groups="base.group_portal")
        ticket = self._ticket(partner=portal.partner_id)
        for fname in ("merged_into_id", "parent_incident_id"):
            with self.assertRaises(AccessError, msg=fname):
                ticket.with_user(portal).read([fname])

    def test_onchange_guard_on_every_model(self):
        """Aucun de nos modèles ne rend en onchange le nom d'un billet invisible."""
        from odoo.exceptions import AccessError
        other_team = self.env["helpdesk.ticket.team"].create({"name": "Équipe étrangère garde"})
        theirs = self._ticket(name="Sujet gardé", team_id=other_team.id)
        agent = new_test_user(self.env, login="agent-garde-modeles", email="garde.modeles@example.com",
                              groups="base.group_user,helpdesk_mgmt.group_helpdesk_user_team")
        self.team.user_ids = [(4, agent.id)]
        name_spec = {"fields": {"display_name": {}}}
        cases = [
            ("helpdesk.ticket.csat", "ticket_id", theirs.id),
            ("helpdesk.triage.log", "ticket_id", theirs.id),
            ("helpdesk.triage.apply", "ticket_id", theirs.id),
            ("helpdesk.macro.apply.wizard", "ticket_id", theirs.id),
            ("helpdesk.ticket.duplicate", "candidate_id", theirs.id),
            ("helpdesk.incident.reply", "parent_id", theirs.id),
            # Many2one : Odoo en rend le nom en sudo, seule la garde l'arrête.
            ("helpdesk.incident.link", "parent_id", theirs.id),
            # Déclenché par un autre champ : l'onchange d'OCA sur le billet de
            # destination ne joue pas, seule la garde arrête la lecture.
            ("helpdesk.ticket.merge", "dst_ticket_id", theirs.id, "user_id"),
            # Many2many : Odoo lit déjà les billets avec les droits de l'usager.
            ("helpdesk.incident.link", "ticket_ids", [[6, 0, theirs.ids]]),
            ("helpdesk.ticket.merge", "ticket_ids", [[6, 0, theirs.ids]]),
        ]
        for model, fname, value, *changed in cases:
            changed = changed[0] if changed else fname
            values = {fname: value, **({changed: False} if changed != fname else {})}
            with self.subTest(model=model, field=fname), self.assertRaises(AccessError):
                self.env[model].with_user(agent).onchange(values, [changed], {fname: name_spec})

    def test_merge_wizard_rows_belong_to_their_creator(self):
        a = new_test_user(self.env, login="fusion-a", email="fusion.a@example.com",
                          groups="base.group_user,helpdesk_mgmt.group_helpdesk_user")
        b = new_test_user(self.env, login="fusion-b", email="fusion.b@example.com",
                          groups="base.group_user,helpdesk_mgmt.group_helpdesk_user")
        first, second = self._ticket(), self._ticket(name="Deuxième")
        wizard = self.env["helpdesk.ticket.merge"].with_user(a).with_context(
            active_model="helpdesk.ticket", active_ids=(first | second).ids,
        ).create({"ticket_ids": [(6, 0, (first | second).ids)], "dst_ticket_id": first.id})
        self.assertFalse(self.env["helpdesk.ticket.merge"].with_user(b).search([("id", "=", wizard.id)]))

    def test_refuses_destination_of_another_client(self):
        """Le billet de destination compte aussi : pas de fusion dans le billet d'un autre client."""
        other_client = self.env["res.partner"].create(
            {"name": "Autre client fusion", "email": "autre.fusion@example.com"})
        mine = self._ticket()
        theirs = self._ticket(name="Autre demande", partner=other_client)
        wizard = self.env["helpdesk.ticket.merge"].with_context(
            active_model="helpdesk.ticket", active_ids=mine.ids,
        ).create({"ticket_ids": [(6, 0, mine.ids)], "dst_ticket_id": theirs.id})
        with self.assertRaises(UserError):
            wizard.merge_tickets()
        self.assertTrue(mine.active)

    def test_parent_incident_must_be_readable(self):
        from odoo.exceptions import AccessError
        other_team = self.env["helpdesk.ticket.team"].create({"name": "Équipe étrangère parent"})
        theirs = self._ticket(name="Incident caché", team_id=other_team.id)
        agent = new_test_user(self.env, login="agent-parent-incident", email="parent.incident@example.com",
                              groups="base.group_user,helpdesk_mgmt.group_helpdesk_user_team")
        self.team.user_ids = [(4, agent.id)]
        mine = self._ticket()
        with self.assertRaises(AccessError):
            mine.with_user(agent).write({"parent_incident_id": theirs.id})
