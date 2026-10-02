"""Shared set-up of the flow tests: real Employee and Manager accounts
(tests run as superuser otherwise, which hides every missing sudo and every
missing rule)."""

from datetime import date, timedelta

import pytz

from odoo import fields
from odoo.tests import TransactionCase

from ..models.tools import local_bounds, to_utc

TZ = pytz.timezone("America/Toronto")


class ShiftCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       no_reset_password=True))
        g_user = cls.env.ref("bf_shift.group_shift_user")
        g_mgr = cls.env.ref("bf_shift.group_shift_manager")
        Users = cls.env["res.users"].with_context(no_reset_password=True)

        def user(login, group):
            return Users.create({
                "name": login.split("_")[-1].title(), "login": login, "email": "%s@example.com" % login,
                "tz": "America/Toronto",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id, group.id])],
            })

        cls.u_mgr = user("shift_" + "mgr", g_mgr)
        cls.u1 = user("shift_" + "ana", g_user)
        cls.u2 = user("shift_" + "bea", g_user)
        cls.u3 = user("shift_" + "cyd", g_user)
        cls.agreement = cls.env["bf.shift.agreement"].create({
            "name": "Convention test",
            "kind": "collective",
            "notice_days": 7.0,
            "callback_min_hours": 4.0,
            "bank_allowed": True,
            "max_refusals": 2,
            "offer_method": "seniority",
            "premium_rule_ids": [(0, 0, {
                "name": "Evening", "code": "EVE", "applies_on": "window",
                "hour_from": 16.0, "hour_to": 24.0, "method": "percent",
                "percent": 7.0, "floor": 2.08,
            })],
        })
        Emp = cls.env["hr.employee"]
        cls.e1, cls.e2, cls.e3 = [Emp.create({
            "name": name, "user_id": u.id, "tz": "America/Toronto",
            "shift_agreement_id": cls.agreement.id,
            "shift_seniority_date": seniority,
            "shift_hourly_rate": 30.0,
            "shift_pay_ref": "P-%s" % name,
        }) for name, u, seniority in (
            ("Ana", cls.u1, date(2010, 1, 1)),
            ("Bea", cls.u2, date(2015, 1, 1)),
            ("Cyd", cls.u3, date(2020, 1, 1)),
        )]
        cls.day_tpl = cls.env["bf.shift.template"].create({
            "name": "Day", "hour_from": 8.0, "hour_to": 16.0, "break_minutes": 30})
        today = fields.Date.context_today(cls.env["bf.shift.schedule"])
        base = today + timedelta(days=21)
        cls.sunday = base + timedelta(days=(6 - base.weekday()) % 7)

    # ------------------------------------------------------------------

    def schedule(self, start=None, days=7, name="Week"):
        start = start or self.sunday
        return self.env["bf.shift.schedule"].create({
            "name": name, "date_from": start, "date_to": start + timedelta(days=days - 1)})

    def shift(self, sched, emp, day, h0=8.0, h1=16.5, brk=30, **kw):
        start, end = local_bounds(day, h0, h1)
        return self.env["bf.shift.assignment"].create(dict({
            "schedule_id": sched.id,
            "employee_id": emp.id if emp else False,
            "start": to_utc(start, TZ),
            "end": to_utc(end, TZ),
            "break_minutes": brk,
        }, **kw))

    def publish(self, sched):
        action = sched.action_publish()
        wizard = self.env["bf.shift.publish.wizard"].browse(action["res_id"])
        wizard.action_confirm()
        return wizard

    # ------------------------------------------------------------------
