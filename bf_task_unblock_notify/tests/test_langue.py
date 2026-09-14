# -*- coding: utf-8 -*-
"""The unblock notification speaks each assignee's language.

It used to be rendered once, in the language of whoever closed the blocking
task, and sent as is to every assignee.
"""

from odoo.fields import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestUnblockNotificationLanguage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_task_unblock_notify"], ["fr_CA"], overwrite=True)
        # Task dependencies are a setting: without its group, a task never waits.
        cls.env.ref("base.group_user").write({"implied_ids": [
            Command.link(cls.env.ref("project.group_project_task_dependencies").id)]})
        groupes = [Command.set([cls.env.ref("project.group_project_user").id])]
        cls.francoise = cls.env["res.users"].create({
            "name": "Françoise Déblocage", "login": "francoise.deblocage@example.com",
            "email": "francoise.deblocage@example.com", "lang": "fr_CA", "groups_id": groupes,
        })
        cls.jane = cls.env["res.users"].create({
            "name": "Jane Unblock", "login": "jane.unblock@example.com",
            "email": "jane.unblock@example.com", "lang": "en_US", "groups_id": groupes,
        })
        cls.projet = cls.env["project.project"].create({
            "name": "Unblock language project", "allow_task_dependencies": True,
        })
        cls.bloquante = cls.env["project.task"].create({
            "name": "Blocking task", "project_id": cls.projet.id,
        })
        cls.attente = cls.env["project.task"].create({
            "name": "Waiting task", "project_id": cls.projet.id,
            "user_ids": [Command.set([cls.francoise.id, cls.jane.id])],
        })
        # Linked after creation, as a person does: that is what puts it on hold.
        cls.attente.write({"depend_on_ids": [Command.link(cls.bloquante.id)]})

    def _notifications(self, user, avant=0):
        # Only what the unblocking sent: the assignment notice came before.
        return self.env["mail.message"].search([
            ("model", "=", "project.task"), ("res_id", "=", self.attente.id),
            ("partner_ids", "in", user.partner_id.ids), ("id", ">", avant),
        ])

    def test_each_assignee_reads_their_language(self):
        self.assertEqual(self.attente.state, "04_waiting_normal")
        # Closed from an English-speaking context: French must still reach Françoise.
        avant = self.env["mail.message"].search([], order="id desc", limit=1).id
        self.bloquante.with_context(lang="en_US").write({"state": "1_done"})
        pour_francoise = self._notifications(self.francoise, avant)
        pour_jane = self._notifications(self.jane, avant)
        self.assertEqual(len(pour_francoise), 1)
        self.assertEqual(len(pour_jane), 1)
        self.assertNotEqual(pour_francoise, pour_jane)
        self.assertIn("Tâche débloquée", pour_francoise.subject)
        self.assertIn("La tâche", pour_francoise.body)
        self.assertIn("Task unblocked", pour_jane.subject)
        self.assertIn("The task", pour_jane.body)
