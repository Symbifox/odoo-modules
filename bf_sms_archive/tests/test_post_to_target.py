"""Le sorcier « Poster sur une fiche » depuis la 5.8.0.

Ce qui change et se vérifie ici : la cible n'est plus forcément une tâche, on ne
choisit plus de projet, et les deux options propres à la tâche (rattacher la
conversation, relayer les prochains messages) ne s'appliquent que lorsque la
cible en est effectivement une.
"""

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestPostToTarget(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.thread = cls.env["sms.archive.thread"].create({
            "phone_normalized": "+15145550101",
            "contact_name": "Cible SMS",
            "owner_id": cls.env.user.id,
        })
        cls.message = cls.env["sms.archive.message"].create({
            "thread_id": cls.thread.id,
            "message_hash": "hash-post-to-target-1",
            "direction": "in",
            "body": "Bonjour, c'est un test.",
            "date_sent": "2026-08-17 12:00:00",
        })
        cls.project = cls.env["project.project"].create({"name": "Projet SMS"})
        cls.task = cls.env["project.task"].create({
            "name": "Tâche SMS", "project_id": cls.project.id,
        })
        cls.partner = cls.env["res.partner"].create({"name": "Contact SMS"})

    def _wizard(self, target=None, **extra):
        vals = {"message_ids": [(6, 0, self.message.ids)]}
        if target is not None:
            vals["target_reference"] = f"{target._name},{target.id}"
        vals.update(extra)
        return self.env["sms.archive.post.to.task.wizard"].create(vals)

    def _message_count(self, record):
        return self.env["mail.message"].search_count([
            ("model", "=", record._name), ("res_id", "=", record.id),
        ])

    # ------------------------------------------------------------------
    def test_task_id_is_derived_from_the_target(self):
        self.assertEqual(self._wizard(self.task).task_id, self.task)
        self.assertFalse(self._wizard(self.partner).task_id)
        self.assertFalse(self._wizard().task_id)

    def test_post_on_a_task_still_links_and_follows(self):
        wizard = self._wizard(self.task, link_threads=True, follow_thread=True)
        before = self._message_count(self.task)
        wizard.action_post()
        self.assertEqual(self._message_count(self.task), before + 1)
        self.assertIn(self.task, self.thread.task_ids)
        self.assertEqual(self.thread.auto_post_task_id, self.task)

    def test_post_on_a_contact_posts_without_touching_the_task_plumbing(self):
        """Le gain de la 5.8.0 : une cible qui n'est pas une tâche."""
        wizard = self._wizard(self.partner, link_threads=True, follow_thread=True)
        before = self._message_count(self.partner)
        wizard.action_post()
        self.assertEqual(self._message_count(self.partner), before + 1)
        self.assertFalse(self.thread.task_ids)
        self.assertFalse(self.thread.auto_post_task_id)

    def test_post_without_a_target_is_refused(self):
        with self.assertRaises(UserError):
            self._wizard().action_post()

    def test_post_without_a_selection_is_refused(self):
        wizard = self.env["sms.archive.post.to.task.wizard"].create({
            "target_reference": f"project.task,{self.task.id}",
        })
        with self.assertRaises(UserError):
            wizard.action_post()

    def test_default_target_comes_from_the_linked_task(self):
        """Neuf fois sur dix on reposte au même endroit : la cible doit être
        proposée, sinon on a juste déplacé la recherche d'un champ à l'autre."""
        self.thread.write({"task_ids": [(4, self.task.id, 0)]})
        wizard = self.env["sms.archive.post.to.task.wizard"].with_context(
            default_message_ids=[(6, 0, self.message.ids)],
        ).create({"message_ids": [(6, 0, self.message.ids)]})
        self.assertEqual(wizard.target_reference, self.task)

    def test_target_selection_reaches_beyond_tasks(self):
        selection = dict(
            self.env["sms.archive.post.to.task.wizard"]._selection_chatter_target()
        )
        self.assertIn("project.task", selection)
        self.assertIn("res.partner", selection)
        self.assertIn("helpdesk.ticket" if "helpdesk.ticket" in self.env
                      else "res.partner", selection)
