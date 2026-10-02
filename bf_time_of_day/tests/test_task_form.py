# -*- coding: utf-8 -*-
from lxml import etree

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestTaskForm(TransactionCase):

    def test_slot_has_its_own_row(self):
        """The slot must not share the deadline's inline row.

        Inside `date_deadline_and_recurring_task`, every widget gets an equal
        share of the row and none gets a label: the deadline was cut to
        "2026-10-02 07:00:" at 1600 px. As a direct child of the group, the
        slot gets its own row and its own label.
        """
        view = self.env.ref("project.view_task_form2")
        arch = etree.fromstring(self.env["project.task"].get_view(view.id, "form")["arch"])
        row = arch.xpath("//div[@id='date_deadline_and_recurring_task']")
        self.assertEqual(len(row), 1, "the deadline row changed shape")
        self.assertFalse(row[0].xpath(".//field[@name='time_of_day_id']"),
                         "the slot is back inside the deadline row")
        slot = arch.xpath("//field[@name='time_of_day_id']")
        self.assertEqual(len(slot), 1)
        self.assertEqual(slot[0].getparent().tag, "group",
                         "the slot must get its label from the group")
