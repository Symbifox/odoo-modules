"""The clinic example. Each doctor's color is set once, on the
employee, and the shift agenda shows it."""

import unittest
from datetime import timedelta

import pytz

from odoo import fields
from odoo.tests import TransactionCase, tagged

from ..models.tools import local_bounds, to_utc

TZ = pytz.timezone("America/Toronto")


@tagged("post_install", "-at_install")
class TestShiftColors(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The employee's own color comes with the bridge bf_color_hr, which
        # bf_shift does not require: without it, there is nothing to show.
        if "color_hex" not in cls.env["hr.employee"]._fields:
            raise unittest.SkipTest("bf_color_hr is not installed")
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        Emp = cls.env["hr.employee"]
        cls.dr_a = Emp.create({"name": "Dr A", "color_hex": "#FF0000", "tz": "America/Toronto"})
        cls.dr_b = Emp.create({"name": "Dr B", "color_hex": "#0000FF", "tz": "America/Toronto"})
        cls.dr_c = Emp.create({"name": "Dr C", "tz": "America/Toronto"})
        cls.template = cls.env["bf.shift.template"].create({
            "name": "Day", "hour_from": 8.0, "hour_to": 16.0, "color": 4})
        day = fields.Date.context_today(cls.env["bf.shift.schedule"]) + timedelta(days=21)
        cls.schedule = cls.env["bf.shift.schedule"].create({
            "name": "Week", "date_from": day, "date_to": day + timedelta(days=6)})
        start, end = local_bounds(day, 8.0, 16.0)

        def shift(emp):
            return cls.env["bf.shift.assignment"].create({
                "schedule_id": cls.schedule.id, "employee_id": emp.id,
                "template_id": cls.template.id,
                "start": to_utc(start, TZ), "end": to_utc(end, TZ),
            })

        cls.shift_a, cls.shift_b, cls.shift_c = shift(cls.dr_a), shift(cls.dr_b), shift(cls.dr_c)

    def test_shift_takes_the_doctors_color(self):
        self.assertEqual(self.shift_a.color_resolved, "#FF0000")
        self.assertEqual(self.shift_a.color_source, "rule")
        self.assertEqual(self.shift_b.color_resolved, "#0000FF")

    def test_doctor_without_color_keeps_the_agenda_color(self):
        # No color on Dr C and no swatch on the default rule: the shift has no
        # resolved color, so the agenda keeps its own per-employee color. The
        # template's color is not the shift's own color.
        self.assertFalse(self.shift_c.color_resolved)
        self.assertFalse(self.shift_c.color_source)

    def test_assign_missing_gives_dr_c_a_color(self):
        rule = self.env.ref("bf_shift.bf_color_rule_shift_employee")
        rule.action_assign_missing()
        self.assertTrue(self.dr_c.color_hex)
        self.assertNotIn(self.dr_c.color_hex, ("#FF0000", "#0000FF"))
        self.assertEqual(self.shift_c.color_resolved, self.dr_c.color_hex)

    def test_coloring_a_shift_never_repaints_its_template(self):
        """The shift's ``color`` is the template's (related): never write through it."""
        # The guard itself: the shift reads an index, but does not own it.
        self.assertTrue(self.shift_a._bf_color_has_index())
        self.assertFalse(self.shift_a._bf_color_has_index(stored=True))
        self.shift_a.color_hex = "#00AA00"
        self.assertEqual(self.template.color, 4)
        self.shift_a.bf_color_set_mine("#00AA00")
        self.assertEqual(self.template.color, 4)
