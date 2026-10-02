"""Flows changed in 18.0.1.2.0, under real roles."""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import Form, tagged

from ..models.tools import internal
from .common import ShiftCase


@tagged("post_install", "-at_install")
class TestNoticeException(ShiftCase):
    """Art. 59.0.1, 3rd paragraph: services required within the limits of the
    1st paragraph give no right to refuse for want of notice; nor does on-call
    duty at home, which is not work."""

    def _tomorrow(self):
        tomorrow = fields.Date.context_today(self.env["bf.shift.schedule"]) + timedelta(days=1)
        sched = self.schedule(start=tomorrow, days=3)
        return sched, tomorrow

    def test_extension_within_limits_asks_nothing(self):
        sched, day = self._tomorrow()
        a = self.shift(sched, self.e1, day, 8.0, 16.5)   # 8 h
        self.publish(sched)
        # Announced ten days ago: the original notice was long enough.
        internal(a, no_log=True).write({"informed_at": a.informed_at - timedelta(days=10)})
        informed = a.informed_at
        self.assertFalse(a.late_notice)
        a.with_user(self.u_mgr).write({"end": a.end + timedelta(hours=1)})   # 9 h
        change = a.change_ids
        self.assertEqual(change.change_type, "modify")
        self.assertFalse(change.late)
        self.assertEqual(change.consent, "na")
        self.assertEqual(a.informed_at, informed, "the original notice stands")
        self.assertFalse(a.late_notice)
        sched._run_checks()
        self.assertFalse(sched.warning_ids.filtered(lambda w: w.code == "notice"),
                         "no short-notice warning for an extension within the limits")
        # Beyond 8 + 2 hours: the extension may be refused, an answer is asked.
        internal(a, no_log=True).write({"informed_at": informed - timedelta(days=1)})
        a.with_user(self.u_mgr).write({"end": a.end + timedelta(hours=2)})   # 11 h
        change = a.change_ids.sorted("id")[-1]
        self.assertTrue(change.late)
        self.assertEqual(change.consent, "pending")
        self.assertGreater(a.informed_at, informed - timedelta(days=1), "a new notice")

    def test_moving_a_shift_is_not_an_extension(self):
        sched, day = self._tomorrow()
        a = self.shift(sched, self.e1, day, 8.0, 16.5)
        self.publish(sched)
        a.with_user(self.u_mgr).write({"start": a.start + timedelta(hours=1),
                                       "end": a.end + timedelta(hours=1)})
        self.assertEqual(a.change_ids.consent, "pending")

    def test_on_call_asks_nothing(self):
        sched, day = self._tomorrow()
        self.shift(sched, self.e1, day, 8.0, 16.5)
        self.publish(sched)
        oncall = self.shift(sched, self.e2, day, 18.0, 6.0, brk=0, kind="on_call")
        change = oncall.change_ids
        self.assertEqual(change.change_type, "create")
        self.assertFalse(change.late)
        self.assertEqual(change.consent, "na")
        self.assertFalse(oncall.late_notice)
        sched._run_checks()
        self.assertFalse(sched.warning_ids.filtered(
            lambda w: w.code == "notice" and w.assignment_id == oncall))
        work = self.shift(sched, self.e2, day + timedelta(days=1), 8.0, 12.0)
        self.assertEqual(work.change_ids.consent, "pending", "a shift of work still asks")
        self.assertTrue(work.late_notice)


