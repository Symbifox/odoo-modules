"""Retention: the records whose retention period is over are destroyed by
hand, never those under dispute nor those a record that stays points to."""

from datetime import date, timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import BaseCase, tagged

from ..lib import retention
from .common import ShiftCase


@tagged("post_install", "-at_install")
class TestRetentionRule(BaseCase):

    def test_cutoff(self):
        # 2019 is over for 6 years in 2026. December 2019, paid in January
        # 2020, belongs to 2020: kept until the end of 2026.
        self.assertEqual(retention.cutoff(date(2026, 10, 1)), date(2019, 12, 1))
        self.assertEqual(retention.cutoff(date(2026, 1, 1)), date(2019, 12, 1))
        self.assertEqual(retention.cutoff(date(2027, 1, 1)), date(2020, 12, 1))
        self.assertEqual(retention.cutoff(date(2026, 12, 31), 7), date(2018, 12, 1))

    def test_links_are_followed(self):
        candidates = {"s1", "s2", "s3", "p1"}
        # p2 stays (not a candidate) and points to s1, swapped with s2.
        links = [("p2", "s1"), ("s1", "s2"), ("s2", "s1"), ("p1", "s3")]
        self.assertEqual(retention.destroyable(candidates, links), {"s3", "p1"})
        self.assertEqual(retention.destroyable(candidates, []), candidates)
        # A chain: p2 keeps s1, which keeps s2 through a link listed before it.
        self.assertEqual(retention.destroyable({"s1", "s2"}, [("s1", "s2"), ("p2", "s1")]),
                         set())


