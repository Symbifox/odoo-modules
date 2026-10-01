"""Correctifs de robustesse et d'accès : un essai par constat."""
from datetime import timedelta
from unittest.mock import patch

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("bf_helpdesk", "bf_helpdesk_review")
class TestReviewFixes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_CA")
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       mail_notify_force_send=False, lang="fr_CA"))
        cls.env.company.partner_id.lang = "fr_CA"
        cls.Ticket = cls.env["helpdesk.ticket"]
        model = cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket")
        cls.agent = new_test_user(
            cls.env, login="agent-revue", email="agent.revue@example.com", tz="America/Toronto",
            groups="base.group_user,helpdesk_mgmt.group_helpdesk_user")
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Revue", "user_ids": [(6, 0, cls.agent.ids)],
            "alias_id": cls.env["mail.alias"].create({
                "alias_name": "revue-essai", "alias_model_id": model.id}).id,
        })
        cls.stages = cls.team._get_applicable_stages()
        cls.closed = cls.stages.filtered("closed")[:1]
        cls.client = cls.env["res.partner"].create({
            "name": "Client Revue", "email": "client.revue@example.com", "lang": "fr_CA"})
        cls.stranger = cls.env["res.partner"].create({
            "name": "Inconnu", "email": "inconnu@example.com", "lang": "fr_CA"})

    def _ticket(self, **vals):
        values = {"name": "Poste lent", "description": "<p>x</p>", "team_id": self.team.id,
                  "partner_id": self.client.id,
                  "stage_id": self.stages.filtered(lambda s: not s.closed)[:1].id}
        values.update(vals)
        ticket = self.Ticket.create(values)
        ticket.message_subscribe(partner_ids=self.client.ids)
        return ticket

    def _mails(self, ticket):
        return self.env["mail.mail"].search([("model", "=", "helpdesk.ticket"), ("res_id", "=", ticket.id)])

    # un seul courriel à la fermeture automatique, même avec un gabarit d'étape
    def test_autoclose_skips_stage_template(self):
        tpl = self.env["mail.template"].create({
            "name": "Fermé (étape)", "model_id": self.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id,
            "subject": "Gabarit d'étape", "email_to": "{{ object.partner_id.email }}",
            "body_html": "<p>Fermé</p>"})
        self.closed.mail_template_id = tpl
        self.team.write({"reminder_enabled": True, "reminder_close_stage_id": self.closed.id,
                         "sla_calendar_id": False})
        ticket = self._ticket(waiting_state="client")
        ticket.with_context(tracking_disable=False).write({"reminder_count": 2})
        ticket.with_context(tracking_disable=False)._bf_reminder_step()
        # Le suivi (et donc le gabarit d'étape) ne s'écrit qu'au précommit.
        self.env.cr.precommit.run()
        self.env.flush_all()
        subjects = self._mails(ticket).mapped("subject")
        self.assertTrue(any("Demande fermée" in (s or "") for s in subjects))
        self.assertFalse(any("Gabarit d'étape" in (s or "") for s in subjects), subjects)
        # Le suivi ne se déclenche pas dans cet essai : le point de coupure se
        # vérifie directement (un envoi réel l'a vu jouer).
        other = self._ticket()
        other.stage_id = self.closed
        self.assertIn("stage_id", other._track_template({"stage_id": True}))
        other._bf_skip_stage_template()
        self.assertNotIn("stage_id", other._track_template({"stage_id": True}))

    # une valeur non textuelle du modèle ne casse pas le triage
    def test_triage_survives_non_string_values(self):
        ticket = self._ticket()
        answer = {"data": {"reponse": 12345, "stage_motif": ["a"], "categorisation": None, "confiance": 50}}
        with patch("odoo.addons.bf_ai_bridge.models.bf_ai_bridge.BfAiBridge.call", return_value=answer):
            self.assertTrue(ticket._bf_triage_run())
        self.assertEqual(ticket.triage_state, "done")
        self.assertEqual(ticket.triage_reply, "12345")

    # un billet en échec ne bloque pas les relances des autres
    def test_reminder_failure_is_isolated(self):
        self.team.write({"reminder_enabled": True, "sla_calendar_id": False})
        with freeze_time(fields.Datetime.now() - timedelta(days=10)):
            t1 = self._ticket(waiting_state="client")
            t2 = self._ticket(waiting_state="client", name="Deuxième")
        orig = type(self.Ticket)._bf_reminder_step

        def boom(rec):
            if rec == t1:
                raise ValueError("panne simulée")
            return orig(rec)
        with patch.object(type(self.Ticket), "_bf_reminder_step", boom):
            self.Ticket._cron_waiting_reminders()
        self.assertEqual(t2.reminder_count, 1)
        self.assertEqual(t1.reminder_count, 0)

    # un inconnu ne lève pas l'attente et ne rouvre rien
    def test_stranger_does_not_lift_waiting(self):
        ticket = self._ticket(waiting_state="client")
        ticket.message_post(body="Je passais par là.", author_id=self.stranger.id,
                            message_type="email", subtype_xmlid="mail.mt_comment")
        self.assertEqual(ticket.waiting_state, "client")
        ticket.message_post(body="Voici l'info.", author_id=self.client.id,
                            message_type="email", subtype_xmlid="mail.mt_comment")
        self.assertFalse(ticket.waiting_state)

    def test_subject_routing_refuses_agent_address(self):
        ticket = self._ticket()
        ticket.message_subscribe(partner_ids=self.agent.partner_id.ids)
        found = self.Ticket._bf_ticket_from_subject({
            "subject": "[%s] x" % ticket.number, "email_from": self.agent.email})
        self.assertFalse(found)
        found = self.Ticket._bf_ticket_from_subject({
            "subject": "[%s] x" % ticket.number, "email_from": self.client.email})
        self.assertEqual(found, ticket)

    # un seul ntfy pour un mot de garde ; « urgent » n'en est plus un
    def test_guard_word_single_ntfy(self):
        self.team.guard_words_enabled = True
        with patch.object(type(self.Ticket), "_maybe_notify_ntfy_critical") as ntfy:
            self._ticket(name="Rançongiciel sur le serveur")
        reasons = [c.kwargs.get("reason") for c in ntfy.call_args_list]
        self.assertEqual(reasons.count("guard_word"), 1)
        self.assertNotIn("escalated", reasons)
        self.assertFalse(self._ticket(name="C'est urgent, merci").guard_words_hit)

    # condensés quotidiens : une date par canal
    def test_daily_digests_per_channel(self):
        Item = self.env["helpdesk.agent.notify.item"]
        ticket = self._ticket()
        for channel in ("odoo", "email"):
            Item.create({"user_id": self.agent.id, "ticket_id": ticket.id, "event": "client_reply",
                         "channel": channel, "mode": "daily", "summary": "S " + channel})
        with freeze_time(fields.Datetime.now().replace(hour=14)):
            Item._cron_send_agent_digests()
        self.assertTrue(all(Item.search([("user_id", "=", self.agent.id)]).mapped("sent")))

    # l'activité « SLA dépassé » ne revient pas toutes les heures
    def test_sla_breach_activity_once(self):
        self.team.write({"sla_response_hours": 1.0, "sla_calendar_id": False})
        ticket = self._ticket(user_id=self.agent.id)
        with freeze_time(ticket.create_date + timedelta(hours=3)):
            self.Ticket._cron_sla_breach_activity()
            acts = ticket.activity_ids.filtered(lambda a: a.summary == "SLA dépassé")
            self.assertEqual(len(acts), 1)
            acts.action_feedback(feedback="vu")
            self.Ticket._cron_sla_breach_activity()
            self.assertFalse(ticket.activity_ids.filtered(lambda a: a.summary == "SLA dépassé"))

    # page de sondage en anglais pour un client anglophone
    def test_csat_labels_english(self):
        from odoo.addons.bf_helpdesk.models.helpdesk_ticket_csat import (
            RATINGS, RATINGS_EN, localized)
        self.assertEqual(dict(localized(RATINGS, RATINGS_EN, True))["5"], "Very satisfied")
        self.assertEqual(dict(localized(RATINGS, RATINGS_EN, False))["5"], "Très satisfait")

    # un client au portail ne lit pas les champs internes de son billet
    def test_portal_cannot_read_internal_fields(self):
        portal = new_test_user(self.env, login="portail-revue", groups="base.group_portal",
                               partner_id=self.client.id)
        ticket = self._ticket(triage_sentiment="frustre", guard_words_hit="virus")
        for field in ("triage_sentiment", "guard_words_hit", "triage_reply", "bf_client_overdue_amount"):
            with self.assertRaises(AccessError, msg=field):
                ticket.with_user(portal).read([field])

    # le lien d'un sondage répondu se ferme à l'échéance
    def test_answered_survey_link_expires(self):
        self.team.write({"csat_mode": "native", "csat_delay_hours": 0, "csat_expiry_days": 28})
        ticket = self._ticket()
        ticket.stage_id = self.closed
        csat = ticket.csat_ids
        csat._record_answer("4")
        self.assertTrue(csat._is_open())
        with freeze_time(fields.Datetime.now() + timedelta(days=29)):
            self.assertFalse(csat._is_open())
        self.assertEqual(csat.state, "answered")

    # règles de société
    def test_company_rules_on_articles(self):
        other = self.env["res.company"].create({"name": "Autre société revue"})
        self.env["helpdesk.article"].create({"name": "Chez l'autre", "company_id": other.id})
        mine = self.env["helpdesk.article"].create({"name": "Chez moi"})
        seen = self.env["helpdesk.article"].with_user(self.agent).search([("name", "in", ["Chez l'autre", "Chez moi"])])
        self.assertEqual(seen, mine)

    # un billet né d'un courriel reçoit un vrai numéro
    def test_email_ticket_gets_sequence_number(self):
        raw = ("From: Client Revue <client.revue@example.com>\r\nTo: revue-essai@example.com\r\n"
               "Subject: Le lecteur ne répond plus\r\nMessage-ID: <revue-numero@example.com>\r\n"
               "Content-Type: text/plain; charset=utf-8\r\n\r\nImpossible d'ouvrir P:.\r\n")
        tid = self.env["mail.thread"].message_process(
            "helpdesk.ticket", raw, custom_values={"team_id": self.team.id})
        ticket = self.Ticket.browse(tid)
        self.assertEqual(ticket.name, "Le lecteur ne répond plus")
        self.assertNotEqual(ticket.number, ticket.name)
        self.assertNotEqual(ticket.number, "/")


    # « Nouveau billet dans mes équipes » sans suivre l'équipe
    def test_new_ticket_pref_reaches_member_not_follower(self):
        self.assertNotIn(self.agent.partner_id, self.team.message_partner_ids)
        self.env["helpdesk.notify.pref"].create({
            "user_id": self.agent.id, "event": "new_ticket", "channel": "email", "mode": "hourly"})
        ticket = self.Ticket.with_context(tracking_disable=False).create({
            "name": "Nouveau poste", "description": "<p>x</p>", "team_id": self.team.id,
            "partner_id": self.client.id})
        items = self.env["helpdesk.agent.notify.item"].search([("ticket_id", "=", ticket.id)])
        self.assertEqual(items.user_id, self.agent)
        self.assertEqual(items.event, "new_ticket")

    # le statut au portail se lit sans droit sur le calendrier
    def test_portal_status_readable_with_team_calendar(self):
        portal = new_test_user(self.env, login="portail-revue", email="portail.revue@example.com",
                               groups="base.group_portal")
        self.team.write({"sla_calendar_id": self.env.company.resource_calendar_id.id,
                         "sla_response_hours": 4.0})
        ticket = self._ticket(partner_id=portal.partner_id.id)
        ticket.message_subscribe(partner_ids=portal.partner_id.ids)
        self.assertTrue(ticket.sla_response_deadline)
        mine = ticket.with_user(portal)
        # Tout le cache : le calendrier écrit plus haut y dort, et une valeur en
        # cache ne repasse pas par le contrôle d'accès.
        self.env.invalidate_all()
        self.assertTrue(mine.portal_status)
        self.assertIn("Première réponse", mine.portal_status_hint)

    # expéditeur nommé, jamais une adresse nue
    def test_client_templates_named_sender(self):
        self.env.company.email = "bonjour@example.com"
        ticket = self._ticket()
        ticket.team_id.alias_id.alias_name = False
        for xmlid in ("mail_template_ticket_ack", "mail_template_waiting_reminder",
                      "mail_template_waiting_autoclose", "mail_template_client_update"):
            tpl = self.env.ref("bf_helpdesk." + xmlid)
            sender = tpl._render_field("email_from", ticket.ids)[ticket.id]
            self.assertEqual(sender, '"%s — Revue" <bonjour@example.com>' % self.env.company.name, xmlid)

    # le compte système ne signe pas « -- System »
    def test_system_author_has_no_signature(self):
        root = self.env.ref("base.user_root")
        root.signature = "<p>System</p>"
        ticket = self._ticket()
        ticket.message_post(
            body="Votre demande est fermée.", author_id=root.partner_id.id,
            message_type="comment", subtype_xmlid="mail.mt_comment",
            partner_ids=self.client.ids)
        mail = self._mails(ticket).filtered(lambda m: "Votre demande est fermée" in (m.body_html or ""))
        self.assertTrue(mail)
        self.assertNotIn("System", mail[-1].body_html)

    # les liens des avis supposent /odoo/helpdesk-tickets/<id>
    def test_ticket_action_has_readable_path(self):
        self.assertEqual(self.env.ref("helpdesk_mgmt.helpdesk_ticket_action").path, "helpdesk-tickets")

    # une copie orpheline de la fiche masquait la vraie
    def test_orphan_views_are_archived(self):
        from odoo.addons.bf_helpdesk.models.orphan_views import archive_orphan_helpdesk_views
        real = self.env.ref("helpdesk_mgmt.ticket_view_form")
        orphan = real.copy({"name": real.name, "priority": real.priority})
        child = self.env["ir.ui.view"].create({
            "name": "enfant orphelin", "model": "helpdesk.ticket", "inherit_id": orphan.id,
            "arch": "<xpath expr='//sheet' position='inside'><div/></xpath>"})
        own = self.env["ir.ui.view"].create({
            "name": "vue maison sans jumelle", "model": "helpdesk.ticket", "type": "form",
            "arch": "<form><field name='name'/></form>"})
        self.env.flush_all()
        archived = archive_orphan_helpdesk_views(self.env.cr)
        self.env.invalidate_all()
        self.assertIn(orphan.id, archived)
        self.assertIn(child.id, archived)
        self.assertFalse(orphan.active)
        self.assertTrue(real.active)
        self.assertTrue(own.active)

    # la fiche entière s'ouvre pour un agent ordinaire
    def test_agent_can_read_every_form_field(self):
        """Tout champ que la fiche montre à l'agent doit lui être lisible : un seul
        champ interdit (feuilles de temps, rencontres) faisait tomber la fiche."""
        import re
        self.assertFalse(self.agent.has_group("hr_timesheet.group_hr_timesheet_user"))
        ticket = self._ticket()
        Ticket = self.Ticket.with_user(self.agent)
        views = Ticket.get_views([(False, "form")])
        arch = views["views"]["form"]["arch"]
        known = views["models"]["helpdesk.ticket"]
        known = known["fields"] if "fields" in known else known
        names = sorted({f for f in re.findall(r'<field name="([a-z0-9_]+)"', arch) if f in known})
        self.env.invalidate_all()
        Ticket.browse(ticket.id).web_read({f: {} for f in names})

    def _form_fields_readable_by(self, user, ticket):
        import re
        Ticket = self.Ticket.with_user(user)
        views = Ticket.get_views([(False, "form")])
        arch = views["views"]["form"]["arch"]
        known = views["models"]["helpdesk.ticket"]
        known = known["fields"] if "fields" in known else known
        names = sorted({f for f in re.findall(r'<field name="([a-z0-9_]+)"', arch) if f in known})
        self.env.invalidate_all()
        Ticket.browse(ticket.id).web_read({f: {} for f in names})

    # un agent « Billets personnels » ouvre aussi la fiche
    def test_personal_agent_can_read_every_form_field(self):
        own = new_test_user(self.env, login="agent-perso-revue", email="perso.revue@example.com",
                            groups="base.group_user,helpdesk_mgmt.group_helpdesk_user_own")
        self.assertFalse(own.has_group("helpdesk_mgmt.group_helpdesk_user_team"))
        self.team.user_ids = [(4, own.id)]
        ticket = self._ticket(user_id=own.id)
        self._form_fields_readable_by(own, ticket)

    # les sondages suivent la visibilité des billets
    def test_csat_scoped_by_team(self):
        other_team = self.env["helpdesk.ticket.team"].create({"name": "Autre équipe revue"})
        ticket = self._ticket(team_id=other_team.id)
        csat = self.env["helpdesk.ticket.csat"].sudo().create({"ticket_id": ticket.id})
        team_agent = new_test_user(self.env, login="agent-equipe-revue", email="equipe.revue@example.com",
                                   groups="base.group_user,helpdesk_mgmt.group_helpdesk_user_team")
        self.team.user_ids = [(4, team_agent.id)]
        Csat = self.env["helpdesk.ticket.csat"]
        self.assertFalse(Csat.with_user(team_agent).search([("id", "=", csat.id)]))
        everyone = new_test_user(self.env, login="agent-tous-revue", email="tous.revue@example.com",
                                 groups="base.group_user,helpdesk_mgmt.group_helpdesk_user")
        self.assertEqual(Csat.with_user(everyone).search([("id", "=", csat.id)]), csat)

    # un seul courriel Blue Fox à la fermeture
    def _close_and_mails(self, **team_vals):
        self.closed.mail_template_id = self.env.ref("bf_helpdesk.mail_template_ticket_closed")
        self.team.write(dict({"sla_calendar_id": False}, **team_vals))
        # Créé AVEC le suivi : un billet créé sans suivi est écarté du suivi pour
        # toute la transaction, et le gabarit d'étape ne partirait jamais.
        Ticket = self.Ticket.with_context(tracking_disable=False)
        ticket = Ticket.create({
            "name": "Poste lent", "description": "<p>x</p>", "team_id": self.team.id,
            "partner_id": self.client.id,
            "stage_id": self.stages.filtered(lambda s: not s.closed)[:1].id})
        ticket.message_subscribe(partner_ids=self.client.ids)
        ticket.stage_id = self.closed
        # Comme flush_tracking() des essais d'Odoo : écrire, puis le suivi.
        self.env.flush_all()
        self.env.cr.precommit.run()
        self.env.flush_all()
        return ticket, self._mails(ticket).mapped("subject")

    def test_closing_email_without_survey(self):
        self.env.company.email = "bonjour@example.com"
        ticket, subjects = self._close_and_mails(csat_mode="none")
        closing = [s for s in subjects if (s or "").startswith("Demande fermée")]
        self.assertEqual(len(closing), 1, subjects)
        self.assertIn(ticket.number, closing[0])

    def test_survey_at_close_replaces_closing_email(self):
        self.env.company.email = "bonjour@example.com"
        ticket, subjects = self._close_and_mails(csat_mode="native", csat_delay_hours=0)
        self.assertTrue(any("Votre avis" in (s or "") for s in subjects), subjects)
        self.assertFalse(any((s or "").startswith("Demande fermée") for s in subjects), subjects)

    def test_closing_template_swap_keeps_hand_choices(self):
        from odoo.addons.bf_helpdesk.models.closing_template import use_bf_closing_template
        oca = self.env.ref("helpdesk_mgmt.closed_ticket_template")
        own = self.env["mail.template"].create({
            "name": "Maison", "model_id": self.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id})
        Stage = self.env["helpdesk.ticket.stage"]
        s1 = Stage.create({"name": "Fermé A", "closed": True, "mail_template_id": oca.id})
        s2 = Stage.create({"name": "Fermé B", "closed": True, "mail_template_id": own.id})
        use_bf_closing_template(self.env)
        self.assertEqual(s1.mail_template_id, self.env.ref("bf_helpdesk.mail_template_ticket_closed"))
        self.assertEqual(s2.mail_template_id, own)

    # le texte à remplacer du gabarit ne part jamais
    def test_client_update_placeholder_blocks_sending(self):
        from odoo.exceptions import UserError
        ticket = self._ticket()
        tpl = self.env.ref("bf_helpdesk.mail_template_client_update")
        Compose = self.env["mail.compose.message"].with_context(
            default_model="helpdesk.ticket", default_res_ids=ticket.ids,
            default_composition_mode="comment", default_template_id=tpl.id,
            default_partner_ids=self.client.ids)
        compose = Compose.create({})
        self.assertIn("Décrivez ici", str(compose.body))
        with self.assertRaises(UserError):
            compose.action_send_mail()
        compose.body = str(compose.body).replace(
            "[Décrivez ici l&#39;avancement, la prochaine étape ou la question au client.]",
            "Le serveur redémarre ce soir.").replace(
            "[Décrivez ici l'avancement, la prochaine étape ou la question au client.]",
            "Le serveur redémarre ce soir.")
        compose.action_send_mail()
        self.assertTrue(self._mails(ticket).filtered(lambda m: "redémarre ce soir" in (m.body_html or "")))

    # L'assistant de macro ne lit pas les messages d'un billet qu'on ne peut modifier
    def test_macro_wizard_refuses_foreign_ticket(self):
        from odoo.exceptions import AccessError
        other_team = self.env["helpdesk.ticket.team"].create({"name": "Équipe étrangère macro"})
        foreign = self._ticket(team_id=other_team.id)
        team_agent = new_test_user(self.env, login="agent-macro-revue", email="macro.revue@example.com",
                                   groups="base.group_user,helpdesk_mgmt.group_helpdesk_user_team")
        self.team.user_ids = [(4, team_agent.id)]
        macro = self.env["helpdesk.macro"].create({"name": "Accusé revue", "body_html": "<p>Bonjour</p>"})
        Wizard = self.env["helpdesk.macro.apply.wizard"].with_user(team_agent)
        with self.assertRaises(AccessError):
            Wizard.create({"ticket_id": foreign.id, "macro_id": macro.id})
        mine = self._ticket()
        wizard = Wizard.create({"ticket_id": mine.id, "macro_id": macro.id})
        with self.assertRaises(AccessError):
            wizard.write({"ticket_id": foreign.id})

    # Le triage IA à la demande exige le droit de modifier le billet
    def test_triage_on_demand_needs_write(self):
        from odoo.exceptions import AccessError
        portal = new_test_user(self.env, login="portail-triage-revue", email="portail.triage@example.com",
                               groups="base.group_portal")
        ticket = self._ticket(partner_id=portal.partner_id.id)
        with self.assertRaises(AccessError):
            ticket.with_user(portal).action_triage_with_claude()

    # Renommer une équipe qui a déjà une adresse courte
    def test_rename_team_with_slug(self):
        self.assertTrue(self.team.slug)
        self.team.write({"name": "Revue renommée", "sla_response_hours": 3.0})
        self.assertEqual(self.team.name, "Revue renommée")
        self.assertEqual(self.team.sla_response_hours, 3.0)

    # Les champs liés de l'assistant de triage ne lisent pas le billet d'une autre équipe
    def test_triage_apply_refuses_foreign_ticket(self):
        from odoo.exceptions import AccessError
        other_team = self.env["helpdesk.ticket.team"].create({"name": "Équipe étrangère triage"})
        foreign = self._ticket(team_id=other_team.id)
        team_agent = new_test_user(self.env, login="agent-triage-revue", email="triage.revue@example.com",
                                   groups="base.group_user,helpdesk_mgmt.group_helpdesk_user_team")
        self.team.user_ids = [(4, team_agent.id)]
        with self.assertRaises(AccessError):
            self.env["helpdesk.triage.apply"].with_user(team_agent).create({"ticket_id": foreign.id})

    # Champs de persona, de matrice et de banque d'heures : illisibles au portail
    def test_portal_cannot_read_persona_and_bank(self):
        from odoo.exceptions import AccessError
        portal = new_test_user(self.env, login="portail-persona-revue", email="persona.revue@example.com",
                               groups="base.group_portal")
        ticket = self._ticket(partner_id=portal.partner_id.id)
        ticket.message_subscribe(partner_ids=portal.partner_id.ids)
        for fname in ("persona_tone_summary", "persona_our_tone_summary", "scope_aligned",
                      "knowledge_item_id", "hour_bank_id", "hour_bank_low"):
            with self.assertRaises(AccessError, msg=fname):
                ticket.with_user(portal).read([fname])

    # Une ligne de temps ne se rattache pas à un billet illisible
    def test_timesheet_line_needs_readable_ticket(self):
        from odoo.exceptions import AccessError
        other_team = self.env["helpdesk.ticket.team"].create({"name": "Équipe étrangère temps"})
        foreign = self._ticket(team_id=other_team.id)
        team_agent = new_test_user(self.env, login="agent-temps-revue", email="temps.revue@example.com",
                                   groups="base.group_user,helpdesk_mgmt.group_helpdesk_user_team")
        self.team.user_ids = [(4, team_agent.id)]
        with self.assertRaises(AccessError):
            self.env["account.analytic.line"].with_user(team_agent)._bf_check_ticket([foreign.id])

    # La présence ne s'expose pas au portail
    def test_presence_hidden_from_portal(self):
        portal = new_test_user(self.env, login="portail-presence-revue", email="presence.revue@example.com",
                               groups="base.group_portal")
        ticket = self._ticket(partner_id=portal.partner_id.id)
        ticket.message_subscribe(partner_ids=portal.partner_id.ids)
        ticket.with_user(self.agent).bf_presence_ping()  # un agent est sur la fiche
        self.assertEqual(ticket.with_user(portal).bf_presence_ping()["others"], [])
        # Et le client ne s'affiche pas à l'agent.
        others = ticket.with_user(self.agent).bf_presence_ping()["others"]
        self.assertNotIn(portal.id, [o["id"] for o in others])

    def _foreign_ticket_and_team_agent(self, login):
        other_team = self.env["helpdesk.ticket.team"].create({"name": f"Équipe étrangère {login}"})
        foreign = self._ticket(team_id=other_team.id)
        agent = new_test_user(self.env, login=login, email=f"{login}@example.com",
                              groups="base.group_user,helpdesk_mgmt.group_helpdesk_user_team")
        self.team.user_ids = [(4, agent.id)]
        return foreign, agent

    # Onchange : le portail n'a aucun formulaire de l'interface
    def test_onchange_refused_to_portal(self):
        portal = new_test_user(self.env, login="portail-onchange-revue",
                               email="onchange.revue@example.com", groups="base.group_portal")
        with self.assertRaises(AccessError):
            self.Ticket.with_user(portal).onchange(
                {"partner_id": portal.partner_id.id}, [], {"name": {}})

    # Onchange : un champ réservé à un groupe ne se lit pas sur un billet neuf
    def test_onchange_checks_field_groups(self):
        agent = new_test_user(self.env, login="agent-persona-revue", email="persona.agent@example.com",
                              groups="base.group_user,helpdesk_mgmt.group_helpdesk_user")
        with self.assertRaises(AccessError):
            self.Ticket.with_user(agent).onchange(
                {"partner_id": self.client.id}, ["partner_id"], {"persona_tone_summary": {}})

    # Onchange : un billet illisible ne se passe pas en valeur
    def test_onchange_refuses_foreign_ticket(self):
        foreign, agent = self._foreign_ticket_and_team_agent("agent-onchange-revue")
        with self.assertRaises(AccessError):
            self.env["helpdesk.triage.log"].with_user(agent).onchange(
                {"ticket_id": foreign.id}, ["ticket_id"],
                {"ticket_id": {"fields": {"display_name": {}}}})

    # Sondage : client et équipe du billet lus avec les droits de l'usager
    def test_csat_related_fields_not_read_in_sudo(self):
        foreign, agent = self._foreign_ticket_and_team_agent("agent-csat-lie-revue")
        self.env.invalidate_all()  # le cache de la transaction masquerait le contrôle
        try:
            partner = self.env["helpdesk.ticket.csat"].with_user(agent).new(
                {"ticket_id": foreign.id}).partner_id
        except AccessError:
            partner = self.env["res.partner"]
        self.assertNotEqual(partner, foreign.partner_id)

    # Macro : rendue avec les droits de l'usager
    def test_macro_body_not_rendered_in_sudo(self):
        foreign, agent = self._foreign_ticket_and_team_agent("agent-macro-rendu-revue")
        macro = self.env["helpdesk.macro"].create({"name": "Accusé", "body_html": "<p>{{ client }}</p>"})
        with self.assertRaises(AccessError):
            self.env["helpdesk.macro.apply.wizard"].with_user(agent).new(
                {"ticket_id": foreign.id, "macro_id": macro.id}).body_html

    # Assistants : chacun ne voit que les siens
    def test_wizard_rows_belong_to_their_creator(self):
        ticket = self._ticket()
        other = new_test_user(self.env, login="agent-voisin-revue", email="voisin.revue@example.com",
                              groups="base.group_user,helpdesk_mgmt.group_helpdesk_user_team")
        self.team.user_ids = [(4, other.id)]
        macro = self.env["helpdesk.macro"].create({"name": "Relance", "body_html": "<p>x</p>"})
        rows = [
            self.env["helpdesk.triage.apply"].with_user(self.agent).create({"ticket_id": ticket.id}),
            self.env["helpdesk.macro.apply.wizard"].with_user(self.agent).create(
                {"ticket_id": ticket.id, "macro_id": macro.id}),
        ]
        for row in rows:
            with self.subTest(model=row._name):
                self.assertFalse(self.env[row._name].with_user(other).search([("id", "=", row.id)]))
                self.assertTrue(self.env[row._name].with_user(self.agent).search([("id", "=", row.id)]))

    # Vue 360 : seulement les billets que l'agent voit
    def test_client360_counts_only_visible_tickets(self):
        foreign, agent = self._foreign_ticket_and_team_agent("agent-360-revue")
        mine = self._ticket()
        self.env.invalidate_all()  # valeur calculée à la création du billet
        self.assertIn(foreign, mine.sudo().bf_client_ticket_ids)
        self.assertNotIn(foreign, mine.with_user(agent).bf_client_ticket_ids)
        self.assertEqual(mine.with_user(agent).bf_client_open_ticket_count, 0)

    # Échéances, relances, triage, heures : illisibles au portail
    def test_portal_cannot_read_sla_and_reminders(self):
        portal = new_test_user(self.env, login="portail-echeance-revue", email="echeance.revue@example.com",
                               groups="base.group_portal")
        ticket = self._ticket(partner_id=portal.partner_id.id)
        ticket.message_subscribe(partner_ids=portal.partner_id.ids)
        for fname in ("sla_state", "sla_resolve_deadline", "reminder_count", "theme_id",
                      "triage_state", "total_hours", "bf_client_hosting_count"):
            with self.assertRaises(AccessError, msg=fname):
                ticket.with_user(portal).read([fname])

    # Le contexte d'un client ne fabrique pas d'événement d'agent
    def test_portal_context_cannot_fake_agent_event(self):
        portal = new_test_user(self.env, login="portail-evenement-revue", email="evenement.revue@example.com",
                               groups="base.group_portal")
        ticket = self._ticket(partner_id=portal.partner_id.id)
        event = ticket.with_user(portal).with_context(bf_hd_event="sla_breach")._bf_agent_event(
            self.env["mail.message"], {})
        self.assertNotEqual(event, "sla_breach")
        self.assertEqual(ticket.with_context(bf_hd_event="sla_breach")._bf_agent_event(
            self.env["mail.message"], {}), "sla_breach")

    # Article : un brouillon tiré du billet d'une autre équipe reste caché
    def test_article_draft_scoped_to_team(self):
        foreign, agent = self._foreign_ticket_and_team_agent("agent-article-revue")
        article = self.env["helpdesk.article"].create({
            "name": "Brouillon", "slug": "brouillon-revue", "source_ticket_id": foreign.id,
            "is_published": False})
        Article = self.env["helpdesk.article"].with_user(agent)
        self.assertFalse(Article.search([("id", "=", article.id)]))
        article.is_published = True
        self.assertTrue(Article.search([("id", "=", article.id)]))

    # Présence : bornée aux sociétés de l'usager
    def test_presence_scoped_to_company(self):
        other_company = self.env["res.company"].create({"name": "Autre société présence"})
        ticket = self._ticket()
        ticket.sudo().company_id = other_company
        self.env["helpdesk.ticket.presence"].sudo().create({
            "ticket_id": ticket.id, "user_id": self.agent.id, "last_seen": fields.Datetime.now()})
        manager = new_test_user(self.env, login="gestion-presence-revue", email="presence.gestion@example.com",
                                groups="base.group_user,helpdesk_mgmt.group_helpdesk_manager")
        self.assertFalse(self.env["helpdesk.ticket.presence"].with_user(manager).search(
            [("ticket_id", "=", ticket.id)]))

    # Onchange : ni un défaut du contexte, ni un défaut par usager, ni une sous-ligne
    def test_onchange_refuses_foreign_ticket_from_defaults_and_lines(self):
        foreign, agent = self._foreign_ticket_and_team_agent("agent-defauts-revue")
        name_spec = {"ticket_id": {"fields": {"display_name": {}}}}
        Log = self.env["helpdesk.triage.log"].with_user(agent)
        with self.assertRaises(AccessError):
            Log.with_context(default_ticket_id=foreign.id).onchange({}, [], name_spec)
        self.env["ir.default"].with_user(agent).set(
            "helpdesk.triage.log", "ticket_id", foreign.id, user_id=True)
        with self.assertRaises(AccessError):
            Log.onchange({}, [], name_spec)
        with self.assertRaises(AccessError):
            self.Ticket.with_user(agent).onchange(
                {"triage_log_ids": [[0, "v1", {"ticket_id": foreign.id}]]}, ["triage_log_ids"],
                {"triage_log_ids": {"fields": name_spec}})

    # Recherche par persona : réservée au rôle persona
    def test_persona_search_needs_persona_role(self):
        portal = new_test_user(self.env, login="portail-persona-recherche",
                               email="persona.recherche@example.com", groups="base.group_portal")
        agent = new_test_user(self.env, login="agent-persona-recherche",
                              email="agent.persona.recherche@example.com",
                              groups="base.group_user,helpdesk_mgmt.group_helpdesk_user")
        for user in (portal, agent):
            with self.subTest(user=user.login), self.assertRaises(AccessError):
                self.Ticket.with_user(user).search_count([("persona_payer_quality", "=", "poor")])
        role = new_test_user(self.env, login="agent-persona-role", email="persona.role@example.com",
                             groups="base.group_user,helpdesk_mgmt.group_helpdesk_user,"
                                    "bf_persona.group_persona_user")
        self.Ticket.with_user(role).search_count([("persona_payer_quality", "=", "poor")])

    # Ligne de temps neuve : le client d'un billet illisible ne sort pas
    def test_new_timesheet_line_hides_unreadable_ticket_client(self):
        foreign, agent = self._foreign_ticket_and_team_agent("agent-ligne-temps-revue")
        mine = self._ticket()
        Line = self.env["account.analytic.line"].with_user(agent)
        self.env.invalidate_all()
        self.assertFalse(Line.new({"ticket_id": foreign.id}).ticket_partner_id)
        self.assertEqual(Line.new({"ticket_id": mine.id}).ticket_partner_id, mine.partner_id)

    # Onchange : un sondage d'un autre billet ne se lie pas par commande
    def test_onchange_refuses_linking_unreadable_csat(self):
        foreign, agent = self._foreign_ticket_and_team_agent("agent-lien-sondage-revue")
        csat = self.env["helpdesk.ticket.csat"].sudo().create({"ticket_id": foreign.id})
        with self.assertRaises(AccessError):
            self.Ticket.with_user(agent).onchange(
                {"csat_ids": [[4, csat.id]]}, ["csat_ids"], {"csat_rating": {}})

    # Réglages internes de l'équipe : illisibles au portail
    def test_portal_cannot_read_team_settings(self):
        portal = new_test_user(self.env, login="portail-equipe-revue", email="equipe.portail@example.com",
                               groups="base.group_portal")
        for fname in ("hour_bank_id", "ntfy_critical_enabled", "csat_followup_user_id"):
            with self.assertRaises(AccessError, msg=fname):
                self.team.with_user(portal).read([fname])

    # Portail : la date du client se forme même quand son contact est d'une autre société
    def test_client_date_for_follower_of_other_company(self):
        other_company = self.env["res.company"].create({"name": "Autre société fuseau"})
        client = self.env["res.partner"].create({
            "name": "Client ailleurs", "email": "ailleurs@example.com",
            "company_id": other_company.id, "tz": "Europe/Paris"})
        portal = new_test_user(self.env, login="portail-abonne-revue", email="abonne.revue@example.com",
                               groups="base.group_portal")
        self.team.sla_calendar_id = False
        ticket = self._ticket(partner_id=client.id)
        ticket.message_subscribe(partner_ids=portal.partner_id.ids)
        self.env.invalidate_all()
        self.assertTrue(ticket.with_user(portal)._bf_format_client_datetime(fields.Datetime.now()))

    # Réponse de sondage : jamais posée par un usager
    def test_agent_cannot_attach_a_survey_answer(self):
        survey = self.env["survey.survey"].create({"title": "Sondage revue"})
        answer = survey._create_answer(email="sondage.autre@example.com")
        ticket = self._ticket()
        with self.assertRaises(AccessError):
            ticket.with_user(self.agent).write({"csat_user_input_id": answer.id})

    # Salutation du persona : réservée au rôle persona
    def test_macro_salutation_needs_persona_role(self):
        self.env["contact.persona"].create({
            "partner_id": self.client.id, "preferred_salutation": "Salut à toi, ami revue"})
        ticket = self._ticket()
        Macro = self.env["helpdesk.macro"]
        self.assertNotIn("ami revue", Macro.with_user(self.agent)._bf_macro_values(ticket)["salutation"])
        role = new_test_user(self.env, login="agent-salutation-role", email="salutation.role@example.com",
                             groups="base.group_user,helpdesk_mgmt.group_helpdesk_user,"
                                    "bf_persona.group_persona_user")
        self.assertIn("ami revue", Macro.with_user(role)._bf_macro_values(ticket)["salutation"])

    # Un agent d'assistance sans accès aux projets ouvre un billet dont l'équipe a une banque
    def test_helpdesk_agent_reads_hour_bank_balance(self):
        project = self.env["project.project"].create({"name": "Projet banque revue"})
        bank = self.env["hour.bank.client"].create(
            {"partner_id": self.client.id, "project_ids": [(6, 0, project.ids)]})
        self.team.hour_bank_id = bank
        ticket = self._ticket()
        agent = new_test_user(self.env, login="agent-banque-revue", email="banque.revue@example.com",
                              groups="base.group_user,helpdesk_mgmt.group_helpdesk_user")
        self.assertFalse(agent.has_group("project.group_project_user"))
        self.env.invalidate_all()
        ticket.with_user(agent).read(["hour_bank_balance", "hour_bank_low"])

    # Article : pas de déplacement vers une société de laquelle l'agent n'est pas
    def test_article_stays_in_the_agent_companies(self):
        other_company = self.env["res.company"].create({"name": "Autre société article"})
        agent = new_test_user(self.env, login="agent-article-societe", email="article.societe@example.com",
                              groups="base.group_user,helpdesk_mgmt.group_helpdesk_user_team")
        article = self.env["helpdesk.article"].with_user(agent).create(
            {"name": "Article à moi", "slug": "article-a-moi-revue"})
        with self.assertRaises(AccessError):
            article.with_user(agent).write({"company_id": other_company.id})
        # Sans société, il paraîtrait au centre d'aide de toutes les sociétés.
        with self.assertRaises(AccessError):
            article.with_user(agent).write({"company_id": False})

    # Préférence de notification : on ne la donne pas à un autre agent
    def test_notify_pref_cannot_be_given_to_another_agent(self):
        a = new_test_user(self.env, login="agent-pref-a", email="pref.a@example.com",
                          groups="base.group_user,helpdesk_mgmt.group_helpdesk_user_team")
        b = new_test_user(self.env, login="agent-pref-b", email="pref.b@example.com",
                          groups="base.group_user,helpdesk_mgmt.group_helpdesk_user_team")
        pref = self.env["helpdesk.notify.pref"].with_user(a).create(
            {"event": "sla_breach", "channel": "none"})
        with self.assertRaises(AccessError):
            pref.with_user(a).write({"user_id": b.id})

    # Réponse de sondage : ne s'efface pas non plus
    def test_agent_cannot_clear_a_survey_answer(self):
        survey = self.env["survey.survey"].create({"title": "Sondage effacé revue"})
        ticket = self._ticket()
        ticket.write({"csat_user_input_id": survey._create_answer(email="efface@example.com").id})
        with self.assertRaises(AccessError):
            ticket.with_user(self.agent).write({"csat_user_input_id": False})

    # Persona : l'action d'ouverture est réservée à son rôle
    def test_open_persona_needs_persona_role(self):
        portal = new_test_user(self.env, login="portail-ouvre-persona", email="ouvre.persona@example.com",
                               groups="base.group_portal")
        ticket = self._ticket(partner_id=portal.partner_id.id)
        with self.assertRaises(AccessError):
            ticket.with_user(portal).action_open_persona()

    # Pièces jointes : hors du formulaire web
    def test_attachments_not_open_to_website_forms(self):
        field = self.env["ir.model.fields"]._get("helpdesk.ticket", "attachment_ids")
        self.assertTrue(field.website_form_blacklisted)

    # Vue 360 : le dernier sondage vient aussi du sondage natif
    def test_client360_last_native_survey(self):
        mine = self._ticket()
        other = self._ticket(name="Autre demande du client")
        self.env["helpdesk.ticket.csat"].sudo().create({
            "ticket_id": other.id, "state": "answered", "rating": "4",
            "answered_date": fields.Datetime.now()})
        self.env.invalidate_all()
        last = mine.with_user(self.agent).bf_client_last_csat
        self.assertTrue(last and "4/5" in last and other.number in last, last)

    # Le menu racine ne porte pas le nom de l'éditeur
    def test_root_menu_label_is_generic(self):
        menu = self.env.ref("helpdesk_mgmt.helpdesk_ticket_main_menu")
        self.assertEqual(menu.with_context(lang="en_US").name, "Helpdesk")

