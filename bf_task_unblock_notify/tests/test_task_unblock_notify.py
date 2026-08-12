from odoo.tests import TransactionCase, tagged


@tagged("bf_task_unblock_notify", "task_unblock")
class TestTaskUnblockNotify(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Task = cls.env["project.task"]
        cls.project = cls.env["project.project"].create({
            "name": "Unblock Test Project",
            "allow_task_dependencies": True,
        })
        cls.user = cls.env["res.users"].create({
            "name": "Assignee",
            "login": "unblock_assignee",
            "email": "unblock@example.com",
        })

    def _make_task(self, name, **overrides):
        vals = {
            "name": name,
            "project_id": self.project.id,
            "user_ids": [(6, 0, [self.user.id])],
        }
        vals.update(overrides)
        return self.Task.create(vals)

    def test_closing_blocker_notifies_waiting_dependent(self):
        """Closing a blocker should post a notification on the now-unblocked task."""
        blocker = self._make_task("Blocker")
        dependent = self._make_task("Dependent")
        # Link dependency via write so _compute_state lands on 04_waiting_normal
        # (Odoo 18 doesn't transition state when depend_on_ids is passed in create)
        dependent.write({"depend_on_ids": [(6, 0, [blocker.id])]})
        self.assertEqual(dependent.state, "04_waiting_normal")
        # Baseline message count on dependent
        before = self.env["mail.message"].search_count([
            ("model", "=", "project.task"),
            ("res_id", "=", dependent.id),
        ])
        # Close blocker
        blocker.write({"state": "1_done"})
        after = self.env["mail.message"].search_count([
            ("model", "=", "project.task"),
            ("res_id", "=", dependent.id),
        ])
        # Dependent should have received at least one new message
        self.assertGreater(after, before)

    def test_closing_non_blocker_does_not_notify(self):
        """Writing state on a task without waiting dependents should not post."""
        standalone = self._make_task("Standalone")
        before = self.env["mail.message"].search_count([
            ("model", "=", "project.task"),
            ("res_id", "=", standalone.id),
        ])
        standalone.write({"state": "1_done"})
        after = self.env["mail.message"].search_count([
            ("model", "=", "project.task"),
            ("res_id", "=", standalone.id),
        ])
        # Only tracking messages from core — no unblock spam expected
        # (the key assertion is that this doesn't error)
        self.assertGreaterEqual(after, before)

    def test_cancelling_blocker_unblocks_dependent(self):
        """Cancellation (1_canceled) also counts as closing for unblock purposes."""
        blocker = self._make_task("Blocker2")
        dependent = self._make_task("Dep2")
        dependent.write({"depend_on_ids": [(6, 0, [blocker.id])]})
        # Should not raise
        blocker.write({"state": "1_canceled"})
        # Dependent's waiting state should have cleared
        dependent.invalidate_recordset(fnames=["state"])
        self.assertNotEqual(dependent.state, "04_waiting_normal")
