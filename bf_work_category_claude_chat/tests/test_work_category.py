from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from odoo.addons.bf_work_category.tests.common import WorkCategoryCase


@tagged("post_install", "-at_install")
class TestGenCategory(WorkCategoryCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.task = cls.env["project.task"].create({"name": "t", "project_id": cls.project_bd.id})

    def session(self, **vals):
        return self.env["claude.chat.session"].create({"name": "conversation", **vals})

    def test_attached_to_a_task(self):
        session = self.session(res_model="project.task", res_id=self.task.id)
        self.assertEqual(self.stored(session), (self.bizdev.id, "task"))

    def test_follows_a_label_change_on_its_task(self):
        session = self.session(res_model="project.task", res_id=self.task.id)
        self.env.flush_all()
        self.task.tag_ids = [(6, 0, self.client.ids)]
        self.assertEqual(self.stored(session), (self.client.id, "task"))

    def test_follows_its_task_to_another_project(self):
        session = self.session(res_model="project.task", res_id=self.task.id)
        self.env.flush_all()
        self.task.project_id = self.project_none
        self.assertEqual(self.stored(session), (None, None))

    def test_attached_to_a_project(self):
        session = self.session(res_model="project.project", res_id=self.project_client.id)
        self.assertEqual(self.stored(session), (self.client.id, "project"))
        self.project_client.tag_ids = [(6, 0, self.bizdev.ids)]
        self.assertEqual(self.stored(session), (self.bizdev.id, "project"))

    def test_attached_to_another_categorised_record(self):
        first = self.session(res_model="project.task", res_id=self.task.id)
        second = self.session(res_model="claude.chat.session", res_id=first.id)
        self.assertEqual(self.stored(second), (self.bizdev.id, "linked"))

    def test_no_category_elsewhere(self):
        partner = self.env["res.partner"].create({"name": "WC partner"})
        self.assertEqual(self.stored(self.session(res_model="res.partner", res_id=partner.id)), (None, None))
        self.assertEqual(self.stored(self.session(res_model="project.task", res_id=999999999)), (None, None))
        self.assertEqual(self.stored(self.session()), (None, None))

    def test_relinking_and_override(self):
        session = self.session(res_model="project.task", res_id=self.task.id)
        session.write({"res_model": "project.project", "res_id": self.project_client.id})
        self.assertEqual(self.stored(session), (self.client.id, "project"))
        session.work_category_manual_id = self.bizdev
        self.assertEqual(self.stored(session), (self.bizdev.id, "manual"))

    def test_recompute_all_covers_attached_records(self):
        session = self.session(res_model="project.task", res_id=self.task.id)
        self.env.flush_all()
        self.env.cr.execute("UPDATE claude_chat_session SET work_category_id = NULL, work_category_task_id = NULL "
                            "WHERE id = %s", [session.id])
        self.env.invalidate_all()
        self.env["bf.work.category.mixin"]._bf_work_category_recompute_all()
        self.assertEqual(self.stored(session), (self.bizdev.id, "task"))

    def test_links_are_hidden_from_users(self):
        user = new_test_user(self.env, login="wc_gen_user", groups="base.group_user")
        session = self.session(res_model="project.task", res_id=self.task.id, user_id=user.id)
        self.env.flush_all()
        with self.assertRaises(AccessError):
            session.with_user(user).read(["work_category_task_id"])
        with self.assertRaises(AccessError):
            session.with_user(user).read(["work_category_project_id"])
        fields = self.env["claude.chat.session"].with_user(user).fields_get()
        self.assertNotIn("work_category_task_id", fields)
        self.assertIn("work_category_id", fields)

    def test_task_changes_keep_last_modified(self):
        session = self.session(res_model="project.task", res_id=self.task.id)
        self.age(session)
        self.task.tag_ids = [(6, 0, self.client.ids)]
        self.assertEqual(self.stored(session), (self.client.id, "task"))
        self.assertEqual(self.last_modified(session), (self.OLD, 1))

    def test_link_to_an_abstract_model_is_ignored(self):
        session = self.session(res_model="bf.work.category.linked.mixin", res_id=1)
        self.assertEqual(self.stored(session), (None, None))
        self.env["bf.work.category.mixin"]._bf_work_category_recompute_all()

    def test_follows_only_what_its_owner_can_read(self):
        # A private project the employee does not follow: attaching their own
        # conversation to its task must not tell them its category.
        user = new_test_user(self.env, login="wc_gen_owner", groups="base.group_user")
        private = self.env["project.project"].create({
            "name": "WC private", "privacy_visibility": "followers", "tag_ids": [(6, 0, self.bizdev.ids)]})
        task = self.env["project.task"].create({"name": "secret", "project_id": private.id})
        self.env.flush_all()
        self.assertFalse(task.with_user(user).has_access("read"))
        mine = self.session(res_model="project.task", res_id=task.id, user_id=user.id)
        self.assertEqual(self.stored(mine), (None, None))
        on_project = self.session(res_model="project.project", res_id=private.id, user_id=user.id)
        self.assertEqual(self.stored(on_project), (None, None))
        theirs = self.session(res_model="project.task", res_id=task.id)
        self.assertEqual(self.stored(theirs), (self.bizdev.id, "task"))
        # Once invited, the weekly recompute picks the link up.
        private.message_subscribe(partner_ids=user.partner_id.ids)
        self.env["bf.work.category.mixin"]._bf_work_category_recompute_all()
        self.assertEqual(self.stored(mine), (self.bizdev.id, "task"))
