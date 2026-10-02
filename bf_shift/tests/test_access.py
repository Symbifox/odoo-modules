"""What a caller cannot do by calling the server directly, beyond the forms:
context defaults, another company, someone else's attachment, a frozen
schedule, the wizards of others."""

from datetime import timedelta

from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tools import mute_logger

from odoo.tests import tagged

from ..lib import retention
from ..models.tools import internal
from .common import ShiftCase


@tagged("post_install", "-at_install")
class TestAccess(ShiftCase):

    def setUp(self):
        super().setUp()
        self.today = fields.Date.context_today(self.env["bf.shift.schedule"])
        self.cut = retention.cutoff(self.today)
        self.Event = self.env["bf.shift.benefit.event"]

    def old_schedule(self, employees, name="Old"):
        ends = self.cut - timedelta(days=14)
        start = ends - timedelta(days=6)
        sched = self.schedule(start=start, days=7, name=name)
        for i, emp in enumerate(employees):
            self.shift(sched, emp, start + timedelta(days=1 + i))
        self.publish(sched)
        return sched

    # ------------------------------------------------------------------
    # Context defaults

    def test_employee_defaults_cannot_set_what_vals_cannot(self):
        # Odoo fills the missing values from default_<field> AFTER the
        # checks on vals: the state, the federal exemption, the approver.
        forged = self.Event.with_user(self.u1).with_context(
            default_state="approved", default_federal_exemption_reason="x")
        event = forged.create({"kind": "taxi", "amount": 480.0,
                               "date": self.sunday + timedelta(days=1)})
        self.assertEqual(event.state, "draft")
        self.assertFalse(event.federal_exemption_reason)
        self.assertEqual(event.federal_status, "taxable")
        sched = self.schedule()
        mine = self.shift(sched, self.e1, self.sunday + timedelta(days=1))
        theirs = self.shift(sched, self.e2, self.sunday + timedelta(days=2))
        self.publish(sched)
        swap = self.env["bf.shift.swap"].with_user(self.u1).with_context(
            default_state="approval", default_check_summary="No warning.",
            default_approved_by=self.u_mgr.id).create({
                "assignment_id": mine.id, "target_id": self.e2.id,
                "target_assignment_id": theirs.id})
        self.assertEqual(swap.state, "draft")
        self.assertFalse(swap.approved_by)

    def test_manager_defaults_still_apply(self):
        event = self.Event.with_user(self.u_mgr).with_context(
            default_federal_exemption_reason="Strike").create({
                "employee_id": self.e1.id, "kind": "taxi", "amount": 30.0,
                "date": self.sunday + timedelta(days=1)})
        self.assertEqual(event.federal_exemption_reason, "Strike")

    # ------------------------------------------------------------------
    # Another company

    def test_destruction_stays_in_the_managers_companies(self):
        other = self.env["res.company"].create({"name": "Other company"})
        theirs = self.env["bf.shift.schedule"].create({
            "name": "Theirs", "company_id": other.id,
            "date_from": self.cut - timedelta(days=20), "date_to": self.cut - timedelta(days=14)})
        Wizard = self.env["bf.shift.retention.wizard"].with_user(self.u_mgr)
        with self.assertRaises(AccessError):
            Wizard.create({"company_id": other.id, "confirm": True}).action_destroy()
        mine = Wizard.create({"confirm": True})
        with self.assertRaises(AccessError):
            mine.write({"company_id": other.id})
            mine.action_destroy()
        self.assertTrue(theirs.exists())

    def test_warnings_and_premiums_of_another_company_are_hidden(self):
        other = self.env["res.company"].create({"name": "Other company"})
        sched = self.env["bf.shift.schedule"].create({
            "name": "Theirs", "company_id": other.id,
            "date_from": self.sunday, "date_to": self.sunday + timedelta(days=6)})
        warning = self.env["bf.shift.warning"].create({
            "schedule_id": sched.id, "employee_id": self.e1.id, "code": "open",
            "message": "Theirs"})
        agreement = self.env["bf.shift.agreement"].create({
            "name": "Theirs", "company_id": other.id,
            "premium_rule_ids": [(0, 0, {"name": "Night", "code": "NIGHT"})]})
        Warning = self.env["bf.shift.warning"].with_user(self.u_mgr)
        Premium = self.env["bf.shift.premium.rule"].with_user(self.u_mgr)
        self.assertFalse(Warning.search([("id", "=", warning.id)]))
        self.assertFalse(Premium.search([("id", "in", agreement.premium_rule_ids.ids)]))

    # ------------------------------------------------------------------
    # Someone else's attachment

    def test_employee_links_only_her_own_receipts(self):
        foreign = self.env["ir.attachment"].create({
            "name": "contract.pdf", "raw": b"contract",
            "res_model": "hr.employee", "res_id": self.e3.id})
        with self.assertRaises(AccessError):
            self.Event.with_user(self.u1).create({
                "kind": "parking", "amount": 5.0, "date": self.sunday,
                "attachment_ids": [(6, 0, foreign.ids)]})
        event = self.Event.with_user(self.u1).create({
            "kind": "parking", "amount": 5.0, "date": self.sunday})
        with self.assertRaises(AccessError):
            event.write({"attachment_ids": [(4, foreign.id)]})
        own = self.env["ir.attachment"].with_user(self.u1).create({
            "name": "receipt.txt", "raw": b"5.00"})
        event.write({"attachment_ids": [(4, own.id)]})
        self.assertEqual(event.attachment_ids, own)

    def test_destruction_spares_a_file_that_belongs_elsewhere(self):
        # A file of the employee record linked to an old event: the
        # destruction only takes receipts.
        foreign = self.env["ir.attachment"].create({
            "name": "contract.pdf", "raw": b"contract",
            "res_model": "hr.employee", "res_id": self.e3.id})
        own = self.env["ir.attachment"].with_user(self.u1).create({
            "name": "receipt.txt", "raw": b"5.00"})
        event = self.Event.with_user(self.u_mgr).create({
            "employee_id": self.e1.id, "kind": "parking", "amount": 5.0,
            "date": self.cut - timedelta(days=30)})
        # A link made before 18.0.1.2.0, when nothing checked it.
        event.sudo().write({"attachment_ids": [(6, 0, (foreign | own).ids)]})
        self.env["bf.shift.retention.wizard"].with_user(self.u_mgr).create(
            {"confirm": True}).action_destroy()
        self.assertFalse(event.exists())
        self.assertFalse(own.exists(), "the receipt goes with its event")
        self.assertTrue(foreign.exists(), "a file of another record stays")

    # ------------------------------------------------------------------
    # A published schedule stays frozen

    def test_state_moves_only_through_the_buttons(self):
        sched = self.schedule()
        self.shift(sched, self.e1, self.sunday + timedelta(days=1))
        self.publish(sched)
        log = sched.assignment_ids
        log.with_user(self.u_mgr).write({"end": log.end + timedelta(hours=1)})
        self.assertTrue(sched.change_ids)
        with self.assertRaises(UserError):
            sched.with_user(self.u_mgr).write({"state": "draft"})
        with self.assertRaises(UserError):
            self.env["bf.shift.schedule"].with_user(self.u_mgr).create({
                "name": "Born published", "state": "published",
                "date_from": self.sunday, "date_to": self.sunday + timedelta(days=6)})
        sched.with_user(self.u_mgr).action_close()
        sched.with_user(self.u_mgr).action_reopen()
        self.assertEqual(sched.state, "published")
        self.assertTrue(sched.change_ids.exists())

    def test_a_schedule_under_dispute_is_never_deleted(self):
        sched = self.schedule(name="Disputed draft")
        sched.retention_hold = True
        with self.assertRaises(UserError):
            sched.with_user(self.u_mgr).unlink()
        self.assertTrue(sched.exists())

    def test_a_schedule_under_dispute_keeps_its_pay_and_benefits(self):
        sched = self.old_schedule([self.e1], name="Disputed")
        shift = sched.assignment_ids
        sched.action_close()
        event = self.Event.with_user(self.u_mgr).create({
            "employee_id": self.e1.id, "kind": "overtime_meal", "date": shift.date,
            "amount": 15.0, "overtime_hours": 2.5, "employer_requested": True,
            "assignment_id": shift.id})
        period = self.env["bf.shift.pay.period"].with_user(self.u_mgr).create({
            "name": "Old pay", "date_from": sched.date_from, "date_to": sched.date_to})
        period.action_compute()
        sched.retention_hold = True
        self.env["bf.shift.retention.wizard"].with_user(self.u_mgr).create(
            {"confirm": True}).action_destroy()
        self.assertTrue(sched.exists() and period.exists() and event.exists(),
                        "the pay and the benefits of a disputed schedule are evidence too")

    # ------------------------------------------------------------------
    # Wizards and onchanges of others

    def test_refusals_stay_private(self):
        sched = self.schedule()
        open_shift = self.shift(sched, False, self.sunday + timedelta(days=3))
        self.publish(sched)
        pool = self.env["bf.shift.pool"].create({
            "name": "List", "agreement_id": self.agreement.id,
            "member_ids": [(0, 0, {"employee_id": e.id}) for e in (self.e1, self.e2)]})
        offer = self.env["bf.shift.offer"].with_user(self.u_mgr).create({
            "assignment_id": open_shift.id, "pool_id": pool.id})
        offer.action_start()
        line = offer.line_ids.filtered(lambda l: l.state == "offered")
        wiz = self.env["bf.shift.refuse.wizard"].with_user(self.u1).create(
            {"offer_line_id": line.id, "reason": "Medical appointment"})
        Refuse = self.env["bf.shift.refuse.wizard"].with_user(self.u2)
        self.assertFalse(Refuse.search([("id", "=", wiz.id)]))
        mine = self.env["bf.shift.retention.wizard"].with_user(self.u_mgr).create({})
        other_mgr = self.u_mgr.copy({"login": "shift_mgr2", "name": "Mgr Two"})
        self.assertFalse(self.env["bf.shift.retention.wizard"].with_user(other_mgr).search(
            [("id", "=", mine.id)]))

    def test_onchanges_tell_nothing_to_others(self):
        old = self.old_schedule([self.e1])
        old.retention_hold = True
        plan = self.env["bf.shift.retention.wizard"].with_user(self.u2).new({})
        self.assertEqual((plan.schedule_count, plan.kept_count), (0, 0))
        day = self.sunday + timedelta(days=1)
        for _i in range(2):
            self.Event.with_user(self.u1).create({
                "kind": "overtime_meal", "amount": 10.0, "date": day,
                "overtime_hours": 2.5, "employer_requested": True})
        probe = self.Event.with_user(self.u2).new({
            "employee_id": self.e1.id, "kind": "overtime_meal", "date": day})
        self.assertEqual(probe.times_this_week, 1, "Bea does not learn Ana's meals")

    # ------------------------------------------------------------------
    # Remarks of the review

    def test_time_bank_carry_is_not_writable(self):
        hr_mgr = self.u_mgr.copy({"login": "shift_mgr_hr", "name": "Mgr HR"})
        hr_mgr.groups_id = [(4, self.env.ref("hr.group_hr_manager").id)]
        with self.assertRaises(UserError):
            self.e1.with_user(hr_mgr).write({"shift_bank_carried": 40.0})
        self.assertEqual(self.e1.shift_bank_carried, 0.0)

    def test_archived_responsible_does_not_sign(self):
        self.env.company.partner_id.email = "company@example.com"
        sched = self.schedule()
        sched.user_id = self.u_mgr
        open_shift = self.shift(sched, False, self.sunday + timedelta(days=3))
        self.publish(sched)
        pool = self.env["bf.shift.pool"].create({
            "name": "List", "agreement_id": self.agreement.id,
            "member_ids": [(0, 0, {"employee_id": self.e1.id})]})
        self.u_mgr.active = False
        offer = self.env["bf.shift.offer"].create({
            "assignment_id": open_shift.id, "pool_id": pool.id})
        offer.action_start()
        notice = self.env["mail.message"].sudo().search([
            ("model", "=", "bf.shift.offer"), ("res_id", "=", offer.id),
            ("message_type", "=", "user_notification")])
        self.assertEqual(notice.author_id, self.env.company.partner_id)

    # ------------------------------------------------------------------
    # Second review round

    def test_personal_defaults_cannot_set_what_vals_cannot(self):
        # Every internal user may set her own defaults (ir.default), and
        # Odoo applies them after the checks on vals.
        Default = self.env["ir.default"].with_user(self.u1)
        Default.set("bf.shift.benefit.event", "state", "approved", user_id=True)
        Default.set("bf.shift.benefit.event", "federal_exemption_reason", "x", user_id=True)
        Default.set("bf.shift.swap", "state", "approval", user_id=True)
        Default.set("bf.shift.swap", "check_summary", "No warning.", user_id=True)
        event = self.Event.with_user(self.u1).create({
            "kind": "taxi", "amount": 480.0, "date": self.sunday + timedelta(days=1)})
        self.assertEqual((event.state, event.federal_exemption_reason), ("draft", False))
        sched = self.schedule()
        mine = self.shift(sched, self.e1, self.sunday + timedelta(days=1))
        theirs = self.shift(sched, self.e2, self.sunday + timedelta(days=2))
        self.publish(sched)
        swap = self.env["bf.shift.swap"].with_user(self.u1).create({
            "assignment_id": mine.id, "target_id": self.e2.id, "target_assignment_id": theirs.id})
        self.assertEqual(swap.state, "draft")
        self.assertFalse(swap.check_summary)

    def test_a_schedule_is_born_a_draft_whatever_the_defaults(self):
        Sched = self.env["bf.shift.schedule"].with_user(self.u_mgr)
        vals = {"name": "W", "date_from": self.sunday, "date_to": self.sunday + timedelta(days=6)}
        with self.assertRaises(UserError):
            Sched.with_context(default_state="closed").create(dict(vals))
        self.env["ir.default"].with_user(self.u_mgr).set(
            "bf.shift.schedule", "state", "published", user_id=True)
        with self.assertRaises(UserError):
            Sched.create(dict(vals))

    def test_a_shift_stays_in_its_published_schedule(self):
        published = self.schedule(name="Published")
        shift = self.shift(published, self.e1, self.sunday + timedelta(days=1))
        self.publish(published)
        shift.with_user(self.u_mgr).write({"end": shift.end + timedelta(hours=1)})
        draft = self.schedule(start=self.sunday + timedelta(days=7), name="Draft")
        loose = self.shift(draft, self.e2, self.sunday + timedelta(days=8))
        Assign = self.env["bf.shift.assignment"].with_user(self.u_mgr)
        with self.assertRaises(UserError):
            Assign.browse(shift.id).write({"schedule_id": draft.id})
        with self.assertRaises(UserError):
            Assign.browse(loose.id).write({"schedule_id": published.id})
        other_draft = self.schedule(start=self.sunday + timedelta(days=14), name="Draft 2")
        Assign.browse(loose.id).write({"schedule_id": other_draft.id})
        self.assertEqual(loose.schedule_id, other_draft)

    def test_the_log_is_never_deleted_with_its_shift(self):
        sched = self.schedule()
        shift = self.shift(sched, self.e1, self.sunday + timedelta(days=1))
        self.publish(sched)
        shift.with_user(self.u_mgr).write({"end": shift.end + timedelta(hours=1)})
        self.assertTrue(shift.change_ids)
        # Even past every lock, the database refuses: only the destruction
        # deletes the changes, and it deletes them first.
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError), self.cr.savepoint():
            internal(shift.sudo(), retention=True).unlink()

    def test_a_manager_links_only_her_own_files(self):
        loose = self.env["ir.attachment"].create({"name": "theme.scss", "raw": b"body{}"})
        with self.assertRaises(AccessError):
            self.Event.with_user(self.u_mgr).create({
                "employee_id": self.e1.id, "kind": "parking", "amount": 5.0,
                "date": self.cut - timedelta(days=30), "attachment_ids": [(6, 0, loose.ids)]})

    def test_destruction_spares_a_loose_file_of_someone_else(self):
        # Linked before 18.0.1.2.0, when nothing checked the link.
        loose = self.env["ir.attachment"].create({"name": "theme.scss", "raw": b"body{}"})
        event = self.Event.with_user(self.u_mgr).create({
            "employee_id": self.e1.id, "kind": "parking", "amount": 5.0,
            "date": self.cut - timedelta(days=30)})
        event.sudo().write({"attachment_ids": [(4, loose.id)]})
        self.env["bf.shift.retention.wizard"].with_user(self.u_mgr).create(
            {"confirm": True}).action_destroy()
        self.assertFalse(event.exists())
        self.assertTrue(loose.exists())

    def test_derived_fields_are_never_written(self):
        draft = self.schedule()
        shift = self.shift(draft, self.e1, self.sunday + timedelta(days=1))
        shift.with_user(self.u_mgr).write({"schedule_state": "published"})
        self.assertEqual(shift.schedule_state, "draft")
        self.assertFalse(self.env["bf.shift.assignment"].with_user(self.u2).search(
            [("id", "=", shift.id)]), "a draft shift stays hidden from the others")
        event = self.Event.with_user(self.u_mgr).create({
            "employee_id": self.e1.id, "kind": "taxi", "amount": 30.0,
            "date": self.sunday + timedelta(days=1)})
        event.with_user(self.u_mgr).write({"federal_status": "not_taxable"})
        self.assertEqual(event.federal_status, "taxable")

    def test_time_bank_carry_is_not_set_at_creation(self):
        hr_mgr = self.u_mgr.copy({"login": "shift_mgr_hr2", "name": "Mgr HR 2"})
        hr_mgr.groups_id = [(4, self.env.ref("hr.group_hr_manager").id)]
        with self.assertRaises(UserError):
            self.env["hr.employee"].with_user(hr_mgr).create(
                {"name": "New", "shift_bank_carried": 40.0})

    # ------------------------------------------------------------------
    # Third review round

    def _running_offer(self):
        sched = self.schedule()
        open_shift = self.shift(sched, False, self.sunday + timedelta(days=3))
        self.publish(sched)
        pool = self.env["bf.shift.pool"].create({
            "name": "List", "agreement_id": self.agreement.id,
            "member_ids": [(0, 0, {"employee_id": e.id}) for e in (self.e1, self.e2)]})
        offer = self.env["bf.shift.offer"].with_user(self.u_mgr).create({
            "assignment_id": open_shift.id, "pool_id": pool.id})
        offer.action_start()
        return offer

    def test_the_trace_of_an_offer_is_kept(self):
        offer = self._running_offer()
        Offer = self.env["bf.shift.offer"].with_user(self.u_mgr)
        with self.assertRaises(UserError):
            Offer.browse(offer.id).unlink()
        with self.assertRaises(UserError):
            Offer.browse(offer.id).write({"state": "unfilled"})
        with self.assertRaises(UserError):
            offer.line_ids[:1].with_user(self.u_mgr).unlink()
        line = offer.line_ids.filtered(lambda l: l.state == "offered")
        line.with_user(line.employee_id.user_id).action_accept()
        self.assertEqual(offer.state, "filled")
        with self.assertRaises(UserError):
            Offer.browse(offer.id).write({"filled_by": False})
        draft = self.env["bf.shift.offer"].with_user(self.u_mgr).create({
            "assignment_id": offer.assignment_id.id, "pool_id": offer.pool_id.id})
        draft.unlink()
        self.assertFalse(draft.exists(), "a draft offer still goes")

    def test_the_trace_of_a_swap_is_kept(self):
        sched = self.schedule()
        mine = self.shift(sched, self.e1, self.sunday + timedelta(days=1))
        theirs = self.shift(sched, self.e2, self.sunday + timedelta(days=2))
        self.publish(sched)
        swap = self.env["bf.shift.swap"].with_user(self.u1).create({
            "assignment_id": mine.id, "target_id": self.e2.id, "target_assignment_id": theirs.id})
        swap.action_submit()
        swap.with_user(self.u2).action_colleague_accept()
        if swap.state == "approval":
            swap.with_user(self.u_mgr).action_approve()
        self.assertEqual(swap.state, "done")
        Swap = self.env["bf.shift.swap"].with_user(self.u_mgr)
        with self.assertRaises(UserError):
            Swap.browse(swap.id).unlink()
        with self.assertRaises(UserError):
            Swap.browse(swap.id).write({"state": "refused"})

    def test_destruction_with_a_log_left_on_another_schedule(self):
        # Before 18.0.1.2.0, a published shift could move to another schedule
        # and its log kept naming the first one.
        old = self.old_schedule([self.e1], name="Old, moved from")
        shift = old.assignment_ids
        shift.with_user(self.u_mgr).write({"end": shift.end + timedelta(hours=1)})
        recent = self.schedule(name="Recent")
        internal(shift, no_log=True).write({"schedule_id": recent.id})
        self.env["bf.shift.retention.wizard"].with_user(self.u_mgr).create(
            {"confirm": True}).action_destroy()
        self.assertTrue(old.exists(), "its log is still read through the recent schedule")
        self.assertTrue(shift.change_ids)

    def test_time_bank_carry_is_not_a_default(self):
        hr_mgr = self.u_mgr.copy({"login": "shift_mgr_hr3", "name": "Mgr HR 3"})
        hr_mgr.groups_id = [(4, self.env.ref("hr.group_hr_manager").id)]
        emp = self.env["hr.employee"].with_user(hr_mgr).with_context(
            default_shift_bank_carried=40.0).create({"name": "New"})
        self.assertEqual(emp.sudo().shift_bank_carried, 0.0)

    # ------------------------------------------------------------------
    # Fourth review round: the trace is not made up at creation either

    def test_the_trace_is_not_made_up_at_creation(self):
        offer = self._running_offer()
        Line = self.env["bf.shift.offer.line"].with_user(self.u_mgr)
        with self.assertRaises(UserError):
            Line.create({"offer_id": offer.id, "employee_id": self.e3.id, "state": "refused", "rank": 9,
                         "response_channel": "self", "refusal_reason": "Never said"})
        Offer = self.env["bf.shift.offer"].with_user(self.u_mgr)
        with self.assertRaises(UserError):
            Offer.create({"assignment_id": offer.assignment_id.id, "pool_id": offer.pool_id.id,
                          "state": "filled", "filled_by": self.e1.id})
        with self.assertRaises(UserError):
            Offer.with_context(default_state="filled").create(
                {"assignment_id": offer.assignment_id.id, "pool_id": offer.pool_id.id})
        sched = self.schedule(start=self.sunday + timedelta(days=7), name="Next")
        mine = self.shift(sched, self.e1, self.sunday + timedelta(days=8))
        theirs = self.shift(sched, self.e2, self.sunday + timedelta(days=9))
        self.publish(sched)
        Swap = self.env["bf.shift.swap"].with_user(self.u_mgr)
        with self.assertRaises(UserError):
            Swap.create({"requester_id": self.e1.id, "assignment_id": mine.id, "target_id": self.e2.id,
                         "target_assignment_id": theirs.id, "state": "done",
                         "approved_by": self.u_mgr.id, "check_summary": "No warning."})
        swap = Swap.create({"requester_id": self.e1.id, "assignment_id": mine.id,
                            "target_id": self.e2.id, "target_assignment_id": theirs.id})
        self.assertEqual(swap.state, "draft", "a plain request is still created")

    # ------------------------------------------------------------------
    # Seen on a screen: records with no name showed "model,id"

    def test_records_have_a_readable_name(self):
        event = self.Event.with_user(self.u1).create({
            "kind": "parking", "amount": 5.0, "date": self.sunday})
        self.assertNotIn("bf.shift", event.display_name)
        self.assertIn("Ana", event.display_name)
        availability = self.env["bf.shift.availability"].with_user(self.u1).create({
            "recurrence": "weekly", "weekday_id": self.env.ref("bf_shift.weekday_2").id})
        self.assertNotIn("bf.shift", availability.display_name)
        offer = self._running_offer()
        for rec in (offer.line_ids[:1], offer.pool_id.member_ids[:1]):
            self.assertNotIn("bf.shift", rec.display_name, rec)

    # ------------------------------------------------------------------
    # Holes the mutation pass found once the links became two-way

    def test_a_person_under_dispute_keeps_a_period_paid_only_by_benefits(self):
        # No shift of hers in an old schedule: only her line keeps the period.
        day = self.cut - timedelta(days=30)
        event = self.Event.with_user(self.u_mgr).create({
            "employee_id": self.e1.id, "kind": "parking", "amount": 5.0, "date": day})
        period = self.env["bf.shift.pay.period"].with_user(self.u_mgr).create({
            "name": "Old pay", "date_from": day - timedelta(days=6), "date_to": day})
        self.env["bf.shift.pay.line"].create({
            "period_id": period.id, "employee_id": self.e1.id, "code": "BEN_PARKING",
            "amount": 5.0, "benefit_event_id": event.id})
        self.e1.shift_retention_hold = True
        self.env["bf.shift.retention.wizard"].with_user(self.u_mgr).create(
            {"confirm": True}).action_destroy()
        self.assertTrue(period.exists() and event.exists())

    def test_only_managers_have_access_to_the_wizard(self):
        # The wizard also refuses by its own checks: the access rule is
        # checked here for itself.
        Access = self.env["ir.model.access"]
        self.assertFalse(Access.with_user(self.u1).check(
            "bf.shift.retention.wizard", "create", raise_exception=False))
        self.assertTrue(Access.with_user(self.u_mgr).check(
            "bf.shift.retention.wizard", "create", raise_exception=False))
