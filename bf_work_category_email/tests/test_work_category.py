from odoo import fields
from odoo.tests import tagged

from odoo.addons.bf_work_category.tests.common import WorkCategoryCase


@tagged("post_install", "-at_install")
class TestEmailCategory(WorkCategoryCase):

    def test_email_follows_the_task_it_is_filed_on(self):
        task = self.env["project.task"].create({"name": "t", "project_id": self.project_bd.id})
        email = self.env["bf.email"].create({
            "subject": "WC", "direction": "in", "date": fields.Datetime.now(), "res_model": "project.task", "res_id": task.id})
        self.assertEqual(self.stored(email), (self.bizdev.id, "task"))
        task.project_id = self.project_client
        self.assertEqual(self.stored(email), (self.client.id, "task"))

    def test_task_changes_keep_last_modified(self):
        task = self.env["project.task"].create({"name": "t", "project_id": self.project_bd.id})
        email = self.env["bf.email"].create({
            "subject": "WC", "direction": "in", "date": fields.Datetime.now(), "res_model": "project.task", "res_id": task.id})
        self.age(email)
        task.project_id = self.project_client
        self.assertEqual(self.stored(email), (self.client.id, "task"))
        self.assertEqual(self.last_modified(email), (self.OLD, 1))
