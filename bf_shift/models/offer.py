from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..lib import engine
from .tools import (flag, internal, is_shift_manager, lang_of, post, schedule_activity,
                    to_local, tz_of)

LINE_STATES = [
    ("waiting", "Waiting"),
    ("offered", "Offered"),
    ("accepted", "Accepted"),
    ("refused", "Refused"),
    ("no_answer", "No answer"),
    ("skipped", "Skipped"),
    ("withdrawn", "No longer needed"),
]

SKIP_REASONS = [
    ("conflict", "Already working then"),
    ("unavailable", "Declared unavailable"),
]


class BfShiftOffer(models.Model):
    """Offering one open shift down a call list, one person at a time.

    Every step is kept with its time: who was offered, who was skipped and
    why, who refused and when. That trace is what a grievance asks for.
    """

    _name = "bf.shift.offer"
    _description = "Open shift offer"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    assignment_id = fields.Many2one("bf.shift.assignment", required=True, ondelete="restrict",
                                    index=True)
    schedule_id = fields.Many2one(related="assignment_id.schedule_id", store=True)
    company_id = fields.Many2one(related="assignment_id.company_id", store=True)
    pool_id = fields.Many2one("bf.shift.pool", string="Call list", required=True,
                              default=lambda self: self._default_pool())
    method = fields.Selection(related="pool_id.method")
    state = fields.Selection([("draft", "Draft"), ("running", "Running"), ("filled", "Filled"),
                              ("unfilled", "Unfilled"), ("cancelled", "Cancelled")],
                             default="draft", required=True, tracking=True)
    response_minutes = fields.Integer("Time to answer (min)",
                                      default=lambda self: self._default_minutes())
    hours = fields.Float(related="assignment_id.planned_hours")
    line_ids = fields.One2many("bf.shift.offer.line", "offer_id", string="Candidates")
    filled_by = fields.Many2one("hr.employee", readonly=True)

    @api.model
    def _default_pool(self):
        assignment = self.env["bf.shift.assignment"].browse(
            self.env.context.get("default_assignment_id"))
        domain = []
        if assignment and assignment.job_id:
            domain = [("job_id", "in", (assignment.job_id.id, False))]
        return self.env["bf.shift.pool"].search(domain, limit=1)

    @api.model
    def _default_minutes(self):
        pool = self._default_pool()
        return pool.agreement_id.offer_response_minutes or 60

    @api.depends("assignment_id")
    def _compute_name(self):
        for rec in self:
            rec.name = _("Offer: %(when)s", when=rec.assignment_id._when_label()) \
                if rec.assignment_id else _("Offer")

    @api.onchange("pool_id")
    def _onchange_pool(self):
        if self.pool_id.agreement_id.offer_response_minutes:
            self.response_minutes = self.pool_id.agreement_id.offer_response_minutes

    # ------------------------------------------------------------------

    def action_start(self):
        for offer in self:
            if offer.state != "draft":
                raise UserError(_("This offer has already started."))
            if not offer.assignment_id.is_open:
                raise UserError(_("The shift is no longer open."))
            offer._build_candidates()
            offer.state = "running"
            post(offer, body=_("Offer started with %(count)s candidate(s), order: %(method)s.",
                                      count=len(offer.line_ids.filtered(
                                          lambda l: l.state == "waiting")),
                                      method=dict(offer.pool_id._fields["method"]._description_selection(
                                          self.env))[offer.pool_id._method()]))
            offer._offer_next()
        return True

    def _build_candidates(self):
        self.ensure_one()
        members = self.pool_id.member_ids.filtered("active")
        assignment = self.assignment_id
        candidates = []
        for member in members:
            candidates.append(engine.Candidate(
                key=member.id,
                seniority=member.seniority_date,
                hours_offered=member.hours_offered,
                tie=member.sequence * 100000 + member.id,
                blocked=self._blocked_reason(member.employee_id, assignment),
            ))
        order = engine.rank_candidates(candidates, self.pool_id._method())
        Member = self.env["bf.shift.pool.member"]
        vals = []
        for rank, cand in enumerate(order, start=1):
            member = Member.browse(cand.key)
            vals.append({
                "offer_id": self.id,
                "member_id": member.id,
                "employee_id": member.employee_id.id,
                "rank": rank,
                "state": "skipped" if cand.blocked else "waiting",
                "skip_reason": cand.blocked or False,
                "seniority_date": member.seniority_date,
                "hours_offered_before": member.hours_offered,
            })
        self.env["bf.shift.offer.line"].create(vals)

    def _blocked_reason(self, employee, assignment):
        clash = self.env["bf.shift.assignment"].sudo().search_count([
            ("employee_id", "=", employee.id),
            ("state", "!=", "cancelled"),
            ("start", "<", assignment.end),
            ("end", ">", assignment.start),
            ("kind", "in", ("work", "callback", "leave", "bank_leave")),
        ])
        if clash:
            return "conflict"
        if self.env["bf.shift.availability"].sudo()._is_unavailable(
                employee, assignment.start, assignment.end):
            return "unavailable"
        return ""

    def _offer_next(self):
        self.ensure_one()
        if self.state != "running":
            return
        nxt = self.line_ids.filtered(lambda l: l.state == "waiting").sorted("rank")[:1]
        if not nxt:
            self.state = "unfilled"
            post(self, body=_("Nobody took the shift: the list is exhausted."))
            responsible = self.schedule_id.user_id
            if responsible:
                env = self.with_context(lang=lang_of(self.env, user=responsible)).env
                schedule_activity(
                    self, "mail.mail_activity_data_todo", user_id=responsible.id,
                    summary=env._("Open shift still unfilled"))
            return
        now = fields.Datetime.now()
        internal(nxt, offer_internal=True).write({"state": "offered", "offered_at": now,
                   "response_deadline": now + timedelta(minutes=self.response_minutes or 60)})
        user = nxt.employee_id.sudo().user_id
        if user:
            env = self.with_context(lang=lang_of(self.env, user=user)).env
            deadline = to_local(nxt.response_deadline, tz_of(nxt.employee_id, self.env))
            schedule_activity(
                self.sudo(), "mail.mail_activity_data_todo", user_id=user.id,
                date_deadline=fields.Date.context_today(self),
                summary=env._("Answer by %(time)s", time=deadline.strftime("%Y-%m-%d %H:%M")))

    def _close_activities(self, employee):
        user = employee.sudo().user_id
        if user:
            self.sudo().activity_ids.filtered(lambda a: a.user_id == user).action_feedback(
                feedback=_("Answered"))

    def action_cancel(self):
        if not is_shift_manager(self.env):
            raise AccessError(_("Only a shift manager cancels an offer."))
        for offer in self:
            internal(offer.line_ids.filtered(lambda l: l.state in ("waiting", "offered")),
                     offer_internal=True).write({"state": "withdrawn"})
            offer.sudo().activity_ids.unlink()
            offer.state = "cancelled"
        return True

    @api.model
    def _cron_expire(self):
        """Offers not answered in time: recorded as such, and the next person
        on the list gets the shift."""
        lines = self.env["bf.shift.offer.line"].sudo().search([
            ("state", "=", "offered"),
            ("response_deadline", "<", fields.Datetime.now()),
        ])
        for line in lines:
            line._record("no_answer", channel="system")


