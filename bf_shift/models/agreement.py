from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..lib import engine

WEEK_START = [
    ("0", "Monday"), ("1", "Tuesday"), ("2", "Wednesday"), ("3", "Thursday"),
    ("4", "Friday"), ("5", "Saturday"), ("6", "Sunday"),
]

OFFER_METHODS = [
    ("seniority", "Seniority (most senior first)"),
    ("rotation", "Rotation (fewest hours offered first)"),
    ("inverse_seniority", "Inverse seniority (least senior first)"),
]


class BfShiftAgreement(models.Model):
    _name = "bf.shift.agreement"
    _description = "Working conditions (labour standards or collective agreement)"
    _inherit = ["mail.thread"]
    _order = "sequence, name"

    name = fields.Char(required=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    kind = fields.Selection(
        [("lnt", "Labour standards only"), ("collective", "Collective agreement")],
        required=True, default="lnt", tracking=True)
    union_name = fields.Char(string="Union")
    date_from = fields.Date(string="In force from")
    date_to = fields.Date(string="In force until")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    notes = fields.Html()

    # Hours and overtime
    week_start = fields.Selection(WEEK_START, required=True, default="6",
                                  string="Week starts on")
    ot_weekly_threshold = fields.Float("Weekly overtime threshold (h)", default=40.0,
                                       tracking=True)
    ot_daily_threshold = fields.Float(
        "Daily overtime threshold (h)", default=0.0,
        help="0 means none: the labour standards only count by the week.")
    ot_multiplier = fields.Float("Overtime rate", default=1.5, tracking=True)
    dt_weekly_threshold = fields.Float(
        "Double time beyond (h per week)", default=0.0, help="0 means none.")
    dt_multiplier = fields.Float("Double time rate", default=2.0)
    dt_seventh_day = fields.Boolean(
        "Seventh day at double time",
        help="Every hour worked on the seventh day worked in the same week is paid at the double time rate.")
    dt_weekday_ids = fields.Many2many(
        "bf.shift.weekday", "bf_shift_agreement_dt_weekday_rel", string="Overtime at double time on",
        help="Overtime worked on these days is paid at the double time rate (e.g. Sunday).")
    bank_allowed = fields.Boolean(
        "Time bank", help="The employee may ask to bank overtime instead of being paid. "
        "The labour standards let the employee ask; the employer cannot impose it.")
    bank_multiplier = fields.Float("Bank credit rate", default=1.5)
    bank_cap_hours = fields.Float("Bank cap (h)", default=0.0, help="0 means no cap.")

    # Scheduling rules
    callback_min_hours = fields.Float("Minimum call-back (h)", default=3.0, tracking=True)
    notice_days = fields.Float("Schedule notice (days)", default=5.0, tracking=True)
    max_extra_daily = fields.Float("Refusal beyond the usual day (+h)", default=2.0)
    max_24h = fields.Float("Refusal beyond (h per 24 h)", default=14.0)
    max_24h_flexible = fields.Float("Refusal beyond, flexible hours (h per 24 h)", default=12.0)
    max_weekly = fields.Float("Refusal beyond (h per week)", default=50.0)
    weekly_rest_hours = fields.Float("Weekly rest (consecutive h)", default=32.0)
    meal_after_hours = fields.Float("Meal break after (h)", default=5.0)
    meal_minutes = fields.Float("Meal break (min)", default=30.0)
    min_rest_between = fields.Float("Rest between shifts (h)", default=0.0,
                                    help="0 means none: the labour standards set none.")
    holiday_indemnity = fields.Boolean("Holiday indemnity (1/20)", default=True)

    # Premiums
    premium_rule_ids = fields.One2many("bf.shift.premium.rule", "agreement_id",
                                       string="Premiums")

    # Offering open shifts
    offer_method = fields.Selection(OFFER_METHODS, default="seniority", required=True,
                                    string="Order of offers")
    offer_response_minutes = fields.Integer("Time to answer an offer (min)", default=60)
    refusal_counts_as_offered = fields.Boolean(
        "A refusal counts as hours offered", default=True,
        help="Equity rule: the hours refused are added to the person's counter, "
        "as if worked, so the rotation moves on.")
    max_refusals = fields.Integer(
        "Removed from the list after (refusals)", default=0, help="0 means never.")
    swap_requires_approval = fields.Boolean("Swaps need approval", default=True)

    # Taxable benefits
    qc_meal_reasonable = fields.Monetary(
        "Reasonable overtime meal, Québec", currency_field="currency_id", default=0.0,
        help="Above this amount, Revenu Québec treats the meal as taxable. 0 means no limit is set.")
    cra_meal_limit = fields.Monetary(
        "Overtime meal limit, CRA", currency_field="currency_id", default=23.0)

    employee_count = fields.Integer(compute="_compute_employee_count")
    floor_warning = fields.Html(compute="_compute_floor_warning", sanitize=False)

    def _compute_employee_count(self):
        # Counted as superuser: a shift manager is not necessarily an HR
        # officer, and hr.employee refuses its private fields to the others.
        groups = self.env["hr.employee"].sudo()._read_group(
            [("shift_agreement_id", "in", self.ids)], ["shift_agreement_id"], ["__count"])
        counts = {agreement.id: count for agreement, count in groups}
        for rec in self:
            rec.employee_count = counts.get(rec.id, 0)

    def _params(self):
        self.ensure_one()
        return engine.Params(
            week_start=int(self.week_start or 6),
            ot_weekly_threshold=self.ot_weekly_threshold,
            ot_daily_threshold=self.ot_daily_threshold,
            ot_multiplier=self.ot_multiplier,
            dt_weekly_threshold=self.dt_weekly_threshold,
            dt_multiplier=self.dt_multiplier,
            dt_seventh_day=self.dt_seventh_day,
            dt_weekdays=frozenset(self.dt_weekday_ids.mapped("code")),
            bank_multiplier=self.bank_multiplier,
            callback_min_hours=self.callback_min_hours,
            notice_days=self.notice_days,
            max_extra_daily=self.max_extra_daily,
            max_24h=self.max_24h,
            max_24h_flexible=self.max_24h_flexible,
            max_weekly=self.max_weekly,
            weekly_rest_hours=self.weekly_rest_hours,
            meal_after_hours=self.meal_after_hours,
            meal_minutes=self.meal_minutes,
            min_rest_between=self.min_rest_between,
            holiday_indemnity=self.holiday_indemnity,
        )

    def _effective_params(self):
        """Parameters actually applied: the agreement, raised to the floor."""
        if not self:
            return engine.LNT
        return engine.most_favourable(self._params())[0]

    def _engine_rules(self, day_from=None, day_to=None):
        self.ensure_one()
        return [rule._engine_rule() for rule in self.premium_rule_ids if rule.active]

    @api.depends(*engine._FAVOURS.keys(), "holiday_indemnity")
    def _compute_floor_warning(self):
        for rec in self:
            _eff, below = engine.most_favourable(rec._params())
            if not below:
                rec.floor_warning = False
                continue
            items = Markup("").join(
                Markup("<li>%s</li>") % escape(rec._fields[name].get_description(self.env)["string"])
                for name in below)
            rec.floor_warning = Markup(
                "<p>%s</p><ul>%s</ul>") % (
                _("These values are less favourable than the labour standards. "
                  "The labour standards apply instead:"), items)

    @api.constrains("ot_weekly_threshold", "ot_multiplier", "notice_days", "callback_min_hours",
                    "max_weekly", "weekly_rest_hours", "offer_response_minutes", "max_refusals")
    def _check_positive(self):
        for rec in self:
            for name in ("ot_weekly_threshold", "ot_multiplier", "notice_days",
                         "callback_min_hours", "max_weekly", "weekly_rest_hours",
                         "offer_response_minutes", "max_refusals"):
                if rec[name] < 0:
                    raise ValidationError(_("A rule value cannot be negative."))

    def action_view_employees(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Employees"),
            "res_model": "hr.employee",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("shift_agreement_id", "=", self.id)],
            "context": {"default_shift_agreement_id": self.id},
        }