@tagged("post_install", "-at_install")
class TestRetention(ShiftCase):

    def setUp(self):
        super().setUp()
        self.today = fields.Date.context_today(self.env["bf.shift.schedule"])
        self.cut = retention.cutoff(self.today)
        self.Event = self.env["bf.shift.benefit.event"]
        self.Period = self.env["bf.shift.pay.period"]
        self.Line = self.env["bf.shift.pay.line"]
        self.Availability = self.env["bf.shift.availability"]

    def wizard(self, **vals):
        return self.env["bf.shift.retention.wizard"].with_user(self.u_mgr).create(vals)

    def destroy(self):
        wiz = self.wizard(confirm=True)
        wiz.action_destroy()
        return wiz

    def old_schedule(self, employees, ends=None, name="Old"):
        """A published week ending on ``ends`` (two weeks before the cut-off
        by default), one shift per employee, on Monday, Tuesday..."""
        ends = ends or self.cut - timedelta(days=14)
        start = ends - timedelta(days=6)
        sched = self.schedule(start=start, days=7, name=name)
        for i, emp in enumerate(employees):
            self.shift(sched, emp, start + timedelta(days=1 + i))
        self.publish(sched)
        return sched

    def event(self, employee, day, **vals):
        return self.Event.with_user(self.u_mgr).create(dict({
            "employee_id": employee.id, "kind": "overtime_meal", "date": day, "amount": 15.0,
            "overtime_hours": 2.5, "employer_requested": True}, **vals))

    # ------------------------------------------------------------------

    def test_destroys_what_is_over(self):
        sched = self.old_schedule([self.e1, self.e2])
        a, b = sched.assignment_ids.sorted("start")
        a.with_user(self.u_mgr).write({"end": a.end + timedelta(hours=1)})
        changes = a.change_ids
        self.assertTrue(changes)
        open_shift = self.shift(sched, False, sched.date_from + timedelta(days=4))
        pool = self.env["bf.shift.pool"].create({
            "name": "Pool", "agreement_id": self.agreement.id,
            "member_ids": [(0, 0, {"employee_id": self.e3.id})]})
        offer = self.env["bf.shift.offer"].create({"assignment_id": open_shift.id,
                                                   "pool_id": pool.id})
        offer.action_start()
        offer_lines = offer.line_ids
        self.assertTrue(offer_lines)
        swap = self.env["bf.shift.swap"].with_user(self.u1).create({
            "assignment_id": a.id, "target_id": self.e2.id, "target_assignment_id": b.id})
        sched.action_close()
        receipt = self.env["ir.attachment"].with_user(self.u1).create({
            "name": "receipt.txt", "raw": b"12.50"})
        event = self.Event.with_user(self.u1).create({
            "kind": "overtime_meal", "date": a.date, "amount": 15.0, "overtime_hours": 2.5,
            "employer_requested": True, "assignment_id": a.id,
            "attachment_ids": [(4, receipt.id)]})
        event.action_submit()
        event.with_user(self.u_mgr).action_approve()
        period = self.Period.with_user(self.u_mgr).create({
            "name": "Old pay", "date_from": sched.date_from, "date_to": sched.date_to})
        period.action_compute()
        period.action_export()
        csv = period.export_attachment_id
        lines = period.line_ids
        self.assertTrue(csv and lines)
        dates = self.Availability.with_user(self.u1).create({
            "recurrence": "dates", "date_from": sched.date_from, "date_to": sched.date_to})
        old_weekly = self.Availability.with_user(self.u1).create({
            "recurrence": "weekly", "weekday_id": self.env.ref("bf_shift.weekday_2").id,
            "date_from": sched.date_from - timedelta(days=70), "date_to": sched.date_to})
        weekly = self.Availability.with_user(self.u1).create({
            "recurrence": "weekly", "weekday_id": self.env.ref("bf_shift.weekday_1").id,
            "date_from": sched.date_from})
        recent = self.schedule(name="Recent")
        self.shift(recent, self.e1, self.sunday + timedelta(days=1))
        self.publish(recent)
        messages = self.env["mail.message"].search([
            "|", "&", ("model", "=", "bf.shift.schedule"), ("res_id", "=", sched.id),
            "&", ("model", "=", "bf.shift.offer"), ("res_id", "=", offer.id)])
        self.assertTrue(messages)

        wiz = self.wizard()
        self.assertEqual(wiz.cutoff, self.cut)
        self.assertEqual(
            (wiz.schedule_count, wiz.assignment_count, wiz.period_count, wiz.event_count,
             wiz.availability_count, wiz.kept_count),
            (1, 3, 1, 1, 2, 0))
        wiz.confirm = True
        wiz.action_destroy()
        self.assertEqual(wiz.state, "done")
        self.assertIn(self.cut.isoformat(), wiz.result)
        for records in (sched, a, b, open_shift, changes, offer, offer_lines, swap, event,
                        receipt, period, lines, csv, dates, old_weekly, messages):
            self.assertFalse(records.exists(), records)
        self.assertTrue(recent.exists() and recent.assignment_ids)
        self.assertTrue(weekly.exists(), "a weekly availability without end stays")
        with self.assertRaises(UserError):
            wiz.action_destroy()

    def test_locks_hold_outside_the_action(self):
        sched = self.old_schedule([self.e1])
        a = sched.assignment_ids
        a.with_user(self.u_mgr).write({"end": a.end + timedelta(hours=1)})
        event = self.event(self.e1, a.date)
        event.action_submit()
        event.action_approve()
        period = self.Period.with_user(self.u_mgr).create({
            "name": "Old pay", "date_from": sched.date_from, "date_to": sched.date_to})
        period.action_compute()
        period.action_export()
        # A flag passed by the caller is not the module's: the locks hold.
        forged = {"bf_shift_retention": True, "bf_shift_internal": True}
        for records in (a.change_ids, a, sched, event, period, period.line_ids):
            with self.assertRaises(UserError, msg=records):
                records.sudo().with_context(**forged).unlink()
        self.assertTrue(a.change_ids.exists() and sched.exists() and period.exists())

    def test_dispute_keeps_the_record_whole(self):
        held_sched = self.old_schedule([self.e1], name="Grievance")
        held_sched.with_user(self.u_mgr).write({"retention_hold": True,
                                                "retention_hold_note": "Grief 2019-04"})
        # Ticked by HR on the employee form (a shift manager alone cannot write employees).
        self.e3.shift_retention_hold = True
        with_cyd = self.old_schedule([self.e2, self.e3], ends=self.cut - timedelta(days=28),
                                     name="With Cyd")
        # Cyd only appears in the change log: the shift went to Ana.
        moved = self.old_schedule([self.e3], ends=self.cut - timedelta(days=42), name="Moved")
        moved.assignment_ids.with_user(self.u_mgr).write({"employee_id": self.e1.id})
        self.assertEqual(moved.assignment_ids.employee_id, self.e1)
        only_ana = self.old_schedule([self.e1], ends=self.cut - timedelta(days=56),
                                     name="Only Ana")
        cyd_event = self.event(self.e3, with_cyd.date_from)
        cyd_dates = self.Availability.create({
            "employee_id": self.e3.id, "recurrence": "dates",
            "date_from": with_cyd.date_from, "date_to": with_cyd.date_to})
        cyd_period = self.Period.with_user(self.u_mgr).create({
            "name": "With Cyd", "date_from": with_cyd.date_from, "date_to": with_cyd.date_to})
        cyd_period.action_compute()
        self.assertIn(self.e3, cyd_period.line_ids.employee_id)

        wiz = self.wizard()
        plan = wiz._plan()
        self.assertEqual(plan["schedules"], only_ana.sudo())
        self.assertFalse(plan["periods"] or plan["events"] or plan["availabilities"])
        self.assertEqual(wiz.kept_count, 6)
        self.destroy()
        self.assertFalse(only_ana.exists())
        for records in (held_sched, with_cyd, moved, cyd_event, cyd_dates, cyd_period):
            self.assertTrue(records.exists(), records)

        # Once the dispute is over, the next run destroys them.
        held_sched.with_user(self.u_mgr).retention_hold = False
        self.e3.shift_retention_hold = False
        self.destroy()
        for records in (held_sched, with_cyd, moved, cyd_event, cyd_dates, cyd_period):
            self.assertFalse(records.exists(), records)

    def test_records_that_stay_keep_what_they_point_to(self):
        last = self.old_schedule([self.e1], ends=self.cut - timedelta(days=1), name="Last")
        edge = self.old_schedule([self.e1], ends=self.cut + timedelta(days=6), name="Edge")
        self.assertEqual(edge.date_from, self.cut)
        # A pay period that straddles the cut-off paid an old shift.
        by_pay = self.old_schedule([self.e2], ends=self.cut - timedelta(days=8), name="By pay")
        straddles = self.Period.with_user(self.u_mgr).create({
            "name": "Straddles", "date_from": self.cut - timedelta(days=3),
            "date_to": self.cut + timedelta(days=10)})
        self.Line.create({"period_id": straddles.id, "employee_id": self.e2.id,
                          "code": "REG", "hours": 1.0,
                          "assignment_ids": [(6, 0, by_pay.assignment_ids.ids)]})
        # The same period paid an old benefit.
        old_meal = self.event(self.e2, self.cut - timedelta(days=2))
        self.Line.create({"period_id": straddles.id, "employee_id": self.e2.id,
                          "code": "BEN_OVERTIME_MEAL", "amount": 15.0,
                          "benefit_event_id": old_meal.id})
        # An old shift swapped for a recent one.
        by_swap = self.old_schedule([self.e3], ends=self.cut - timedelta(days=21), name="By swap")
        recent = self.schedule(name="Recent")
        recent_shift = self.shift(recent, self.e2, self.sunday + timedelta(days=1))
        self.publish(recent)
        self.env["bf.shift.swap"].create({
            "requester_id": self.e3.id, "assignment_id": by_swap.assignment_ids.id,
            "target_id": self.e2.id, "target_assignment_id": recent_shift.id})
        # A recent benefit event on an old shift.
        by_event = self.old_schedule([self.e2], ends=self.cut - timedelta(days=35),
                                     name="By event")
        self.event(self.e2, self.today, assignment_id=by_event.assignment_ids.id)

        wiz = self.wizard()
        self.assertEqual(wiz._plan()["schedules"], last.sudo())
        self.assertEqual(wiz.kept_count, 4)
        self.destroy()
        self.assertFalse(last.exists())
        for records in (edge, by_pay, by_swap, by_event, old_meal, straddles.line_ids):
            self.assertTrue(records.exists(), records)
        self.assertEqual(straddles.line_ids.assignment_ids, by_pay.assignment_ids)

    def test_time_bank_balance_does_not_move(self):
        old = self.Period.with_user(self.u_mgr).create({
            "name": "Old", "date_from": self.cut - timedelta(days=20),
            "date_to": self.cut - timedelta(days=7)})
        for code, hours in (("BANKIN", 6.0), ("BANKOUT", 2.0), ("REG", 30.0)):
            self.Line.create({"period_id": old.id, "employee_id": self.e1.id, "code": code,
                              "hours": hours})
        old.write({"state": "computed"})
        old.with_user(self.u_mgr).action_export()
        # Not exported: never counted, nothing to carry.
        draft = self.Period.with_user(self.u_mgr).create({
            "name": "Old draft", "date_from": self.cut - timedelta(days=40),
            "date_to": self.cut - timedelta(days=27)})
        self.Line.create({"period_id": draft.id, "employee_id": self.e1.id, "code": "BANKIN",
                          "hours": 9.0})
        recent = self.Period.with_user(self.u_mgr).create({
            "name": "Recent", "date_from": self.today - timedelta(days=14),
            "date_to": self.today - timedelta(days=1)})
        self.Line.create({"period_id": recent.id, "employee_id": self.e1.id, "code": "BANKIN",
                          "hours": 1.0})
        recent.write({"state": "computed"})
        recent.with_user(self.u_mgr).action_export()
        emp = self.e1
        self.assertEqual(emp.shift_bank_balance, 5.0)
        self.destroy()
        self.assertFalse(old.exists() or draft.exists())
        emp.invalidate_recordset()
        self.assertEqual(emp.shift_bank_carried, 4.0)
        self.assertEqual(emp.shift_bank_balance, 5.0)

    def test_only_the_current_company(self):
        other = self.env["res.company"].create({"name": "Autre société"})
        theirs = self.env["bf.shift.schedule"].create({
            "name": "Theirs", "company_id": other.id,
            "date_from": self.cut - timedelta(days=20), "date_to": self.cut - timedelta(days=14)})
        their_period = self.Period.create({
            "name": "Theirs", "company_id": other.id,
            "date_from": self.cut - timedelta(days=20), "date_to": self.cut - timedelta(days=14)})
        ours = self.old_schedule([self.e1])
        wiz = self.wizard()
        self.assertEqual((wiz.schedule_count, wiz.period_count), (1, 0))
        self.destroy()
        self.assertFalse(ours.exists())
        self.assertTrue(theirs.exists() and their_period.exists())

    def test_who_and_how_long(self):
        with self.assertRaises(AccessError):
            self.env["bf.shift.retention.wizard"].with_user(self.u1).create({})
        for name in ("shift_retention_hold", "shift_retention_hold_note", "shift_bank_carried"):
            self.assertEqual(self.env["hr.employee"]._fields[name].groups,
                             "bf_shift.group_shift_manager", name)
        with self.assertRaises(ValidationError):
            self.wizard(years=5)
        wiz = self.wizard(years=8)
        self.assertEqual(wiz.cutoff, retention.cutoff(self.today, 8))
        with self.assertRaises(UserError):
            wiz.action_destroy()