class BfShiftOfferLine(models.Model):
    _name = "bf.shift.offer.line"
    _description = "Open shift offer, one candidate"
    _order = "offer_id, rank"

    offer_id = fields.Many2one("bf.shift.offer", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="offer_id.company_id", store=True)
    assignment_id = fields.Many2one(related="offer_id.assignment_id")
    member_id = fields.Many2one("bf.shift.pool.member", ondelete="set null")
    employee_id = fields.Many2one("hr.employee", required=True)
    employee_user_id = fields.Many2one("res.users", compute="_compute_user", store=True,
                                       compute_sudo=True)
    rank = fields.Integer(required=True)
    state = fields.Selection(LINE_STATES, default="waiting", required=True)
    skip_reason = fields.Selection(SKIP_REASONS)
    seniority_date = fields.Date(readonly=True)
    hours_offered_before = fields.Float("Hours offered before", readonly=True)
    offered_at = fields.Datetime(readonly=True)
    response_deadline = fields.Datetime("Answer by", readonly=True)
    responded_at = fields.Datetime(readonly=True)
    response_channel = fields.Selection([("self", "By the employee"),
                                         ("manager", "Recorded by a manager"),
                                         ("system", "Deadline passed")], readonly=True)
    recorded_by = fields.Many2one("res.users", readonly=True)
    refusal_reason = fields.Char(readonly=True)

    @api.depends("employee_id.user_id")
    def _compute_user(self):
        for rec in self:
            rec.employee_user_id = rec.employee_id.user_id

    def write(self, vals):
        # Answers go through _record(), which keeps the trace; a plain write
        # from the interface cannot rewrite it.
        if not self.env.su and not flag(self.env, "offer_internal"):
            allowed = {"state"} if all(r.state in ("waiting", "offered") for r in self) else set()
            if set(vals) - allowed or vals.get("state") not in (None, "withdrawn"):
                raise UserError(_("Answers to an offer are recorded with the Accept "
                                  "and Refuse buttons."))
        return super().write(vals)

    def action_accept(self):
        self.ensure_one()
        self._record("accepted")
        return True

    def action_refuse(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Refuse the shift"),
            "res_model": "bf.shift.refuse.wizard",
            "views": [[False, "form"]],
            "target": "new",
            "context": {"default_offer_line_id": self.id},
        }

    def _record(self, answer, reason=False, channel=None):
        """Record an answer. Checks who answers, then writes as superuser:
        an employee has no write access to offers."""
        self.ensure_one()
        if self.state != "offered":
            raise UserError(_("This offer is not waiting for an answer."))
        if channel != "system":
            role = self.offer_id.assignment_id._check_is_employee_or_manager(self.employee_id)
            channel = "self" if role == "self" else "manager"
        if channel != "system" and self.response_deadline and \
                fields.Datetime.now() > self.response_deadline:
            raise UserError(_("The time to answer this offer has passed."))
        line = internal(self.sudo(), offer_internal=True)
        offer = line.offer_id
        agreement = offer.pool_id.agreement_id
        member = line.member_id
        hours = offer.assignment_id.planned_hours
        line.write({
            "state": answer,
            "responded_at": fields.Datetime.now(),
            "response_channel": channel,
            "recorded_by": self.env.user.id if channel != "system" else False,
            "refusal_reason": reason or False,
        })
        offer._close_activities(line.employee_id)
        if answer == "accepted":
            if not offer.assignment_id.is_open:
                raise UserError(_("Someone else already took this shift."))
            internal(offer.assignment_id, consent="given",
                     reason=_("Offer accepted from the call list")).write(
                {"employee_id": line.employee_id.id})
            if member:
                member.write({"hours_offered": member.hours_offered + hours,
                              "hours_accepted": member.hours_accepted + hours})
            offer.line_ids.filtered(lambda l: l.state == "waiting").write({"state": "withdrawn"})
            offer.write({"state": "filled", "filled_by": line.employee_id.id})
            post(offer, body=_("%(name)s accepted the shift.", name=line.employee_id.name))
            return
        if member:
            vals = {}
            if agreement.refusal_counts_as_offered:
                vals["hours_offered"] = member.hours_offered + hours
            if answer == "refused":
                vals["refusal_count"] = member.refusal_count + 1
            if vals:
                member.write(vals)
            if answer == "refused" and agreement.max_refusals and \
                    member.refusal_count >= agreement.max_refusals and member.active:
                member._withdraw("refusals")
        post(offer, body=_("%(name)s: %(answer)s.", name=line.employee_id.name,
                                  answer=dict(line._fields["state"]._description_selection(
                                      self.env))[answer]))
        offer._offer_next()