class BfShiftPremiumRule(models.Model):
    _name = "bf.shift.premium.rule"
    _description = "Shift premium"
    _order = "agreement_id, sequence, id"

    agreement_id = fields.Many2one("bf.shift.agreement", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    name = fields.Char(required=True, translate=True)
    code = fields.Char("Payroll code", required=True)
    applies_on = fields.Selection(
        [("window", "Hours of the day"), ("weekdays", "Whole days"),
         ("holidays", "Public holidays")],
        required=True, default="window")
    hour_from = fields.Float("From", default=16.0)
    hour_to = fields.Float("To", default=24.0,
                           help="An end before the start wraps past midnight (23:00 to 07:00).")
    weekday_ids = fields.Many2many("bf.shift.weekday", "bf_shift_premium_weekday_rel",
                                   string="Days", help="Empty means every day.")
    method = fields.Selection([("percent", "Percentage of the hourly rate"),
                               ("amount", "Amount per hour")],
                              required=True, default="percent")
    percent = fields.Float("Percentage")
    amount = fields.Monetary("Amount per hour", currency_field="currency_id")
    floor = fields.Monetary("Minimum per hour", currency_field="currency_id")
    enhanced_percent = fields.Float(
        "Enhanced percentage", help="Applied instead when the employee works enough hours in the block.")
    enhanced_threshold = fields.Float("Enhanced from (h in the block)")
    enhanced_period_days = fields.Integer("Block length (days)", default=14)
    majority = fields.Boolean(
        "Majority rule", help="The whole shift earns the premium when most of its hours fall "
        "in the window, and none of it otherwise.")
    date_from = fields.Date("In force from")
    date_to = fields.Date("In force until")
    currency_id = fields.Many2one(related="agreement_id.currency_id")

    @api.constrains("hour_from", "hour_to", "percent", "amount", "floor")
    def _check_values(self):
        for rule in self:
            if not (0 <= rule.hour_from <= 24 and 0 <= rule.hour_to <= 24):
                raise ValidationError(_("Hours go from 0 to 24."))
            if rule.percent < 0 or rule.amount < 0 or rule.floor < 0:
                raise ValidationError(_("A premium cannot be negative."))

    @api.onchange("code")
    def _onchange_code(self):
        if self.code:
            self.code = self.code.strip().upper()

    def _engine_rule(self):
        self.ensure_one()
        return engine.PremiumRule(
            code=(self.code or "").strip().upper(),
            label=self.name,
            applies_on=self.applies_on,
            hour_from=self.hour_from,
            hour_to=self.hour_to,
            weekdays=frozenset(self.weekday_ids.mapped("code")),
            method=self.method,
            percent=self.percent,
            amount=self.amount,
            floor=self.floor,
            enhanced_percent=self.enhanced_percent,
            enhanced_threshold=self.enhanced_threshold,
            enhanced_period_days=self.enhanced_period_days or 14,
            majority=self.majority,
            date_from=self.date_from,
            date_to=self.date_to,
            key=self.id,
        )
