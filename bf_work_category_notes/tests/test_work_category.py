from odoo.tests import new_test_user, tagged

from odoo.addons.bf_work_category.tests.common import WorkCategoryCase


@tagged("post_install", "-at_install")
class TestNoteCategory(WorkCategoryCase):

    def test_note_follows_its_task(self):
        task = self.env["project.task"].create({"name": "t", "project_id": self.project_bd.id})
        note = self.env["bf.note"].create({"name": "note", "res_ref": f"project.task,{task.id}"})
        self.assertEqual((note.res_model, note.res_id), ("project.task", task.id))
        self.assertEqual(self.stored(note), (self.bizdev.id, "task"))
        task.tag_ids = [(6, 0, self.client.ids)]
        self.assertEqual(self.stored(note), (self.client.id, "task"))

    def test_free_note_has_no_category(self):
        self.assertEqual(self.stored(self.env["bf.note"].create({"name": "note"})), (None, None))

    def test_task_changes_keep_last_modified(self):
        # The phone refuses to save a note whose write_date moved under it:
        # a colleague relabelling the task must not cause that.
        task = self.env["project.task"].create({"name": "t", "project_id": self.project_bd.id})
        note = self.env["bf.note"].create({"name": "note", "res_ref": f"project.task,{task.id}"})
        self.age(note)
        task.tag_ids = [(6, 0, self.client.ids)]
        self.assertEqual(self.stored(note), (self.client.id, "task"))
        self.assertEqual(self.last_modified(note), (self.OLD, 1))

    def test_a_real_edit_in_the_same_write_dates_only_its_record(self):
        task = self.env["project.task"].create({"name": "t", "project_id": self.project_bd.id})
        follows = self.env["bf.note"].create({"name": "follows", "res_ref": f"project.task,{task.id}"})
        edited = self.env["bf.note"].create({"name": "edited"})
        self.age(follows, edited)
        task.tag_ids = [(6, 0, self.client.ids)]
        edited.name = "edited, renamed"
        self.env.flush_all()
        self.assertEqual(self.stored(follows), (self.client.id, "task"))
        self.assertEqual(self.last_modified(follows), (self.OLD, 1))
        self.assertNotEqual(self.last_modified(edited)[0], self.OLD)

    def test_owner_rights_with_several_companies_ticked(self):
        # An administrator working with two companies ticked files the note of
        # an employee who belongs to one: the owner's access is measured under
        # the owner's companies, not the caller's, and nothing fails.
        other = self.env["res.company"].create({"name": "WC other company"})
        main = self.env.company
        admin = new_test_user(self.env, login="wc_two_companies", groups="base.group_user,project.group_project_manager",
                              company_id=main.id, company_ids=[(6, 0, (main | other).ids)])
        employee = new_test_user(self.env, login="wc_one_company", groups="base.group_user,project.group_project_user",
                                 company_id=main.id, company_ids=[(6, 0, main.ids)])
        task = self.env["project.task"].create({"name": "t", "project_id": self.project_bd.id})
        note = self.env["bf.note"].create({"name": "theirs", "user_id": employee.id})
        both = dict(allowed_company_ids=(main | other).ids)
        note.with_user(admin).with_context(**both).sudo().write({"res_ref": f"project.task,{task.id}"})
        self.assertEqual(self.stored(note), (self.bizdev.id, "task"))
        cron = self.env.ref("bf_work_category.ir_cron_work_category_recompute")
        cron.active = False
        self.bizdev.with_user(admin).with_context(**both).action_bf_work_category_recompute()
        self.assertEqual(self.stored(note), (self.bizdev.id, "task"))
