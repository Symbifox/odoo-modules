import logging
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..lib import retention
from ..models.tools import internal

_logger = logging.getLogger(__name__)


class BfShiftRetentionWizard(models.TransientModel):
    """Destroy the shift records whose retention period is over.

    Run by hand, never on a schedule. Destroyed rather than anonymised: the
    regulation on anonymisation (A-2.1, r. 0.1) asks for a re-identification
    risk analysis under a competent person's supervision and a register,
    which no button can promise (in a small team, the night shift of one
    position on Tuesdays is one person). Destruction is the other way out of
    P-39.1, s. 23.

    An old record stays when a dispute is under way for the schedule or for
    one of the persons it concerns, and when a record that stays points to it
    (a pay period to the shifts it paid, a benefit to its shift, a swap
    between two schedules): the evidence stays whole.
    """

    _name = "bf.shift.retention.wizard"
    _description = "Destroy old shift records"

    company_id = fields.Many2one("res.company", required=True, readonly=True,
                                 default=lambda self: self.env.company)
    years = fields.Integer(
        "Years kept after the end of the year", required=True, default=retention.MIN_YEARS,
        help="At least 6: the tax laws keep the supporting documents of the pay 6 years after "
        "the end of the year they relate to.")
    cutoff = fields.Date("Destroy what ends before", compute="_compute_plan")
    schedule_count = fields.Integer("Schedules", compute="_compute_plan")
    assignment_count = fields.Integer("Shifts", compute="_compute_plan")
    period_count = fields.Integer("Pay periods", compute="_compute_plan")
    event_count = fields.Integer("Benefit events", compute="_compute_plan")
    availability_count = fields.Integer("Availability", compute="_compute_plan")
    kept_count = fields.Integer(
        "Old records kept", compute="_compute_plan",
        help="Old enough, but kept: a dispute is under way for the schedule or for a person it "
        "concerns, or a record that stays points to them.")
    confirm = fields.Boolean("I understand that the destruction cannot be undone")
    state = fields.Selection([("plan", "To confirm"), ("done", "Done")], default="plan",
                             required=True)
    result = fields.Text(readonly=True)

    @api.constrains("years")
    def _check_years(self):
        for rec in self:
            if rec.years < retention.MIN_YEARS:
                raise ValidationError(_(
                    "The tax laws keep the supporting documents of the pay at least "
                    "%(years)s years after the end of the year.", years=retention.MIN_YEARS))

    @api.depends("years", "company_id")
    def _compute_plan(self):
        for rec in self:
            # Counted as superuser: an onchange is anyone's call, and the
            # counts would tell a stranger that a dispute is under way.
            if rec.years < retention.MIN_YEARS or not rec._may_plan():
                rec.update({"cutoff": False, "schedule_count": 0, "assignment_count": 0,
                            "period_count": 0, "event_count": 0, "availability_count": 0,
                            "kept_count": 0})
                continue
            plan = rec._plan()
            rec.update({
                "cutoff": plan["cutoff"],
                "schedule_count": len(plan["schedules"]),
                "assignment_count": len(plan["schedules"].assignment_ids),
                "period_count": len(plan["periods"]),
                "event_count": len(plan["events"]),
                "availability_count": len(plan["availabilities"]),
                "kept_count": plan["kept"],
            })

    def _may_plan(self):
        """A shift manager, for one of the companies of their user (not of
        the context, which the caller chooses)."""
        return bool(self.company_id) and \
            self.env.user.has_group("bf_shift.group_shift_manager") and \
            self.company_id in self.env.user.company_ids

    def _check_destroying_company(self):
        for rec in self:
            if not rec._may_plan():
                raise AccessError(_("Only a shift manager of the company destroys its old "
                                    "records."))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._check_destroying_company()
        return records

    def write(self, vals):
        res = super().write(vals)
        if "company_id" in vals:
            self._check_destroying_company()
        return res

    # ------------------------------------------------------------------

    def _plan(self):
        """What goes, as of today. Read as superuser: the plan must see every
        record, whoever the manager is allowed to read."""
        self.ensure_one()
        env = self.sudo().env
        # The server's date, not the caller's time zone: a zone set ahead on
        # December 31 would move the cut-off a year.
        cut = retention.cutoff(fields.Date.today(), self.years)
        company = self.company_id
        Schedule = env["bf.shift.schedule"]
        Period = env["bf.shift.pay.period"]
        Event = env["bf.shift.benefit.event"]
        Availability = env["bf.shift.availability"]
        Line = env["bf.shift.pay.line"]
        Swap = env["bf.shift.swap"]
        held = env["hr.employee"].with_context(active_test=False).search(
            [("shift_retention_hold", "=", True)])

        schedules = Schedule.search([("company_id", "=", company.id), ("date_to", "<", cut)])
        periods = Period.search([("company_id", "=", company.id), ("date_to", "<", cut)])
        events = Event.search([("company_id", "=", company.id), ("date", "<", cut)])
        availabilities = Availability.search([
            ("company_id", "=", company.id), ("date_to", "!=", False), ("date_to", "<", cut)])

        shifts = schedules.assignment_ids
        swaps = Swap.search(["|", ("assignment_id", "in", shifts.ids),
                             ("target_assignment_id", "in", shifts.ids)])
        people = defaultdict(lambda: env["hr.employee"])
        for shift in shifts:
            people[shift.schedule_id] |= (
                shift.employee_id | shift.change_ids.employee_id
                | shift.change_ids.previous_employee_id | shift.offer_ids.line_ids.employee_id)
        for swap in swaps:
            for shift in swap.assignment_id | swap.target_assignment_id:
                people[shift.schedule_id] |= swap.requester_id | swap.target_id

        candidates = set()
        for sched in schedules:
            if not sched.retention_hold and not (people[sched] & held):
                candidates.add(("schedule", sched.id))
        for period in periods:
            if not (period.line_ids.employee_id & held):
                candidates.add(("period", period.id))
        candidates |= {("event", e.id) for e in events if e.employee_id not in held}
        candidates |= {("availability", a.id) for a in availabilities
                       if a.employee_id not in held}

        links = []
        # Evidence clusters stay or go together: a period and the shifts it
        # paid, a benefit and its shift, a swap and both its schedules. A
        # schedule kept for a dispute keeps its pay and its benefits.
        for line in Line.search([("assignment_ids", "in", shifts.ids)]):
            for shift in line.assignment_ids & shifts:
                links.append((("period", line.period_id.id), ("schedule", shift.schedule_id.id)))
                links.append((("schedule", shift.schedule_id.id), ("period", line.period_id.id)))
        for line in Line.search([("benefit_event_id", "in", events.ids)]):
            links.append((("period", line.period_id.id), ("event", line.benefit_event_id.id)))
        for event in Event.search([("assignment_id", "in", shifts.ids)]):
            links.append((("event", event.id), ("schedule", event.assignment_id.schedule_id.id)))
            links.append((("schedule", event.assignment_id.schedule_id.id), ("event", event.id)))
        for swap in swaps:
            ends = (swap.assignment_id | swap.target_assignment_id).schedule_id
            for a in ends:
                for b in ends - a:
                    links.append((("schedule", a.id), ("schedule", b.id)))
        # Before 18.0.1.2.0 a published shift could move to another schedule:
        # its log still names the first one. The two stay or go together, or
        # the database (ondelete restrict) would refuse the whole destruction.
        Change = env["bf.shift.change"]
        for change in Change.search(["|", ("schedule_id", "in", schedules.ids),
                                     ("assignment_id", "in", shifts.ids)]):
            a, b = change.schedule_id, change.assignment_id.schedule_id
            if a != b:
                links.append((("schedule", a.id), ("schedule", b.id)))
                links.append((("schedule", b.id), ("schedule", a.id)))

        gone = retention.destroyable(candidates, links)

        def pick(records, kind):
            return records.filtered(lambda r: (kind, r.id) in gone)

        plan = {
            "cutoff": cut,
            "schedules": pick(schedules, "schedule"),
            "periods": pick(periods, "period"),
            "events": pick(events, "event"),
            "availabilities": pick(availabilities, "availability"),
        }
        plan["kept"] = (len(schedules) + len(periods) + len(events) + len(availabilities)
                        - len(gone))
        return plan

    def action_destroy(self):
        self.ensure_one()
        if not self.env.user.has_group("bf_shift.group_shift_manager"):
            raise AccessError(_("Only a shift manager can destroy old records."))
        self._check_destroying_company()
        if self.state != "plan":
            raise UserError(_("This destruction is already done."))
        if not self.confirm:
            raise UserError(_("Tick the box to confirm: the destruction cannot be undone."))
        plan = self._plan()
        counts = {
            "schedules": len(plan["schedules"]),
            "shifts": len(plan["schedules"].assignment_ids),
            "periods": len(plan["periods"]),
            "events": len(plan["events"]),
            "availability": len(plan["availabilities"]),
        }
        self._destroy(plan)
        result = _(
            "Destroyed: %(schedules)s schedule(s) with %(shifts)s shift(s), %(periods)s pay "
            "period(s), %(events)s benefit event(s), %(availability)s availability record(s). "
            "Kept for a dispute or linked to a kept record: %(kept)s. Cut-off: before %(cutoff)s.",
            kept=plan["kept"], cutoff=plan["cutoff"].isoformat(), **counts)
        _logger.info("bf_shift retention, company %s, by user %s: %s", self.company_id.id,
                     self.env.uid, result)
        self.write({"state": "done", "result": result})
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "views": [[False, "form"]],
            "target": "new",
        }

    def _destroy(self, plan):
        """Delete through the ORM, with the only flag the locks let through:
        the chatter, followers and activities go with their record."""
        env = internal(self.env["bf.shift.schedule"].sudo(), retention=True).env
        Attachment = env["ir.attachment"]

        def attachments(records):
            return Attachment.search([("res_model", "=", records._name),
                                      ("res_id", "in", records.ids)]) if records else Attachment

        periods = plan["periods"].with_env(env)
        self._carry_time_bank(periods)
        files = attachments(periods)
        periods.unlink()

        events = plan["events"].with_env(env)
        # Receipts only: a file of the event, or a loose file uploaded by
        # whoever declared the event or the person it concerns. A file that
        # belongs elsewhere stays, even when someone linked it to an old event.
        receipts = env["ir.attachment"]
        for event in events:
            owners = event.create_uid | event.employee_id.user_id
            receipts |= event.attachment_ids.filtered(
                lambda f, ev=event, own=owners:
                    (f.res_model == ev._name and f.res_id in (0, ev.id))
                    or (not f.res_model and f.create_uid in own))
        files |= attachments(events)
        events.unlink()
        if receipts:
            used = env["bf.shift.benefit.event"].search(
                [("attachment_ids", "in", receipts.ids)]).attachment_ids
            files |= receipts - used

        schedules = plan["schedules"].with_env(env)
        shifts = schedules.assignment_ids
        offers = env["bf.shift.offer"].search([("assignment_id", "in", shifts.ids)])
        swaps = env["bf.shift.swap"].search(["|", ("assignment_id", "in", shifts.ids),
                                             ("target_assignment_id", "in", shifts.ids)])
        files |= attachments(offers) | attachments(swaps) | attachments(shifts) \
            | attachments(schedules)
        offers.unlink()
        swaps.unlink()
        (shifts.change_ids | env["bf.shift.change"].search(
            [("schedule_id", "in", schedules.ids)])).unlink()
        shifts.unlink()
        schedules.unlink()

        plan["availabilities"].with_env(env).unlink()
        files.exists().unlink()

    def _carry_time_bank(self, periods):
        """The time bank balance adds up the bank lines of the exported
        periods: the net of the destroyed ones is carried over, so the
        balance stays what it was."""
        net = defaultdict(float)
        for line in periods.filtered(lambda p: p.state == "exported").line_ids:
            if line.code == "BANKIN":
                net[line.employee_id] += line.hours
            elif line.code == "BANKOUT":
                net[line.employee_id] -= line.hours
        for employee, hours in net.items():
            if hours:
                employee.shift_bank_carried += hours
