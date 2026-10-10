# -*- coding: utf-8 -*-
"""The notice of 18.0.2.0.0: layout, content, project manager, game plan."""
import socket
from datetime import datetime
from unittest.mock import patch

from odoo.fields import Command
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_task_unblock_notify.models.unblock_event import (
    FULL_AT, LEADER, PLAN_ALLOWED_PARAM, TaskUnblockEvent, _ask_bridge, clean_plan, clean_text,
    is_clean)


class FakeTransport:
    """Stands for bf_ai_bridge's transport: records what the pass would read."""

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls = answer, error, []

    def post(self, socket_path, endpoint, payload, timeout):
        self.calls.append((endpoint, payload, timeout))
        if self.error:
            raise self.error
        return self.answer


PLAN = {
    "situation": ["**Les ratios sont livrés** dans le fichier maître.",
                  "Voir https://exemple.invalid/piege pour la suite.",
                  "0 h saisie sur 3,5 h.", "Une quatrième puce de trop."],
    "next_actions": ["Monter une première version avant mardi."],
}


@tagged("post_install", "-at_install")
class TestUnblockNotice(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.ref("base.group_user").write({"implied_ids": [
            Command.link(cls.env.ref("project.group_project_task_dependencies").id)]})
        groupes = [Command.set([cls.env.ref("project.group_project_user").id])]
        cls.closer = cls.env["res.users"].create({
            "name": "Closer Auckland", "login": "closer.unblock@example.com",
            "email": "closer.unblock@example.com", "lang": "en_US",
            "tz": "Pacific/Auckland", "groups_id": groupes,
        })
        cls.assignee = cls.env["res.users"].create({
            "name": "Assignee Toronto", "login": "assignee.unblock@example.com",
            "email": "assignee.unblock@example.com", "lang": "en_US",
            "tz": "America/Toronto", "groups_id": groupes,
        })
        cls.manager = cls.env["res.users"].create({
            "name": "Manager Unblock", "login": "manager.unblock@example.com",
            "email": "manager.unblock@example.com", "lang": "en_US", "groups_id": groupes,
        })
        cls.client = cls.env["res.partner"].create({"name": "Client Unblock"})
        cls.other_client = cls.env["res.partner"].create({"name": "Other Client"})
        cls.project = cls.env["project.project"].create({
            "name": "Unblock notice project", "allow_task_dependencies": True,
            "partner_id": cls.client.id, "user_id": cls.manager.id,
            "privacy_visibility": "employees",
        })
        cls.other_project = cls.env["project.project"].create({
            "name": "Elsewhere project", "allow_task_dependencies": True,
            "partner_id": cls.other_client.id, "privacy_visibility": "employees",
        })
        cls.private_project = cls.env["project.project"].create({
            "name": "Private project", "allow_task_dependencies": True,
            "privacy_visibility": "followers",
        })
        cls.Event = cls.env["bf.task.unblock.event"]
        cls.company = cls.env.company

    def _task(self, name, project=None, **vals):
        return self.env["project.task"].create(dict(
            {"name": name, "project_id": (project or self.project).id}, **vals))

    def _waiting(self, *blockers, **vals):
        task = self._task("Waiting task", user_ids=[Command.set([self.assignee.id])], **vals)
        task.write({"depend_on_ids": [Command.set([b.id for b in blockers])]})
        self.assertEqual(task.state, "04_waiting_normal")
        return task

    def _notices(self, task, partner, after=0):
        return self.env["mail.message"].search([
            ("model", "=", "project.task"), ("res_id", "=", task.id),
            ("message_type", "=", "user_notification"),
            ("partner_ids", "in", partner.ids), ("id", ">", after),
        ])

    def _last_id(self):
        return self.env["mail.message"].search([], order="id desc", limit=1).id

    # ------------------------------------------------------------------
    # Layout and content
    # ------------------------------------------------------------------
    def test_layout_prefers_the_brand(self):
        branded = self.env.ref("bluefox_branding.bf_mail_layout", raise_if_not_found=False)
        expected = ("bluefox_branding.bf_mail_layout" if branded
                    else "mail.mail_notification_light")
        self.assertEqual(self.Event._layout(), expected)

    def test_notice_content(self):
        same = self._task("Blocker same project")
        elsewhere = self._task("Blocker elsewhere", project=self.other_project)
        deadline = datetime(2026, 10, 16, 18, 0)
        waiting = self._waiting(same, elsewhere, date_deadline=deadline,
                                allocated_hours=3.5, priority="1")
        after = self._last_id()
        (same | elsewhere).with_user(self.closer).write({"state": "1_done"})
        notice = self._notices(waiting, self.assignee.partner_id, after)
        self.assertEqual(len(notice), 1)
        body = notice.body
        # Q1, Q2: the recipient's time zone, not the closer's; no seconds.
        event = self.Event.search([("task_id", "=", waiting.id)])
        toronto = event.with_context(lang="en_US", tz="America/Toronto")._when(event.create_date)
        auckland = event.with_context(lang="en_US", tz="Pacific/Auckland")._when(event.create_date)
        self.assertIn(toronto, body)
        self.assertNotIn(auckland, body)
        self.assertNotRegex(body, r"\d{1,2}:\d{2}:\d{2}")
        # Q3: the same project and client are not repeated for the blocker.
        self.assertEqual(body.count("Unblock notice project"), 1)
        self.assertIn("Elsewhere project", body)
        self.assertIn("Other Client", body)
        # Q4: each blocker is a link.
        self.assertIn("res_id=%s" % same.id, body)
        self.assertIn("res_id=%s" % elsewhere.id, body)
        # Q5: deadline, planned hours, priority.
        self.assertIn("Deadline:", body)
        self.assertIn("3.5 h", body)
        self.assertIn("High priority", body)
        self.assertIn("Closer Auckland", body)
        self.assertEqual(event.state, "sent")
        self.assertFalse(event.with_plan)

    def test_french_formats(self):
        event = self.Event.with_context(lang="fr_CA", tz="America/Toronto")
        self.assertEqual(event._hours(3.5), "3,5 h")
        self.assertIn(" h ", event._when(datetime(2026, 10, 10, 0, 15, 20)))
        self.assertEqual(event._when(datetime(2026, 10, 10, 0, 15, 20)), "9 oct., 20 h 15")

    def test_signature_is_left_to_the_layout(self):
        blocker = self._task("Blocker signature")
        waiting = self._waiting(blocker)
        after = self._last_id()
        blocker.write({"state": "1_done"})
        notice = self._notices(waiting, self.assignee.partner_id, after)
        self.assertFalse(notice.email_add_signature)

    def test_dependency_removed_names_who_and_what(self):
        blocker = self._task("Link to drop")
        waiting = self._waiting(blocker)
        after = self._last_id()
        waiting.with_user(self.closer).write({"depend_on_ids": [Command.clear()]})
        notice = self._notices(waiting, self.assignee.partner_id, after)
        self.assertEqual(len(notice), 1)
        self.assertIn("removed the dependency", notice.body)
        self.assertIn("Link to drop", notice.body)
        self.assertIn("Closer Auckland", notice.body)

    # ------------------------------------------------------------------
    # Unassigned task (Q7)
    # ------------------------------------------------------------------
    def test_unassigned_task_notifies_the_project_manager(self):
        self.company.unblock_notify_manager = True
        blocker = self._task("Blocker unassigned")
        # Odoo assigns the creator of a task: cleared, as a person would.
        waiting = self._task("Nobody on it")
        waiting.write({"user_ids": [Command.clear()], "depend_on_ids": [Command.set([blocker.id])]})
        after = self._last_id()
        blocker.write({"state": "1_done"})
        notice = self._notices(waiting, self.manager.partner_id, after)
        self.assertEqual(len(notice), 1)
        self.assertIn("project manager", notice.body)

    def test_unassigned_task_stays_silent_when_switched_off(self):
        self.company.unblock_notify_manager = False
        blocker = self._task("Blocker silent")
        waiting = self._task("Nobody on it either")
        waiting.write({"user_ids": [Command.clear()], "depend_on_ids": [Command.set([blocker.id])]})
        blocker.write({"state": "1_done"})
        self.assertFalse(self.Event.search([("task_id", "=", waiting.id)]))

    # ------------------------------------------------------------------
    # Game plan
    # ------------------------------------------------------------------
    def _with_plan(self):
        self.company.unblock_gen_plan = True
        self.env["ir.config_parameter"].sudo().set_param(PLAN_ALLOWED_PARAM, "True")
        return patch.object(TaskUnblockEvent, "_gen_installed", return_value=True)

    def test_clean_plan(self):
        plan = clean_plan(PLAN)
        self.assertEqual(len(plan["situation"]), 3)
        self.assertNotIn("**", plan["situation"][0])
        self.assertNotIn("https://", plan["situation"][1])
        self.assertIsNone(clean_plan({"situation": "not a list"}))
        self.assertIsNone(clean_plan(None))
        self.assertIsNone(clean_plan({"situation": [3, None], "next_actions": []}))

    def test_plan_waits_then_leaves_once_with_the_plan(self):
        blocker = self._task("Blocker with plan")
        blocker.message_post(body="Delivered: the ratios are in the master file.",
                             message_type="comment", subtype_xmlid="mail.mt_note")
        secret = self._task("Secret blocker", project=self.private_project)
        waiting = self._waiting(blocker, secret)
        after = self._last_id()
        with self._with_plan():
            (blocker | secret).write({"state": "1_done"})
            event = self.Event.search([("task_id", "=", waiting.id)])
            self.assertTrue(event.with_plan)
            self.assertEqual(event.state, "pending")
            self.assertFalse(self._notices(waiting, self.assignee.partner_id, after))
            token = event._claim()
            self.assertTrue(token)
            self.assertFalse(event._claim(), "a notice is claimed once")
            requests, eligible = event._plan_requests()
        self.assertEqual(eligible, [self.assignee.partner_id.id])
        payload = requests[0]["payload"]
        # Read as the recipient: the private blocker is not in what Gen reads.
        names = [b["name"] for b in payload["blockers"]]
        self.assertIn("Blocker with plan", names)
        # What the blocker delivered reaches the pass: its last notes.
        self.assertIn("ratios are in the master file", payload["blockers"][0]["thread"][0])
        self.assertEqual(payload["unblock"]["by"], self.env.user.name)
        self.assertNotIn("Secret blocker", names)
        self.assertEqual(payload["recipient"], "Assignee Toronto")
        transport = FakeTransport(answer=PLAN)
        plans = dict.fromkeys(eligible)
        outcome = _ask_bridge(("/nowhere.sock", "bf", transport), requests, plans)
        self.assertEqual(outcome, "ok")
        self.assertEqual(transport.calls[0][0], "/task-unblock-plan")
        self.assertEqual(transport.calls[0][1]["tenant"], "bf")
        self.assertGreaterEqual(transport.calls[0][1]["budget_s"], 45)
        # A message from someone outside the company is said to be.
        client_note = waiting.message_post(
            body="Please wire the deposit.", message_type="comment",
            author_id=self.client.id, subtype_xmlid="mail.mt_comment")
        self.assertTrue(client_note)
        self.assertTrue(event._send(plans, outcome, token=token))
        self.assertFalse(event._send(plans, outcome, token=token), "sent once")
        notice = self._notices(waiting, self.assignee.partner_id, after)
        self.assertEqual(len(notice), 1)
        self.assertIn("Game plan proposed by Gen", notice.body)
        self.assertIn("Monter une première version avant mardi.", notice.body)
        self.assertNotIn("exemple.invalid", notice.body)
        # 🔴 The notice does not name a blocker the recipient cannot open.
        self.assertNotIn("Secret blocker", notice.body)
        self.assertNotIn("Private project", notice.body)
        self.assertIn("which you cannot open", notice.body)
        self.assertEqual(event.plan_outcome, "ok")

    def test_plan_failure_still_sends_and_says_so(self):
        blocker = self._task("Blocker timeout")
        waiting = self._waiting(blocker)
        after = self._last_id()
        with self._with_plan():
            blocker.write({"state": "1_done"})
            event = self.Event.search([("task_id", "=", waiting.id)])
            token = event._claim()
            requests, eligible = event._plan_requests()
        plans = dict.fromkeys(eligible)
        outcome = _ask_bridge(
            ("/nowhere.sock", "bf", FakeTransport(error=socket.timeout())), requests, plans)
        self.assertEqual(outcome, "timeout")
        event._send(plans, outcome, token=token)
        notice = self._notices(waiting, self.assignee.partner_id, after)
        self.assertEqual(len(notice), 1)
        self.assertIn("could not prepare a game plan", notice.body)
        self.assertEqual(event.plan_outcome, "timeout")

    def test_missing_route_stops_asking(self):
        transport = FakeTransport(error=ValueError("Bridge HTTP 404: Not Found"))
        requests = [{"partner_id": 1, "payload": {}}, {"partner_id": 2, "payload": {}}]
        plans = {1: None, 2: None}
        self.assertEqual(_ask_bridge(("/s", "bf", transport), requests, plans), "no_route")
        self.assertEqual(len(transport.calls), 1)

    def test_no_bridge_no_plan(self):
        self.assertEqual(_ask_bridge(None, [{"partner_id": 1, "payload": {}}], {1: None}),
                         "unavailable")

    def test_cron_sends_what_waited_too_long(self):
        blocker = self._task("Blocker late")
        waiting = self._waiting(blocker)
        after = self._last_id()
        with self._with_plan():
            blocker.write({"state": "1_done"})
        event = self.Event.search([("task_id", "=", waiting.id)])
        self.assertEqual(event.state, "pending")
        self.env.cr.execute(
            "UPDATE bf_task_unblock_event SET create_date = create_date - interval '5 minutes' "
            "WHERE id = %s", [event.id])
        event.invalidate_recordset()
        self.Event._cron_send_late()
        self.assertEqual(event.state, "sent")
        self.assertEqual(event.plan_outcome, "late")
        self.assertEqual(len(self._notices(waiting, self.assignee.partner_id, after)), 1)

    def test_cron_takes_over_a_lost_thread(self):
        blocker = self._task("Blocker lost")
        waiting = self._waiting(blocker)
        with self._with_plan():
            blocker.write({"state": "1_done"})
        event = self.Event.search([("task_id", "=", waiting.id)])
        token = event._claim()
        self.env.cr.execute(
            "UPDATE bf_task_unblock_event SET claimed_at = claimed_at - interval '11 minutes' "
            "WHERE id = %s RETURNING claimed_at", [event.id])
        old = self.env.cr.fetchone()[0]
        event.invalidate_recordset()
        self.Event._cron_send_late()
        self.assertEqual(event.state, "sent")
        # The thread that comes back late finds nothing to send.
        self.assertFalse(event._send({}, "ok", token=token))
        self.assertFalse(event._send({}, "ok", token=old))

    def test_settings_section_follows_gen(self):
        settings = self.env["res.config.settings"].create({})
        self.assertEqual(settings.unblock_gen_available, self.Event._plan_available())
        action = self.env["onboarding.onboarding.step"].action_open_bf_task_unblock_settings()
        self.assertEqual(action["res_model"], "res.config.settings")


    def test_plan_needs_the_database_allowance(self):
        self.env["ir.config_parameter"].sudo().set_param(PLAN_ALLOWED_PARAM, "False")
        self.company.unblock_gen_plan = True
        with patch.object(TaskUnblockEvent, "_gen_installed", return_value=True):
            self.assertFalse(self.Event._plan_available())
            blocker = self._task("Blocker not allowed")
            waiting = self._waiting(blocker)
            blocker.write({"state": "1_done"})
        event = self.Event.search([("task_id", "=", waiting.id)])
        self.assertFalse(event.with_plan)
        self.assertEqual(event.state, "sent")

    def test_hidden_blocker_named_nowhere_without_plan(self):
        visible = self._task("Visible blocker")
        secret = self._task("Hidden blocker", project=self.private_project)
        waiting = self._waiting(visible, secret)
        after = self._last_id()
        (visible | secret).write({"state": "1_done"})
        notice = self._notices(waiting, self.assignee.partner_id, after)
        self.assertEqual(len(notice), 1)
        self.assertIn("Visible blocker", notice.body)
        self.assertNotIn("Hidden blocker", notice.body)
        self.assertNotIn("res_id=%s" % secret.id, notice.body)

    def test_grouped_view_context_does_not_lose_the_notice(self):
        blocker = self._task("Blocker dragged in kanban")
        waiting = self._waiting(blocker)
        after = self._last_id()
        blocker.with_context(default_state="01_in_progress", default_kind="x").write(
            {"state": "1_done"})
        self.assertEqual(len(self._notices(waiting, self.assignee.partner_id, after)), 1)

    def test_clean_text_leaves_nothing_a_mail_client_would_link(self):
        pieges = (
            "voirhttps://x.test/a", "_https://x.test", "hxxps://x.test", "ftp://x.test",
            "mailto:paie@evil.test", "paie@evil.com", "evil-portail.com/connexion", "bit.ly/abc",
            "h\u200bttps://x.com", "\uff48\uff54\uff54\uff50\uff53://x.com", "//evil.example.com",
            "www.piege.test", "secure-login.support", "login-portal.help", "45.33.32.156/login",
            "portail.microsoft-365.services", "evil\u2066.com",
            # Markup removed AFTER the patterns used to rebuild them.
            "Ouvrir https:/*`*/evil*`*.com/login", "paie@evil*`*.com", "mailto*`*:paie@evil*`*.com",
            "www*`*.evil*`*.com", "e**v**i**l.c**o**m",
            # What comes before the dot need not be a letter for Python.
            "\u0e14\u0e35.com/login", "\u0936\u093f\u0915\u094d\u0937\u093e.\u092d\u093e\u0930\u0924/x",
            "i\u2764.ws", "evil\u0336.com", "evil\u3002com", "evil\uff61com", "evil\u2024com",
            # Invisible marks after the dot, schemes without « // ».
            "evil.\u180bcom/login", "evil.\u17b4com", "evil.\ua7f1hop", "https:134744072/login",
            "http:/134744072", "https:\\\\134744072\\x", "wss:134744072", "https:0x8080808/x",
            "\\\\134744072\\share", "search-ms:query=x", "ms-msdt:/id", "skype:evil?call",
        )
        for piege in pieges:
            nettoye = clean_text("Avant %s après" % piege)
            self.assertTrue(is_clean(nettoye), (piege, nettoye))
            self.assertNotIn("://", nettoye, piege)
        # A cut at the line limit leaves no live domain behind.
        long = clean_text("a" * 461 + " //" * 5 + " evil.commune")
        self.assertTrue(is_clean(long), long)
        # Blind fuzz: markup and invisibles between every character.
        for piege in ("https://evil.com/x", "paie@evil.com", "evil.com", "www.evil.com"):
            for glue in ("*", "`", "\u200b", "\u2066", "**", "*`*"):
                self.assertTrue(is_clean(clean_text(glue.join(piege))), (piege, glue))

    def test_clean_text_keeps_plain_words_readable(self):
        for texte in ("La ligne 6.6.1, version 18.0.2.0.0, 3,5 h.", "Le PDF (Synthèses.pdf) et server.py.",
                      "Échéance : le 16 octobre.", "Deadline: Oct 16.", "Data: the totals. SMS : rien."):
            nettoye = clean_text(texte)
            self.assertEqual(nettoye.replace(LEADER, ".").replace(FULL_AT, "@"), texte)
        self.assertEqual(clean_text("Écrire à paie@evil.com"), "Écrire à paie%sevil%scom" % (FULL_AT, LEADER))
        # A phone number costs the line; a date or a version does not.
        self.assertEqual(clean_text("Appeler le 1 888 555-0199 demain."), "")
        self.assertEqual(clean_text("Rappeler au (514) 555.0199"), "")
        self.assertTrue(clean_text("Version 18.0.2.0.0 du 2026-10-16, 1 263 factures."))
        # Words that end in a scheme name are words.
        self.assertEqual(clean_text("Profile:admin Metadata:x").replace(LEADER, "."), "Profile:admin Metadata:x")
        # A very long line is cut before it is cleaned.
        import time
        debut = time.monotonic()
        clean_text("a" * 40000 + "://")
        self.assertLess(time.monotonic() - debut, 1)

    def test_other_company_of_the_closer_does_not_lose_the_notice(self):
        other = self.env["res.company"].create({"name": "Unblock second company"})
        groupes = [Command.set([self.env.ref("project.group_project_user").id])]
        elsewhere = self.env["res.users"].create({
            "name": "Assignee second company", "login": "assignee.c2@example.com",
            "email": "assignee.c2@example.com", "company_id": other.id,
            "company_ids": [Command.set([other.id])], "groups_id": groupes,
        })
        self.closer.write({"company_ids": [Command.link(other.id)]})
        project = self.env["project.project"].create({
            "name": "Second company project", "allow_task_dependencies": True,
            "company_id": other.id, "privacy_visibility": "employees",
        })
        blocker = self.env["project.task"].create({"name": "Blocker c2", "project_id": project.id})
        waiting = self.env["project.task"].create({
            "name": "Waiting c2", "project_id": project.id,
            "user_ids": [Command.set([elsewhere.id])]})
        waiting.write({"depend_on_ids": [Command.set([blocker.id])]})
        after = self._last_id()
        # The closer works with both companies active, the assignee has only the second.
        blocker.with_user(self.closer).with_context(
            allowed_company_ids=[self.company.id, other.id]).write({"state": "1_done"})
        notice = self._notices(waiting, elsewhere.partner_id, after)
        self.assertEqual(len(notice), 1)
        self.assertIn("Blocker c2", notice.body)

    def test_late_send_says_the_plan_is_missing(self):
        blocker = self._task("Blocker late with mention")
        waiting = self._waiting(blocker)
        after = self._last_id()
        with self._with_plan():
            blocker.write({"state": "1_done"})
            event = self.Event.search([("task_id", "=", waiting.id)])
            self.env.cr.execute(
                "UPDATE bf_task_unblock_event SET create_date = create_date - interval '5 minutes' "
                "WHERE id = %s", [event.id])
            event.invalidate_recordset()
            self.Event._cron_send_late()
        notice = self._notices(waiting, self.assignee.partner_id, after)
        self.assertIn("could not prepare a game plan", notice.body)

    def test_failing_notice_is_marked_failed(self):
        blocker = self._task("Blocker failing")
        waiting = self._waiting(blocker)
        with self._with_plan():
            blocker.write({"state": "1_done"})
            event = self.Event.search([("task_id", "=", waiting.id)])
            self.env.cr.execute(
                "UPDATE bf_task_unblock_event SET create_date = create_date - interval '5 minutes' "
                "WHERE id = %s", [event.id])
            event.invalidate_recordset()
            with patch.object(TaskUnblockEvent, "_notify_partner", side_effect=RuntimeError("boom")):
                for _ in range(3):
                    self.Event._cron_send_late()
        self.assertEqual(event.state, "failed")
        self.assertEqual(event.attempts, 3)

    def test_one_thread_per_transaction(self):
        blockers = self._task("Blocker A") | self._task("Blocker B")
        first = self._waiting(blockers[0])
        second = self._waiting(blockers[1])
        with self._with_plan():
            before = len(self.env.cr.postcommit._funcs)
            blockers.write({"state": "1_done"})
            self.assertEqual(len(self.env.cr.postcommit._funcs), before + 1)
        ids = self.env.cr.postcommit.data["bf_task_unblock_notify.event_ids"]
        events = self.Event.search([("task_id", "in", (first | second).ids)])
        self.assertEqual(sorted(ids), sorted(events.ids))


    def test_external_author_is_marked(self):
        waiting = self._task("Task with a client message", user_ids=[Command.set([self.assignee.id])])
        waiting.message_post(body="Please wire the deposit.", message_type="comment",
                             author_id=self.client.id, subtype_xmlid="mail.mt_comment")
        event = self.Event.create({
            "state": "sent", "task_id": waiting.id, "kind": "blocker_closed",
            "partner_ids": [Command.set(self.assignee.partner_id.ids)]})
        payload = event._plan_context(self.assignee)
        self.assertTrue(any("Client Unblock (external)" in line for line in payload["thread"]),
                        payload["thread"])
        waiting.message_post(body="Inbound mail.", message_type="email",
                             author_id=self.assignee.partner_id.id, subtype_xmlid="mail.mt_comment")
        payload = event._plan_context(self.assignee)
        self.assertTrue(any("Assignee Toronto (by email)" in line for line in payload["thread"]),
                        payload["thread"])
        self.assertEqual(payload["unblock"]["by"], "", "no blocker visible, nobody named")