@tagged("post_install", "-at_install")
class TestUsualHoursFromCalendar(ShiftCase):

    def test_hours_of_each_weekday(self):
        cal = self.env["resource.calendar"].create({
            "name": "8-7-10", "attendance_ids": [(5, 0, 0)] + [
                (0, 0, {"name": n, "dayofweek": d, "hour_from": f, "hour_to": t, "day_period": p})
                for n, d, f, t, p in (("Mon", "0", 8, 12, "morning"), ("Mon", "0", 13, 17, "afternoon"),
                                      ("Tue", "1", 9, 16, "morning"),
                                      ("Fri", "4", 7, 12, "morning"), ("Fri", "4", 12, 13, "lunch"),
                                      ("Fri", "4", 13, 18, "afternoon"))]})
        self.e1.resource_calendar_id = cal
        usual = self.e1._shift_usual_hours()
        monday = self.sunday + timedelta(days=1)
        self.assertEqual(usual(monday), 8.0)
        self.assertEqual(usual(monday + timedelta(days=1)), 7.0)
        self.assertEqual(usual(monday + timedelta(days=4)), 10.0, "the lunch line is not work")
        self.assertEqual(usual(monday + timedelta(days=2)), cal.hours_per_day,
                         "a day the calendar does not work: its average day")
        # The check follows it: 9.5 h on Tuesday is beyond 7 + 2.
        sched = self.schedule()
        self.shift(sched, self.e1, monday + timedelta(days=1), 7.0, 17.0)   # 9.5 h
        self.shift(sched, self.e1, monday + timedelta(days=4), 7.0, 19.0)   # 11.5 h on Friday
        sched._run_checks()
        warns = sched.warning_ids.filtered(lambda w: w.code == "daily_extra")
        self.assertEqual(len(warns), 1)
        self.assertEqual(warns.assignment_id.date, monday + timedelta(days=1))
        self.assertIn("9.5 h that day, more than 9 h (usual day of 7 h on that weekday, "
                      "plus 2 h)", warns.message)
        self.assertIn("LNT art. 59.0.1", warns.message)

    def test_variable_hours(self):
        self.assertFalse(self.e1._shift_variable_hours())
        self.e1.shift_flexible_hours = True
        self.assertTrue(self.e1._shift_variable_hours())
        self.e1.shift_flexible_hours = False
        flex = self.env["resource.calendar"].create({"name": "Flex", "flexible_hours": True,
                                                     "hours_per_day": 7.0})
        self.e1.resource_calendar_id = flex
        self.assertTrue(self.e1._shift_variable_hours())


@tagged("post_install", "-at_install")
class TestSplitShiftMessage(ShiftCase):

    def test_split_day_warns_at_twelve_hours(self):
        sched = self.schedule()
        monday = self.sunday + timedelta(days=1)
        self.shift(sched, self.e1, monday, 6.0, 12.5)     # 6 h
        self.shift(sched, self.e1, monday, 14.0, 21.0)    # 6.5 h: 12.5 h in two pieces
        sched._run_checks()
        warns = sched.warning_ids.filtered(lambda w: w.employee_id == self.e1)
        self.assertEqual(warns.mapped("code"), ["max_24h"],
                         "12 h in 24 on a split day; no '+2 h' warning")
        self.assertIn("more than 12 h for variable or non-continuous hours", warns.message)
        self.assertIn("LNT art. 59.0.1", warns.message)


