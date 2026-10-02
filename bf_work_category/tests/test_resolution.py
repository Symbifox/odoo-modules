from odoo.exceptions import AccessError, ValidationError
from odoo.tests import new_test_user, tagged

from .common import WorkCategoryCase


@tagged("post_install", "-at_install")
class TestResolution(WorkCategoryCase):

    def test_project_from_its_label(self):
        self.assertEqual(self.project_bd.work_category_id, self.bizdev)
        self.assertEqual(self.project_bd.work_category_origin, "label")
        self.assertFalse(self.project_none.work_category_id)
        self.assertFalse(self.project_none.work_category_origin)

    def test_task_follows_its_project(self):
        task = self.env["project.task"].create({"name": "t", "project_id": self.project_bd.id})
        self.assertEqual(task.work_category_id, self.bizdev)
        self.assertEqual(task.work_category_origin, "project")
        self.assertEqual(self.stored(task), (self.bizdev.id, "project"))

    def test_task_label_wins_over_project(self):
        task = self.env["project.task"].create({
            "name": "t", "project_id": self.project_client.id, "tag_ids": [(6, 0, self.bizdev.ids)]})
        self.assertEqual((task.work_category_id, task.work_category_origin), (self.bizdev, "label"))

    def test_lowest_order_wins(self):
        task = self.env["project.task"].create({
            "name": "t", "project_id": self.project_none.id, "tag_ids": [(6, 0, (self.client | self.bizdev).ids)]})
        self.assertEqual(task.work_category_id, self.bizdev)
        self.client.work_category_sequence = 1
        self.assertEqual(task.work_category_id, self.client)

    def test_plain_label_is_ignored(self):
        task = self.env["project.task"].create({
            "name": "t", "project_id": self.project_none.id, "tag_ids": [(6, 0, self.plain.ids)]})
        self.assertFalse(task.work_category_id)
        self.assertEqual(self.stored(task), (None, None))

    def test_override_wins(self):
        task = self.env["project.task"].create({
            "name": "t", "project_id": self.project_bd.id, "tag_ids": [(6, 0, self.bizdev.ids)],
            "work_category_manual_id": self.client.id})
        self.assertEqual((task.work_category_id, task.work_category_origin), (self.client, "manual"))
        task.work_category_manual_id = False
        self.assertEqual(self.stored(task), (self.bizdev.id, "label"))

    def test_override_ignored_once_label_is_no_longer_a_category(self):
        task = self.env["project.task"].create({
            "name": "t", "project_id": self.project_bd.id, "work_category_manual_id": self.client.id})
        self.client.is_work_category = False
        self.assertEqual((task.work_category_id, task.work_category_origin), (self.bizdev, "project"))

    def test_flagging_a_label_reaches_projects_and_tasks(self):
        task = self.env["project.task"].create({"name": "t", "project_id": self.project_none.id})
        self.assertFalse(task.work_category_id)
        self.plain.is_work_category = True
        self.assertEqual(self.project_none.work_category_id, self.plain)
        self.assertEqual(self.stored(task), (self.plain.id, "project"))

    def test_project_label_change_reaches_tasks(self):
        task = self.env["project.task"].create({"name": "t", "project_id": self.project_client.id})
        self.project_client.tag_ids = [(6, 0, self.bizdev.ids)]
        self.assertEqual(self.stored(task), (self.bizdev.id, "project"))

    def test_moving_a_task(self):
        task = self.env["project.task"].create({"name": "t", "project_id": self.project_bd.id})
        task.project_id = self.project_none
        self.assertEqual(self.stored(task), (None, None))
        task.project_id = self.project_client
        self.assertEqual(self.stored(task), (self.client.id, "project"))

    def test_group_by_category(self):
        Task = self.env["project.task"]
        Task.create([{"name": "a", "project_id": self.project_bd.id},
                     {"name": "b", "project_id": self.project_bd.id},
                     {"name": "c", "project_id": self.project_client.id}])
        groups = Task.read_group(
            [("project_id", "in", (self.project_bd | self.project_client).ids)],
            ["work_category_id"], ["work_category_id"])
        counts = {group["work_category_id"][0]: group["work_category_id_count"] for group in groups}
        self.assertEqual(counts, {self.bizdev.id: 2, self.client.id: 1})

    def test_recompute_all_repairs_stale_values(self):
        task = self.env["project.task"].create({"name": "t", "project_id": self.project_bd.id})
        self.env.flush_all()
        self.env.cr.execute("UPDATE project_task SET work_category_id = NULL, work_category_origin = NULL "
                            "WHERE id = %s", [task.id])
        self.env.invalidate_all()
        self.assertFalse(task.work_category_id)
        counts = self.env["bf.work.category.mixin"]._bf_work_category_recompute_all()
        self.assertIn("project.task", counts)
        self.assertEqual(self.stored(task), (self.bizdev.id, "project"))

    def test_recompute_all_is_for_project_administrators(self):
        user = new_test_user(self.env, login="wc_user", groups="project.group_project_user")
        with self.assertRaises(AccessError):
            self.env["bf.work.category.mixin"].with_user(user)._bf_work_category_recompute_all()
        action = self.bizdev.action_bf_work_category_recompute()
        self.assertEqual(action["tag"], "display_notification")

    # --- storing a category is bookkeeping, not an edit

    def test_label_changes_keep_last_modified(self):
        task = self.env["project.task"].create({"name": "t", "project_id": self.project_none.id})
        self.age(task, self.project_none)
        self.plain.is_work_category = True
        self.assertEqual(self.stored(task), (self.plain.id, "project"))
        self.assertEqual(self.last_modified(task), (self.OLD, 1))
        self.assertEqual(self.last_modified(self.project_none), (self.OLD, 1))
        self.project_none.tag_ids = [(6, 0, self.bizdev.ids)]
        self.assertEqual(self.stored(task), (self.bizdev.id, "project"))
        self.assertEqual(self.last_modified(task), (self.OLD, 1))

    def test_a_real_edit_still_counts(self):
        task = self.env["project.task"].create({"name": "t", "project_id": self.project_none.id})
        self.age(task)
        task.write({"name": "renamed", "tag_ids": [(6, 0, self.bizdev.ids)]})
        self.assertEqual(self.stored(task), (self.bizdev.id, "label"))
        self.assertNotEqual(self.last_modified(task)[0], self.OLD)

    def test_recompute_all_writes_only_what_differs(self):
        stale = self.env["project.task"].create({"name": "stale", "project_id": self.project_bd.id})
        right = self.env["project.task"].create({"name": "right", "project_id": self.project_client.id})
        self.age(stale, right)
        self.env.cr.execute("UPDATE project_task SET work_category_id = NULL, work_category_origin = NULL "
                            "WHERE id = %s", [stale.id])
        self.env.invalidate_all()
        counts = self.env["bf.work.category.mixin"]._bf_work_category_recompute_all()
        self.assertEqual(self.stored(stale), (self.bizdev.id, "project"))
        self.assertEqual(self.last_modified(stale), (self.OLD, 1))
        self.assertEqual(self.last_modified(right), (self.OLD, 1))
        self.assertGreaterEqual(counts["project.task"], 1)
        self.assertEqual(self.env["bf.work.category.mixin"]._bf_work_category_recompute_all()["project.task"], 0)

    def test_button_runs_in_the_background(self):
        cron = self.env.ref("bf_work_category.ir_cron_work_category_recompute")
        triggers = self.env["ir.cron.trigger"].search_count([("cron_id", "=", cron.id)])
        self.bizdev.action_bf_work_category_recompute()
        self.assertEqual(self.env["ir.cron.trigger"].search_count([("cron_id", "=", cron.id)]), triggers + 1)
        user = new_test_user(self.env, login="wc_user_button", groups="project.group_project_user")
        with self.assertRaises(AccessError):
            self.bizdev.with_user(user).action_bf_work_category_recompute()

    def test_button_runs_at_once_when_the_scheduled_action_is_off(self):
        cron = self.env.ref("bf_work_category.ir_cron_work_category_recompute")
        cron.active = False
        triggers = self.env["ir.cron.trigger"].search_count([("cron_id", "=", cron.id)])
        task = self.env["project.task"].create({"name": "t", "project_id": self.project_bd.id})
        self.env.flush_all()
        self.env.cr.execute("UPDATE project_task SET work_category_id = NULL WHERE id = %s", [task.id])
        self.env.invalidate_all()
        action = self.bizdev.action_bf_work_category_recompute()
        self.assertEqual(self.env["ir.cron.trigger"].search_count([("cron_id", "=", cron.id)]), triggers)
        self.assertEqual(self.stored(task), (self.bizdev.id, "project"))
        self.assertEqual(action["tag"], "display_notification")

    # --- what a user can write

    def test_a_user_cannot_write_the_resolved_category(self):
        user = new_test_user(self.env, login="wc_writer", groups="project.group_project_user")
        task = self.env["project.task"].create({"name": "t", "project_id": self.project_bd.id})
        task.with_user(user).write({"work_category_id": self.plain.id, "work_category_origin": "manual"})
        self.assertEqual(self.stored(task), (self.bizdev.id, "project"))
        created = self.env["project.task"].with_user(user).create({
            "name": "c", "project_id": self.project_none.id,
            "work_category_id": self.plain.id, "work_category_origin": "manual"})
        self.assertEqual(self.stored(created), (None, None))

    def test_only_project_administrators_make_a_category(self):
        user = new_test_user(self.env, login="wc_tagger", groups="project.group_project_user")
        Tag = self.env["project.tags"].with_user(user)
        label = Tag.create({"name": "WC user label", "is_work_category": False, "work_category_sequence": 10})
        label.name = "WC user label, renamed"
        self.bizdev.with_user(user).name = "WC bizdev, renamed"
        with self.assertRaises(AccessError):
            label.write({"is_work_category": True})
        with self.assertRaises(AccessError):
            Tag.create({"name": "WC forbidden", "is_work_category": True})
        with self.assertRaises(AccessError):
            self.bizdev.with_user(user).write({"is_work_category": False})
        with self.assertRaises(AccessError):
            self.bizdev.with_user(user).write({"work_category_sequence": 1})
        with self.assertRaises(AccessError):
            Tag.with_context(default_is_work_category=True).create({"name": "WC by default"})
        with self.assertRaises(AccessError):
            Tag.with_context(default_is_work_category=True).name_create("WC by name_create")
        with self.assertRaises(AccessError):
            self.bizdev.with_user(user).unlink()
        label.unlink()
        self.assertFalse(label.exists())
        self.assertTrue(self.bizdev.is_work_category)

    def test_override_must_be_a_category(self):
        task = self.env["project.task"].create({"name": "t", "project_id": self.project_bd.id})
        with self.assertRaises(ValidationError):
            task.work_category_manual_id = self.plain
