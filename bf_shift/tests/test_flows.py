"""End-to-end flows, played under real roles (tests run as superuser
otherwise, which hides every missing sudo and every missing rule)."""

import base64
from datetime import date, timedelta

import pytz

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from ..models.tools import local_bounds, to_local, to_utc

TZ = pytz.timezone("America/Toronto")


@tagged("post_install", "-at_install")
class TestFlows(TransactionCase):

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

    def test_generate_local_times(self):
        sched = self.schedule()
        monday = self.sunday + timedelta(days=1)
        weekdays = self.env["bf.shift.weekday"].search([("code", "in", (0, 1, 2, 3, 4))])
        wizard = self.env["bf.shift.generate.wizard"].create({
            "schedule_id": sched.id, "template_id": self.day_tpl.id,
            "employee_ids": [(6, 0, (self.e1 | self.e2).ids)],
            "date_from": sched.date_from, "date_to": sched.date_to,
            "weekday_ids": [(6, 0, weekdays.ids)],
        })
        wizard.action_generate()
        self.assertEqual(len(sched.assignment_ids), 10)
        first = sched.assignment_ids.sorted("start")[0]
        self.assertEqual(to_local(first.start, TZ).hour, 8)
        self.assertEqual(first.date, monday)
        self.assertEqual(first.planned_hours, 7.5)

    def test_publish_then_log(self):
        sched = self.schedule()
        a = self.shift(sched, self.e1, self.sunday + timedelta(days=1))
        self.assertFalse(a.change_ids, "a draft schedule logs nothing")
        self.publish(sched)
        self.assertEqual(sched.state, "published")
        self.assertTrue(a.informed_at)
        self.assertFalse(sched.warning_ids)
        # A change three weeks ahead: logged, not short notice.
        a.with_user(self.u_mgr).write({"end": a.end + timedelta(hours=1)})
        self.assertEqual(len(a.change_ids), 1)
        change = a.change_ids
        self.assertEqual(change.change_type, "modify")
        self.assertFalse(change.late)
        self.assertEqual(change.consent, "na")
        self.assertEqual(change.user_id, self.u_mgr)
        with self.assertRaises(UserError):
            change.write({"after": "rewritten"})
        with self.assertRaises(UserError):
            change.unlink()
        with self.assertRaises(UserError):
            a.unlink()
        with self.assertRaises(UserError):
            sched.unlink()
        a.with_user(self.u_mgr).action_done()
        self.assertEqual(len(a.change_ids), 1, "marking done is not a change of schedule")
        a.with_user(self.u_mgr).action_cancel()
        self.assertEqual(a.change_ids.sorted("id")[-1].change_type, "cancel")

    def test_short_notice_and_consent(self):
        tomorrow = fields.Date.context_today(self.env["bf.shift.schedule"]) + timedelta(days=1)
        sched = self.schedule(start=tomorrow, days=3)
        a = self.shift(sched, self.e1, tomorrow)
        sched._run_checks()
        self.assertIn("notice", sched.warning_ids.mapped("code"))
        self.publish(sched)
        self.assertTrue(a.late_notice)
        a.with_user(self.u_mgr).write({"employee_id": self.e2.id})
        change = a.change_ids
        self.assertEqual(change.change_type, "reassign")
        self.assertTrue(change.late)
        self.assertEqual(change.consent, "pending")
        self.assertEqual(change.employee_id, self.e2)
        self.assertEqual(change.previous_employee_id, self.e1)
        # The previous employee sees it, but cannot answer for the new one.
        self.assertTrue(change.with_user(self.u1).after)
        with self.assertRaises(AccessError):
            change.with_user(self.u1).action_accept()
        change.with_user(self.u2).action_accept()
        self.assertEqual(change.consent, "given")
        self.assertEqual(change.consent_recorded_by, self.u2)
        with self.assertRaises(UserError):
            change.with_user(self.u2).action_refuse()
        # Not even the module's own code rewrites an answer once recorded.
        with self.assertRaisesRegex(UserError, "already recorded"):
            change.sudo().write({"consent": "refused"})
        # Not even a manager rewrites an answer already recorded.
        with self.assertRaises(UserError):
            change.with_user(self.u_mgr).write({"consent": "refused"})

    def test_employee_sees_only_what_is_posted(self):
        sched = self.schedule()
        a = self.shift(sched, self.e2, self.sunday + timedelta(days=1))
        Sched = self.env["bf.shift.schedule"].with_user(self.u1)
        self.assertFalse(Sched.search([("id", "=", sched.id)]))
        self.publish(sched)
        self.assertTrue(Sched.search([("id", "=", sched.id)]))
        self.assertTrue(self.env["bf.shift.assignment"].with_user(self.u1).search(
            [("id", "=", a.id)]))
        with self.assertRaises(AccessError):
            sched.with_user(self.u1).write({"name": "mine now"})
        with self.assertRaises(AccessError):
            a.with_user(self.u1).write({"employee_id": self.e1.id})
        period = self.env["bf.shift.pay.period"].create({
            "name": "P", "date_from": sched.date_from, "date_to": sched.date_to})
        with self.assertRaises(AccessError):
            period.with_user(self.u1).read(["name"])
        # Availability: own only.
        Avail = self.env["bf.shift.availability"]
        mine = Avail.with_user(self.u1).create({
            "recurrence": "weekly",
            "weekday_id": self.env.ref("bf_shift.weekday_0").id})
        self.assertEqual(mine.employee_id, self.e1)
        theirs = Avail.create({"employee_id": self.e2.id, "recurrence": "weekly",
                               "weekday_id": self.env.ref("bf_shift.weekday_1").id})
        self.assertFalse(Avail.with_user(self.u1).search([("id", "=", theirs.id)]))
        with self.assertRaises(AccessError):
            Avail.with_user(self.u1).create({
                "employee_id": self.e2.id, "recurrence": "weekly",
                "weekday_id": self.env.ref("bf_shift.weekday_2").id})

    def test_closed_schedule(self):
        sched = self.schedule()
        a = self.shift(sched, self.e1, self.sunday + timedelta(days=1))
        self.publish(sched)
        sched.action_close()
        with self.assertRaises(UserError):
            a.write({"start": a.start + timedelta(hours=1)})
        a.write({"actual_start": a.start, "actual_end": a.end + timedelta(hours=1)})
        self.assertEqual(a.worked_hours, 9.0)
        sched.action_reopen()
        a.write({"start": a.start + timedelta(minutes=15)})

    def test_unavailability_in_checks(self):
        sched = self.schedule()
        monday = self.sunday + timedelta(days=1)
        self.shift(sched, self.e1, monday)
        self.env["bf.shift.availability"].create({
            "employee_id": self.e1.id, "recurrence": "weekly",
            "weekday_id": self.env.ref("bf_shift.weekday_0").id})
        sched._run_checks()
        self.assertEqual(sched.warning_ids.mapped("code"), ["unavailable"])

    def test_numbers_in_the_user_language(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        sched = self.schedule()
        self.shift(sched, self.e1, self.sunday + timedelta(days=1), 7.0, 19.5, brk=0)
        sched.with_context(lang="fr_CA")._run_checks()
        messages = " ".join(sched.warning_ids.mapped("message"))
        self.assertIn("12,5 h", messages)
        self.assertIn("30 minutes", messages)
        self.assertNotIn("30.0", messages)
        self.assertNotIn("12.5", messages)

    # ------------------------------------------------------------------
    # Offers
    # ------------------------------------------------------------------

    def _pool(self):
        return self.env["bf.shift.pool"].create({
            "name": "Overtime", "agreement_id": self.agreement.id,
            "member_ids": [(0, 0, {"employee_id": e.id}) for e in (self.e3, self.e1, self.e2)],
        })

    def test_offer_by_seniority(self):
        pool = self._pool()
        sched = self.schedule()
        monday = self.sunday + timedelta(days=1)
        self.shift(sched, self.e1, monday, 8.0, 16.5)          # Ana already works
        open_shift = self.shift(sched, False, monday, 12.0, 20.5)
        self.assertTrue(open_shift.is_open)
        self.publish(sched)
        offer = self.env["bf.shift.offer"].with_user(self.u_mgr).create({
            "assignment_id": open_shift.id, "pool_id": pool.id})
        offer.action_start()
        lines = offer.line_ids.sorted("rank")
        self.assertEqual(lines.mapped("employee_id"), self.e1 | self.e2 | self.e3)
        self.assertEqual(lines[0].employee_id, self.e1)
        self.assertEqual((lines[0].state, lines[0].skip_reason), ("skipped", "conflict"))
        self.assertEqual(lines[1].state, "offered")
        self.assertEqual(lines[2].state, "waiting")
        # Cyd cannot see nor answer Bea's line.
        with self.assertRaises(AccessError):
            lines[1].with_user(self.u3).action_accept()
        # Nobody rewrites an answer by hand.
        with self.assertRaises(UserError):
            lines[1].with_user(self.u_mgr).write({"state": "accepted"})
        # Bea refuses, from her own account.
        wiz = self.env["bf.shift.refuse.wizard"].with_user(self.u2).create({
            "offer_line_id": lines[1].id, "reason": "Garde des enfants"})
        wiz.action_confirm()
        self.assertEqual(lines[1].state, "refused")
        self.assertEqual(lines[1].response_channel, "self")
        member_b = pool.member_ids.filtered(lambda m: m.employee_id == self.e2)
        self.assertEqual(member_b.refusal_count, 1)
        self.assertEqual(member_b.hours_offered, 8.0, "a refusal counts as hours offered")
        self.assertEqual(lines[2].state, "offered")
        # Cyd accepts.
        lines[2].with_user(self.u3).action_accept()
        self.assertEqual(open_shift.employee_id, self.e3)
        self.assertEqual(offer.state, "filled")
        change = open_shift.change_ids
        self.assertEqual(change.change_type, "reassign")
        self.assertEqual(change.consent, "given")
        member_c = pool.member_ids.filtered(lambda m: m.employee_id == self.e3)
        self.assertEqual(member_c.hours_accepted, 8.0)

    def test_refusals_remove_from_list(self):
        pool = self._pool()
        member = pool.member_ids.filtered(lambda m: m.employee_id == self.e1)
        member.refusal_count = 1
        sched = self.schedule()
        open_shift = self.shift(sched, False, self.sunday + timedelta(days=2))
        self.publish(sched)
        offer = self.env["bf.shift.offer"].create({"assignment_id": open_shift.id,
                                                   "pool_id": pool.id})
        offer.action_start()
        line = offer.line_ids.filtered(lambda l: l.state == "offered")
        self.assertEqual(line.employee_id, self.e1)
        self.env["bf.shift.refuse.wizard"].with_user(self.u1).create(
            {"offer_line_id": line.id}).action_confirm()
        self.assertFalse(member.active)
        self.assertEqual(member.withdraw_reason, "refusals")

    def test_offer_expires(self):
        pool = self._pool()
        sched = self.schedule()
        open_shift = self.shift(sched, False, self.sunday + timedelta(days=2))
        self.publish(sched)
        offer = self.env["bf.shift.offer"].create({"assignment_id": open_shift.id,
                                                   "pool_id": pool.id})
        offer.action_start()
        first = offer.line_ids.filtered(lambda l: l.state == "offered")
        first.sudo().write({"response_deadline": fields.Datetime.now() - timedelta(minutes=1)})
        self.env["bf.shift.offer"]._cron_expire()
        self.assertEqual(first.state, "no_answer")
        self.assertEqual(first.response_channel, "system")
        self.assertEqual(len(offer.line_ids.filtered(lambda l: l.state == "offered")), 1)
        member = first.member_id
        self.assertEqual(member.refusal_count, 0, "no answer is not a refusal")

    # ------------------------------------------------------------------
    # Swaps
    # ------------------------------------------------------------------

    def test_swap(self):
        sched = self.schedule()
        a = self.shift(sched, self.e1, self.sunday + timedelta(days=1))
        b = self.shift(sched, self.e2, self.sunday + timedelta(days=2))
        self.publish(sched)
        Swap = self.env["bf.shift.swap"]
        with self.assertRaises(UserError):
            Swap.with_user(self.u1).create({
                "requester_id": self.e2.id, "assignment_id": b.id, "target_id": self.e1.id})
        swap = Swap.with_user(self.u1).create({
            "assignment_id": a.id, "target_id": self.e2.id, "target_assignment_id": b.id})
        self.assertEqual(swap.requester_id, self.e1)
        swap.action_submit()
        with self.assertRaises(UserError):
            swap.with_user(self.u3).action_colleague_accept()
        swap.with_user(self.u2).action_colleague_accept()
        self.assertEqual(swap.state, "approval")
        with self.assertRaises(UserError):
            swap.with_user(self.u2).action_approve()
        swap.with_user(self.u_mgr).action_approve()
        self.assertEqual(swap.state, "done")
        self.assertEqual(a.employee_id, self.e2)
        self.assertEqual(b.employee_id, self.e1)
        self.assertEqual(a.change_ids.consent, "given")
        self.assertTrue(swap.check_summary)

    def test_swap_without_approval(self):
        self.agreement.swap_requires_approval = False
        sched = self.schedule()
        a = self.shift(sched, self.e1, self.sunday + timedelta(days=1))
        self.publish(sched)
        swap = self.env["bf.shift.swap"].with_user(self.u1).create({
            "assignment_id": a.id, "target_id": self.e3.id})
        swap.action_submit()
        swap.with_user(self.u3).action_colleague_accept()
        self.assertEqual(swap.state, "done")
        self.assertEqual(a.employee_id, self.e3)

    # ------------------------------------------------------------------
    # Bank, benefits, pay
    # ------------------------------------------------------------------

    def test_bank_needs_employee_request(self):
        sched = self.schedule()
        with self.assertRaises(ValidationError):
            self.shift(sched, self.e1, self.sunday + timedelta(days=1), to_bank=True)
        a = self.shift(sched, self.e1, self.sunday + timedelta(days=1), to_bank=True,
                       bank_requested_by_employee=True)
        self.assertTrue(a.to_bank)

    def test_benefit_events(self):
        monday = self.sunday + timedelta(days=1)
        receipt = self.env["ir.attachment"].with_user(self.u1).create({
            "name": "receipt.txt", "raw": b"12.50"})
        Event = self.env["bf.shift.benefit.event"].with_user(self.u1)
        events = Event.browse()
        for i in range(3):
            events |= Event.create({
                "kind": "overtime_meal", "date": monday + timedelta(days=i), "amount": 15.0,
                "overtime_hours": 2.5, "employer_requested": True,
                "attachment_ids": [(4, receipt.id)],
            })
        self.assertEqual(events.mapped("employee_id"), self.e1)
        self.assertEqual(events.mapped("times_this_week"), [1, 2, 3])
        self.assertEqual(events.mapped("quebec_status"), ["not_taxable", "not_taxable", "taxable"])
        self.assertEqual(events.mapped("federal_status"), ["not_taxable", "not_taxable", "taxable"])
        events[0].action_submit()
        with self.assertRaises(UserError):
            events[0].action_approve()
        with self.assertRaises(UserError):
            events[0].write({"amount": 99.0})
        events[0].with_user(self.u_mgr).action_approve()
        self.assertEqual(events[0].state, "approved")

    def test_pay_period(self):
        sched = self.schedule()
        shifts = self.env["bf.shift.assignment"]
        for i in range(1, 6):  # Mon-Fri 8:00-18:00, 30 min break = 9.5 h
            shifts |= self.shift(sched, self.e1, self.sunday + timedelta(days=i), 8.0, 18.0)
        sat = self.shift(sched, self.e1, self.sunday + timedelta(days=6), 16.0, 24.0)
        self.shift(sched, self.e2, self.sunday + timedelta(days=1), 8.0, 16.5)
        self.publish(sched)
        # Ana stayed one more hour on Friday.
        friday = shifts.sorted("start")[-1]
        friday.write({"actual_start": friday.start, "actual_end": friday.end + timedelta(hours=1)})
        meal = self.env["bf.shift.benefit.event"].create({
            "employee_id": self.e1.id, "kind": "overtime_meal", "amount": 18.0,
            "date": self.sunday + timedelta(days=5), "overtime_hours": 3.0,
            "employer_requested": True})
        meal.with_user(self.u_mgr).action_submit()
        meal.with_user(self.u_mgr).action_approve()
        period = self.env["bf.shift.pay.period"].with_user(self.u_mgr).create({
            "name": "Pay", "date_from": sched.date_from, "date_to": sched.date_to})
        period.action_compute()
        ana = period.line_ids.filtered(lambda l: l.employee_id == self.e1)
        hours = {l.code: l.hours for l in ana}
        # 5 x 9.5 + 1 + 7.5 = 56 h: 40 regular, 16 overtime.
        self.assertEqual(hours["REG"], 40.0)
        self.assertEqual(hours["OT"], 16.0)
        # Evening: 2 h a weekday (16-18, 19 on Friday) prorated, Saturday 7.5 h.
        self.assertAlmostEqual(hours["EVE"], 4 * 2 * 9.5 / 10 + 3 * 10.5 / 11 + 7.5, places=2)
        eve = ana.filtered(lambda l: l.code == "EVE")
        self.assertAlmostEqual(eve.amount, round(hours["EVE"] * 2.10, 2), places=2)
        ot = ana.filtered(lambda l: l.code == "OT")
        self.assertEqual(ot.amount, 16 * 30 * 1.5)
        benefit = ana.filtered(lambda l: l.code == "BEN_OVERTIME_MEAL")
        self.assertEqual(benefit.amount, 18.0)
        self.assertEqual(benefit.taxable_quebec, "taxable", "no receipt")
        self.assertEqual(benefit.taxable_federal, "not_taxable")
        bea = period.line_ids.filtered(lambda l: l.employee_id == self.e2)
        # 8:00-16:30: half an hour in the evening window, prorated for the break.
        self.assertEqual({l.code: l.hours for l in bea}, {"REG": 8.0, "EVE": round(0.5 * 8 / 8.5, 4)})
        action = period.action_export()
        self.assertEqual(period.state, "exported")
        csv = base64.b64decode(period.export_attachment_id.datas).decode("utf-8-sig")
        self.assertIn("P-Ana", csv)
        self.assertIn(",OT,", csv)
        self.assertIn("/web/content/", action["url"])
        with self.assertRaisesRegex(UserError, "not recomputed"):
            period.action_compute()
        with self.assertRaises(UserError):
            period.unlink()

    def test_bank_balance(self):
        sched = self.schedule()
        for i in range(1, 6):
            self.shift(sched, self.e1, self.sunday + timedelta(days=i), 8.0, 18.0,
                       to_bank=(i == 5), bank_requested_by_employee=(i == 5))
        self.publish(sched)
        period = self.env["bf.shift.pay.period"].create({
            "name": "Pay", "date_from": sched.date_from, "date_to": sched.date_to})
        period.action_compute()
        self.assertEqual(self.e1.shift_bank_balance, 0.0, "counted once exported")
        period.action_export()
        self.e1.invalidate_recordset(["shift_bank_balance"])
        # 47.5 h: 7.5 h of overtime, all on Friday, banked at 1.5.
        self.assertEqual(self.e1.shift_bank_balance, 11.25)

    def test_manager_without_hr_rights(self):
        """A shift manager is not necessarily an HR officer: the screens they
        use must not read the employees' private fields as themselves."""
        self.assertFalse(self.u_mgr.has_group("hr.group_hr_user"))
        Agreement = self.env["bf.shift.agreement"].with_user(self.u_mgr)
        rows = Agreement.search_read([("id", "=", self.agreement.id)], ["employee_count"])
        self.assertEqual(rows[0]["employee_count"], 3)
        sched = self.schedule()
        self.shift(sched, self.e1, self.sunday + timedelta(days=1), 8.0, 20.0, brk=0)
        sched.with_user(self.u_mgr)._run_checks()
        self.assertIn("meal", sched.warning_ids.mapped("code"))
        sched.with_user(self.u_mgr).action_publish()
        sched._do_publish(notify=False)
        period = self.env["bf.shift.pay.period"].with_user(self.u_mgr).create({
            "name": "P", "date_from": sched.date_from, "date_to": sched.date_to})
        # An empty cache, as in a real request: the cache of the test
        # transaction would otherwise hide the refused read.
        self.env.invalidate_all()
        period.action_compute()
        self.env.invalidate_all()
        period.action_export()
        self.assertEqual(period.state, "exported")
        self.env.invalidate_all()
        self.assertEqual(sched.with_user(self.u_mgr).assignment_ids.employee_id.name, "Ana")

    # ------------------------------------------------------------------
    # What an employee cannot do by calling the server directly
    # ------------------------------------------------------------------

    def test_employee_cannot_withdraw_a_colleague(self):
        pool = self._pool()
        other = pool.member_ids.filtered(lambda m: m.employee_id == self.e2)
        with self.assertRaises(AccessError):
            other.with_user(self.u1).action_withdraw()
        self.assertTrue(other.active)
        mine = pool.member_ids.filtered(lambda m: m.employee_id == self.e1)
        mine.with_user(self.u1).action_withdraw()
        self.assertEqual((mine.active, mine.withdraw_reason), (False, "own"))

    def test_employee_cannot_set_states_or_verdicts(self):
        Event = self.env["bf.shift.benefit.event"].with_user(self.u1)
        with self.assertRaises(AccessError):
            Event.create({"kind": "parking", "amount": 50, "state": "approved"})
        event = Event.create({"kind": "overtime_meal", "amount": 60, "state": "draft"})
        with self.assertRaises(AccessError):
            event.write({"quebec_status": "not_taxable"})
        with self.assertRaises(AccessError):
            event.write({"employee_id": self.e2.id})
        self.assertEqual(event.employee_id, self.e1)
        sched = self.schedule()
        a = self.shift(sched, self.e1, self.sunday + timedelta(days=1))
        self.publish(sched)
        Swap = self.env["bf.shift.swap"].with_user(self.u1)
        for extra in ({"state": "approval"}, {"state": "done"}, {"approved_by": self.u_mgr.id},
                      {"check_summary": "No warning."}):
            with self.assertRaises(AccessError):
                Swap.create(dict({"assignment_id": a.id, "target_id": self.e2.id}, **extra))
        avail = self.env["bf.shift.availability"].with_user(self.u1).create({
            "recurrence": "weekly", "weekday_id": self.env.ref("bf_shift.weekday_4").id})
        with self.assertRaises(AccessError):
            avail.write({"employee_id": self.e2.id})
        self.assertEqual(avail.employee_id, self.e1)

    def test_reasons_follow_the_reader_language(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        event = self.env["bf.shift.benefit.event"].with_user(self.u1).create({
            "kind": "overtime_meal", "amount": 10, "overtime_hours": 1.0})
        self.assertIn("no receipt", event.with_context(lang="en_US").verdict_note)
        event.invalidate_recordset(["verdict_note"])
        self.assertIn("aucun reçu", event.with_context(lang="fr_CA").verdict_note)

    def test_public_methods_answer_rpc(self):
        """XML-RPC cannot carry None: every public action returns a value."""
        import inspect
        for name in self.env.registry:
            if not name.startswith("bf.shift."):
                continue
            model = self.env[name]
            for attr, fn in inspect.getmembers(type(model), inspect.isfunction):
                if attr.startswith("action_") and fn.__module__.startswith("odoo.addons.bf_shift"):
                    src = inspect.getsource(fn)
                    self.assertIn("return", src, "%s.%s returns None" % (name, attr))

    def test_notices_in_the_reader_language(self):
        """Each person is told in their own language, not in the language of
        whoever made the change; the log follows the employee concerned."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.u_mgr.lang = "fr_CA"
        self.u1.lang = "fr_CA"
        self.u2.lang = "en_US"
        sched = self.schedule()
        a = self.shift(sched, self.e1, self.sunday + timedelta(days=1))
        b = self.shift(sched, self.e2, self.sunday + timedelta(days=2))
        sched.with_user(self.u_mgr).action_publish()
        sched.with_user(self.u_mgr)._do_publish(notify=True)

        def notices(user):
            return self.env["mail.message"].sudo().search([
                ("message_type", "=", "user_notification"),
                ("partner_ids", "in", user.partner_id.ids)]).mapped("body")

        self.assertTrue(any("est publié" in m for m in notices(self.u1)), notices(self.u1))
        self.assertTrue(any("is published" in m for m in notices(self.u2)), notices(self.u2))
        b.with_user(self.u_mgr).write({"end": b.end + timedelta(hours=1)})
        self.assertTrue(any("Shift changed" in m for m in notices(self.u2)), notices(self.u2))
        self.assertIn("Shift", b.change_ids.after, "logged in the employee's language (English)")
        a.with_user(self.u_mgr).write({"end": a.end + timedelta(hours=1)})
        self.assertIn("Quart", a.change_ids.after, "logged in the employee's language (French)")

    # ------------------------------------------------------------------
    # Findings of the adversarial review
    # ------------------------------------------------------------------

    def test_rpc_context_cannot_switch_off_the_log(self):
        """An RPC caller controls its context: the internal flags it passes
        must be ignored, on every path that writes as superuser."""
        forged = {"bf_shift_no_log": True, "bf_shift_silent": True, "bf_shift_consent": "given",
                  "bf_shift_reason": "forged", "bf_shift_offer_internal": True,
                  "bf_shift_internal": True}
        pool = self._pool()
        sched = self.schedule()
        open_shift = self.shift(sched, False, self.sunday + timedelta(days=3))
        a = self.shift(sched, self.e1, self.sunday + timedelta(days=1))
        self.publish(sched)
        offer = self.env["bf.shift.offer"].create({"assignment_id": open_shift.id, "pool_id": pool.id})
        offer.action_start()
        line = offer.line_ids.filtered(lambda l: l.state == "offered")
        user = line.employee_id.user_id
        line.with_user(user).with_context(**forged).action_accept()
        change = open_shift.change_ids
        self.assertEqual(len(change), 1, "the acceptance is logged despite bf_shift_no_log")
        self.assertNotEqual(change.reason, "forged")
        swap = self.env["bf.shift.swap"].with_user(self.u1).create({
            "assignment_id": a.id, "target_id": self.e3.id})
        swap.with_user(self.u1).action_submit()
        self.agreement.swap_requires_approval = False
        swap.with_user(self.u3).with_context(**forged).action_colleague_accept()
        self.assertEqual(a.employee_id, self.e3)
        self.assertEqual(len(a.change_ids), 1, "the swap is logged despite bf_shift_no_log")
        with self.assertRaises(UserError):
            line.with_user(self.u_mgr).with_context(**forged).write({"state": "refused"})

    def test_log_is_read_only_even_for_managers(self):
        sched = self.schedule()
        a = self.shift(sched, self.e1, self.sunday + timedelta(days=1))
        self.publish(sched)
        a.write({"end": a.end + timedelta(hours=1)})
        change = a.change_ids
        Change = self.env["bf.shift.change"].with_user(self.u_mgr)
        with self.assertRaises(UserError):
            Change.create({"assignment_id": a.id, "schedule_id": sched.id, "change_type": "modify",
                           "after": "made up"})
        with self.assertRaisesRegex(UserError, "cannot be edited"):
            change.with_user(self.u_mgr).write({"consent": "given"})

    def test_exported_period_is_locked(self):
        sched = self.schedule()
        self.shift(sched, self.e1, self.sunday + timedelta(days=1))
        self.publish(sched)
        period = self.env["bf.shift.pay.period"].with_user(self.u_mgr).create({
            "name": "P", "date_from": sched.date_from, "date_to": sched.date_to})
        period.action_compute()
        period.action_export()
        with self.assertRaises(UserError):
            period.write({"date_to": sched.date_to + timedelta(days=1)})
        with self.assertRaises(UserError):
            period.line_ids[:1].write({"hours": 99})
        with self.assertRaises(UserError):
            period.line_ids[:1].unlink()
        period.action_reset()
        period.line_ids[:1].write({"hours": 1})

    def test_late_acceptance_is_refused(self):
        pool = self._pool()
        sched = self.schedule()
        open_shift = self.shift(sched, False, self.sunday + timedelta(days=3))
        self.publish(sched)
        offer = self.env["bf.shift.offer"].create({"assignment_id": open_shift.id, "pool_id": pool.id})
        offer.action_start()
        line = offer.line_ids.filtered(lambda l: l.state == "offered")
        line.sudo().write({"response_deadline": fields.Datetime.now() - timedelta(minutes=1)})
        with self.assertRaises(UserError):
            line.with_user(line.employee_id.user_id).action_accept()

    def test_companies_are_separated(self):
        other = self.env["res.company"].create({"name": "Other company"})
        g_user = self.env.ref("bf_shift.group_shift_user")
        stranger = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Stranger", "login": "shift_stranger", "email": "stranger@example.com",
            "company_id": other.id, "company_ids": [(6, 0, other.ids)],
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id, g_user.id])]})
        stranger_emp = self.env["hr.employee"].create({"name": "Stranger", "user_id": stranger.id,
                                                       "company_id": other.id})
        self.agreement.company_id = self.env.company
        pool = self._pool()
        self.assertTrue(pool.company_id)
        self.assertFalse(self.env["bf.shift.pool"].with_user(stranger).search([("id", "=", pool.id)]))
        self.assertFalse(self.env["bf.shift.pool.member"].with_user(stranger).search(
            [("pool_id", "=", pool.id)]))
        sched = self.schedule()
        a = self.shift(sched, self.e1, self.sunday + timedelta(days=1))
        self.publish(sched)
        with self.assertRaises(ValidationError):
            self.env["bf.shift.swap"].create({"assignment_id": a.id,
                                              "requester_id": self.e1.id,
                                              "target_id": stranger_emp.id})

    def test_manager_without_email_address(self):
        """Odoo refuses a message whose author has no address, and the refusal
        would undo the publication. Found on the demo, with real data."""
        mgr = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "No Address", "login": "shift_noaddress",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("bf_shift.group_shift_manager").id])]})
        self.assertFalse(mgr.partner_id.email)
        self.env.company.partner_id.email = "company@example.com"
        sched = self.schedule()
        self.shift(sched, self.e1, self.sunday + timedelta(days=1))
        sched.with_user(mgr).action_publish()
        sched.with_user(mgr)._do_publish(notify=True)
        self.assertEqual(sched.state, "published")
        notice = self.env["mail.message"].sudo().search([
            ("model", "=", "bf.shift.schedule"), ("res_id", "=", sched.id),
            ("message_type", "=", "user_notification")])
        self.assertEqual(notice.author_id, self.env.company.partner_id, "signed by the company")
        # Without any address at all, the action still goes through, silently.
        self.env.company.partner_id.email = False
        sched2 = self.schedule(start=self.sunday + timedelta(days=7))
        a = self.shift(sched2, self.e1, self.sunday + timedelta(days=8))
        sched2.with_user(mgr).action_publish()
        sched2.with_user(mgr)._do_publish(notify=True)
        self.assertEqual(sched2.state, "published")
        a.with_user(mgr).write({"end": a.end + timedelta(hours=1)})
        self.assertEqual(len(a.change_ids), 1)

    def test_floor_warning(self):
        weak = self.env["bf.shift.agreement"].create({
            "name": "Weak", "callback_min_hours": 2.0, "notice_days": 5.0})
        self.assertTrue(weak.floor_warning)
        self.assertIn("call-back", weak.floor_warning.lower())
        self.assertFalse(self.agreement.floor_warning)

    def test_copy_next(self):
        sched = self.schedule()
        a = self.shift(sched, self.e1, self.sunday + timedelta(days=1))
        action = sched.action_copy_next()
        new = self.env["bf.shift.schedule"].browse(action["res_id"])
        self.assertEqual(new.date_from, sched.date_from + timedelta(days=7))
        self.assertEqual(new.state, "draft")
        self.assertEqual(new.assignment_ids.start, a.start + timedelta(days=7))