@tagged("post_install", "-at_install")
class TestBenefitEvents(ShiftCase):

    def test_provided_meal_without_receipt(self):
        Event = self.env["bf.shift.benefit.event"].with_user(self.u1)
        vals = {"kind": "overtime_meal", "amount": 15.0, "overtime_hours": 2.5,
                "employer_requested": True, "date": self.sunday + timedelta(days=1)}
        reimbursed = Event.create(dict(vals))
        self.assertEqual(reimbursed.provision, "reimbursed", "the default keeps receipts")
        self.assertEqual(reimbursed.quebec_status, "taxable")
        provided = Event.create(dict(vals, provision="provided",
                                     date=self.sunday + timedelta(days=2)))
        self.assertEqual((provided.quebec_status, provided.federal_status),
                         ("not_taxable", "not_taxable"))

    def test_planned_overtime_from_the_shift(self):
        sched = self.schedule()
        monday = self.sunday + timedelta(days=1)
        extra = self.shift(sched, self.e1, monday, 16.5, 19.0, brk=0)   # 2.5 h planned
        self.publish(sched)
        # Sent home after 1.5 h: the planned length still counts.
        extra.write({"actual_start": extra.start, "actual_end": extra.start + timedelta(hours=1.5)})
        self.assertEqual(extra.worked_hours, 1.5)
        form = Form(self.env["bf.shift.benefit.event"].with_user(self.u_mgr))
        form.employee_id = self.e1
        form.kind = "overtime_meal"
        form.assignment_id = extra
        self.assertEqual(form.overtime_hours, 2.5)
        form.employer_requested = True
        form.provision = "provided"
        form.amount = 15.0
        event = form.save()
        self.assertNotIn("under_two_hours", event.verdict_codes or "")
        self.assertEqual(event.quebec_status, "not_taxable")

    def test_subsidized_meal_value(self):
        Event = self.env["bf.shift.benefit.event"].with_user(self.u1)
        free = Event.create({"kind": "subsidized_meal", "meal_cost": 9.0, "meal_price_paid": 3.5,
                             "amount": 99.0})
        self.assertEqual(free.amount, 5.5, "the benefit is the cost less the price paid")
        self.assertEqual((free.quebec_status, free.federal_status), ("taxable", "taxable"))
        paid = Event.create({"kind": "subsidized_meal", "meal_cost": 9.0, "meal_price_paid": 9.0})
        self.assertEqual(paid.amount, 0.0)
        self.assertEqual((paid.quebec_status, paid.federal_status), ("not_taxable", "not_taxable"))
        # Another kind keeps the amount typed in.
        taxi = Event.create({"kind": "taxi", "amount": 32.0})
        self.assertEqual(taxi.amount, 32.0)

    def test_subsidized_meal_without_cost_keeps_its_amount(self):
        # As a meal entered before 18.0.1.1.0: no cost, an amount typed in.
        # Approving it must not bring the amount down to 0.
        meal = self.env["bf.shift.benefit.event"].with_user(self.u1).create({
            "kind": "subsidized_meal", "amount": 6.0})
        self.assertEqual(meal.amount, 6.0)
        meal.action_submit()
        meal.with_user(self.u_mgr).action_approve()
        self.assertEqual(meal.amount, 6.0)
        self.assertEqual((meal.quebec_status, meal.federal_status), ("check", "check"))
        meal.with_user(self.u_mgr).write({"meal_cost": 9.0, "meal_price_paid": 5.0})
        self.assertEqual(meal.amount, 4.0, "once the cost is known, the value is computed")

    def test_taxi_federal_exemption_is_the_managers(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        taxi = self.env["bf.shift.benefit.event"].with_user(self.u1).create({
            "kind": "taxi", "amount": 32.0, "overtime_hours": 3.0, "employer_requested": True,
            "provision": "provided", "no_transit_or_safety": True})
        self.assertEqual((taxi.quebec_status, taxi.federal_status), ("not_taxable", "taxable"))
        self.assertIn("personal travel for the CRA", taxi.with_context(lang="en_US").verdict_note)
        taxi.invalidate_recordset(["verdict_note"])
        self.assertIn("déplacement personnel", taxi.with_context(lang="fr_CA").verdict_note)
        with self.assertRaises(AccessError):
            taxi.write({"federal_exemption_reason": "Night shift, no bus"})
        taxi.with_user(self.u_mgr).write({"federal_exemption_reason": "Ruling 2027-01"})
        self.assertEqual(taxi.federal_status, "not_taxable")
        taxi.invalidate_recordset(["verdict_note"])
        self.assertIn("(Ruling 2027-01)", taxi.with_context(lang="en_US").verdict_note)

    def test_subsidized_reasons_in_french(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        meal = self.env["bf.shift.benefit.event"].with_user(self.u1).create({
            "kind": "subsidized_meal", "meal_cost": 9.0, "meal_price_paid": 2.0})
        self.assertIn("la différence", meal.with_context(lang="fr_CA").verdict_note)
        status = dict(meal.with_context(lang="fr_CA")._fields["provision"]
                      ._description_selection(meal.with_context(lang="fr_CA").env))
        self.assertEqual(status["provided"], "Fourni par l'employeur")

    def test_refusals_in_the_callers_language(self):
        # Outside a method, _() finds no self and, over RPC, no request
        # language: the refusal went out in English to a French employee.
        self.env["res.lang"]._activate_lang("fr_CA")
        Event = self.env["bf.shift.benefit.event"].with_user(self.u1).with_context(lang="fr_CA")
        with self.assertRaisesRegex(AccessError, "Vous ne pouvez pas remplir ces champs vous-même"):
            Event.create({"kind": "taxi", "amount": 20.0, "federal_exemption_reason": "x"})
        mine = Event.create({"kind": "parking", "amount": 5.0})
        with self.assertRaisesRegex(AccessError, "Cette fiche doit rester la vôtre"):
            mine.write({"employee_id": self.e2.id})


@tagged("post_install", "-at_install")
class TestOnCallAndMinimumFlows(ShiftCase):
    """On-call pay, remote call-back and short shifts, from the forms to the
    pay period."""

    def test_pay_period_lines(self):
        self.agreement.write({"on_call_pay": "per_hour", "on_call_amount": 2.0})
        sched = self.schedule()
        mon = self.sunday + timedelta(days=1)
        self.shift(sched, self.e1, mon, 18.0, 6.0, brk=0, kind="on_call")   # 12 h
        remote = self.shift(sched, self.e1, mon, 22.0, 22.5, brk=0, kind="callback")
        remote.with_user(self.u_mgr).write({"remote": True})
        self.shift(sched, self.e1, mon + timedelta(days=1), 22.0, 23.0, brk=0, kind="callback")
        short = self.shift(sched, self.e2, mon, 8.0, 10.0, brk=0)
        self.publish(sched)
        period = self.env["bf.shift.pay.period"].with_user(self.u_mgr).create({
            "name": "Pay", "date_from": sched.date_from, "date_to": sched.date_to})
        period.action_compute()
        ana = period.line_ids.filtered(lambda l: l.employee_id == self.e1)
        oncall = ana.filtered(lambda l: l.code == "ONCALL")
        self.assertEqual((oncall.hours, oncall.amount), (12.0, 24.0))
        self.assertEqual((oncall.taxable_quebec, oncall.taxable_federal), ("taxable", "taxable"))
        # The agreement's call-back minimum is 4 h: only the call-back on site.
        cbtop = ana.filtered(lambda l: l.code == "CBTOP")
        self.assertEqual(cbtop.hours, 3.0)
        self.assertNotIn(remote, cbtop.assignment_ids)
        bea = period.line_ids.filtered(lambda l: l.employee_id == self.e2)
        mintop = bea.filtered(lambda l: l.code == "MINTOP")
        self.assertEqual((mintop.hours, mintop.amount), (1.0, 30.0))
        self.assertEqual(mintop.assignment_ids, short)
        self.assertIn("art. 58", mintop.name)

    def test_exception_and_force_majeure(self):
        sched = self.schedule()
        mon = self.sunday + timedelta(days=1)
        short = self.shift(sched, self.e2, mon, 8.0, 10.0, brk=0)
        self.publish(sched)
        self.agreement.write({"presence_exception": "multiple_presences",
                              "callback_min_hours": 2.0})
        self.assertFalse(self.agreement.floor_warning,
                         "under an exception, a call-back minimum under 3 h is lawful")
        self.assertEqual(self.agreement._effective_params().callback_min_hours, 2.0)
        period = self.env["bf.shift.pay.period"].with_user(self.u_mgr).create({
            "name": "Pay", "date_from": sched.date_from, "date_to": sched.date_to})
        period.action_compute()
        self.assertNotIn("MINTOP", period.line_ids.mapped("code"))
        self.agreement.presence_exception = False
        self.assertTrue(self.agreement.floor_warning)
        short.with_user(self.u_mgr).write({"force_majeure": True})
        period.action_compute()
        self.assertNotIn("MINTOP", period.line_ids.mapped("code"))
        short.with_user(self.u_mgr).write({"force_majeure": False})
        period.action_compute()
        self.assertIn("MINTOP", period.line_ids.mapped("code"))

    def test_labels_in_french(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        env = self.env(context=dict(self.env.context, lang="fr_CA"))
        labels = dict(env["bf.shift.agreement"]._fields["on_call_pay"]._description_selection(env))
        self.assertEqual(labels["per_period"], "Forfait par période de garde")
        sched = self.schedule()
        self.shift(sched, self.e2, self.sunday + timedelta(days=1), 8.0, 10.0, brk=0)
        self.publish(sched)
        period = env["bf.shift.pay.period"].with_user(self.u_mgr).create({
            "name": "Paie", "date_from": sched.date_from, "date_to": sched.date_to})
        period.action_compute()
        mintop = period.line_ids.filtered(lambda l: l.code == "MINTOP")
        self.assertEqual(mintop.name, "Minimum de 3 heures (LNT, art. 58), complément")


@tagged("post_install", "-at_install")
class TestAveragingFlows(ShiftCase):
    """LNT art. 53, from the working conditions to the pay period."""

    def _averaging(self, source="collective", weeks=2, **kw):
        self.agreement.write(dict({"averaging_source": source, "averaging_weeks": weeks,
                                   "averaging_anchor": self.sunday}, **kw))

    def _week(self, sched, emp, sunday, h1):
        for d in range(1, 6):
            self.shift(sched, emp, sunday + timedelta(days=d), 8.0, h1, brk=0)

    def _pay(self, date_from, date_to):
        period = self.env["bf.shift.pay.period"].with_user(self.u_mgr).create({
            "name": "Pay", "date_from": date_from, "date_to": date_to})
        period.action_compute()
        return period

    def _hours(self, period, emp):
        """Hours by code, the evening premium of the shared agreement aside."""
        out = {}
        for line in period.line_ids.filtered(lambda l: l.employee_id == emp and l.code != "EVE"):
            out[line.code] = round(out.get(line.code, 0.0) + line.hours, 2)
        return out

    def test_limits_of_each_source(self):
        with self.assertRaises(ValidationError):
            self._averaging("individual", 5)
        self._averaging("individual", 4)
        self.assertEqual(self.agreement._effective_params().averaging_week_cap, 50.0)
        with self.assertRaises(ValidationError):
            self.agreement.averaging_anchor = False
        with self.assertRaises(ValidationError):
            self._averaging("collective", 1)
        self._averaging("collective", 26)
        self.assertEqual(self.agreement._effective_params().averaging_week_cap, 0.0)
        with self.assertRaises(ValidationError):
            self.agreement.averaging_max_weeks = 8
        self._averaging("collective", 8, averaging_max_weeks=8)
        with self.assertRaises(ValidationError):
            self.agreement.kind = "lnt"
        lnt = self.env["bf.shift.agreement"].create({"name": "LNT", "kind": "lnt"})
        with self.assertRaises(ValidationError):
            lnt.write({"averaging_source": "collective", "averaging_anchor": self.sunday})
        lnt.write({"averaging_source": "individual", "averaging_anchor": self.sunday})
        # No averaging: the weeks are counted one by one, whatever is left typed.
        self.agreement.averaging_source = "none"
        self.assertEqual(self.agreement._effective_params().averaging_weeks, 1)

    def test_pay_periods(self):
        self._averaging()
        sched = self.schedule(days=14, name="Two weeks")
        second = self.sunday + timedelta(days=7)
        self._week(sched, self.e1, self.sunday, 16.8)    # 44 h
        self._week(sched, self.e1, second, 15.2)         # 36 h
        self._week(sched, self.e2, self.sunday, 16.8)    # 44 h
        self._week(sched, self.e2, second, 16.8)         # 44 h
        self.publish(sched)
        week1 = self._pay(self.sunday, self.sunday + timedelta(days=6))
        self.assertEqual(self._hours(week1, self.e1), {"REG": 44.0})
        self.assertEqual(self._hours(week1, self.e2), {"REG": 44.0},
                         "the averaging period is not over: usual rate")
        week2 = self._pay(second, second + timedelta(days=6))
        self.assertEqual(self._hours(week2, self.e1), {"REG": 36.0})
        self.assertEqual(self._hours(week2, self.e2), {"REG": 36.0, "OT": 8.0})
        ot = week2.line_ids.filtered(lambda l: l.employee_id == self.e2 and l.code == "OT")
        self.assertEqual(ot.amount, 8 * 30.0 * 1.5)
        both = self._pay(self.sunday, second + timedelta(days=6))
        self.assertEqual(self._hours(both, self.e1), {"REG": 80.0})
        self.assertEqual(self._hours(both, self.e2), {"REG": 80.0, "OT": 8.0})
        # A pay period that ends on the Wednesday of the second week holds its
        # last day no more: the overtime waits; the next one pays it, part on
        # its own hours and the premium only on the hours already paid.
        early = self._pay(self.sunday, second + timedelta(days=3))
        self.assertNotIn("OT", self._hours(early, self.e2))
        late = self._pay(second + timedelta(days=4), second + timedelta(days=6))
        self.assertEqual(self._hours(late, self.e2), {"REG": 9.6, "OT": 8.0},
                         "Thursday and Friday hold 17.6 h, 8 of them become overtime")
        # Without averaging, the first week alone pays 4 h.
        self.agreement.averaging_source = "none"
        week1.action_compute()
        self.assertEqual(self._hours(week1, self.e2), {"REG": 40.0, "OT": 4.0})

    def test_premium_only_on_hours_paid_earlier(self):
        self._averaging()
        sched = self.schedule(days=14, name="Two weeks")
        second = self.sunday + timedelta(days=7)
        self._week(sched, self.e2, self.sunday, 19.0)                   # 55 h
        for d in (1, 2, 3):                                             # Mon-Wed, 12 h
            self.shift(sched, self.e2, second + timedelta(days=d), 8.0, 20.0, brk=0)
        friday = self.shift(sched, self.e2, second + timedelta(days=5), 8.0, 13.0, brk=0)
        self.publish(sched)
        # 96 h: 16 owed. The pay period holds only the Friday (5 h) of the
        # last week; the other 11 h were paid at the usual rate before.
        period = self._pay(second + timedelta(days=4), second + timedelta(days=6))
        self.assertEqual(self._hours(period, self.e2), {"OT": 5.0, "OTAVG": 11.0})
        otavg = period.line_ids.filtered(lambda l: l.code == "OTAVG")
        self.assertEqual((otavg.multiplier, otavg.amount), (0.5, 11 * 30.0 * 0.5))
        self.assertEqual(period.total_hours, 5.0, "hours already counted are not counted again")
        ot = period.line_ids.filtered(lambda l: l.code == "OT")
        self.assertEqual(ot.assignment_ids, friday)

    def test_warnings(self):
        self._averaging()
        sched = self.schedule(days=14, name="Two weeks")
        second = self.sunday + timedelta(days=7)
        self._week(sched, self.e1, self.sunday, 19.0)   # 55 h
        self._week(sched, self.e1, second, 17.0)        # 45 h
        self._week(sched, self.e2, self.sunday, 19.0)   # 55 h
        self._week(sched, self.e2, second, 19.0)        # 55 h
        sched._run_checks()
        ana = sched.warning_ids.filtered(lambda w: w.employee_id == self.e1)
        self.assertNotIn("weekly_max", ana.mapped("code"), "50 h on average")
        bea = sched.warning_ids.filtered(
            lambda w: w.employee_id == self.e2 and w.code == "weekly_max")
        self.assertEqual(len(bea), 1)
        self.assertIn("2 weeks from %s" % self.sunday.isoformat(), bea.message)
        self.assertIn("55 h a week on average", bea.message)
        self.assertIn("art. 53", bea.message)
        # An individual agreement: no week beyond 50 h.
        self._averaging("individual")
        sched._run_checks()
        cap = sched.warning_ids.filtered(lambda w: w.code == "averaging_week_cap")
        self.assertEqual(set(cap.employee_id.ids), {self.e1.id, self.e2.id})
        self.assertIn("10 hours beyond the 40-hour norm", cap[0].message)

    def test_warnings_see_the_whole_period(self):
        # The period's first week is in another schedule, already published.
        self._averaging()
        second = self.sunday + timedelta(days=7)
        first = self.schedule(name="Week 1")
        self._week(first, self.e1, self.sunday, 19.0)    # 55 h
        self.publish(first)
        sched = self.schedule(start=second, name="Week 2")
        self._week(sched, self.e1, second, 18.0)         # 50 h
        sched._run_checks()
        warning = sched.warning_ids.filtered(lambda w: w.code == "weekly_max")
        self.assertEqual(len(warning), 1, "52.5 h a week on the average of the two")
        self.assertIn("52.5 h a week on average", warning.message)

    def test_pay_sees_the_whole_period(self):
        # 8 weeks under a collective agreement: the pay period of the last two
        # weeks must count the first week, beyond the 4 weeks looked back for
        # the holiday indemnity.
        self._averaging(weeks=8)
        sched = self.schedule(days=56, name="Eight weeks")
        self._week(sched, self.e1, self.sunday, 18.0)    # 50 h
        for w in range(1, 8):
            self._week(sched, self.e1, self.sunday + timedelta(days=7 * w), 16.0)   # 40 h
        self.publish(sched)
        last = self._pay(self.sunday + timedelta(days=42), self.sunday + timedelta(days=55))
        self.assertEqual(self._hours(last, self.e1), {"REG": 70.0, "OT": 10.0})

    def test_labels_in_french(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        env = self.env(context=dict(self.env.context, lang="fr_CA"))
        labels = dict(env["bf.shift.agreement"]._fields["averaging_source"]
                      ._description_selection(env))
        self.assertEqual(labels["individual"], "Entente individuelle écrite")
        self._averaging("individual", 4)
        with self.assertRaises(ValidationError) as caught:
            self.agreement.with_env(env).averaging_weeks = 5
        self.assertIn("4 semaines", str(caught.exception))
