import contextvars
from datetime import date, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

#: Education Regulation (I-13.3, r. 8), s. 29 and 29.1, as read in the consolidated text of
#: 2026-05-01: (month, day, year offset from the start of the school year).
FIRST_COMMUNICATION = (10, 15, 0)
REPORT_CARD_DEADLINES = {1: (11, 20, 0), 2: (3, 15, 1), 3: (7, 10, 1)}
#: Weighting of the final result by stage (s. 30.2); data, the school may not change it but a
#: new regulation may.
DEFAULT_WEIGHTS = {1: 20.0, 2: 20.0, 3: 60.0}

#: The years whose terms are being saved together: their weights move in one save (20/20/60 to 30/20/50) and are
#: checked once, at the end, by the year. Not a context key: a client cannot switch the check off.
_WEIGHTS_SAVED_TOGETHER = contextvars.ContextVar("bf_school_term_weights_saved_together", default=frozenset())


class SchoolYear(models.Model):
    _inherit = "bf.school.year"

    term_ids = fields.One2many("bf.school.term", "year_id", "Terms")
    first_communication_date = fields.Date(
        "First written communication", compute="_compute_first_communication_date",
        store=True, readonly=False,
        help="Written communication other than a report card, sent to the parents at the "
             "latest on October 15 (Education Regulation s. 29).")
    success_threshold = fields.Float(
        "Success threshold (%)", default=60.0,
        help="Education Regulation s. 28.1: the success threshold is 60 % for every subject.")
    deadline_override_reason = fields.Char(
        "Deadline exception",
        help="The government order that moves a legal deadline for this year, with its reference. "
             "Required to set a date later than the regulation's.")

    _sql_constraints = [
        ("success_threshold_range", "CHECK(success_threshold >= 0 AND success_threshold <= 100)",
         "The success threshold is a percentage."),
    ]

    @api.depends("date_start")
    def _compute_first_communication_date(self):
        for year in self:
            if year.date_start:
                month, day, offset = FIRST_COMMUNICATION
                year.first_communication_date = date(year.date_start.year + offset, month, day)
            else:
                year.first_communication_date = False

    def _legal_deadline(self, sequence):
        """The latest date the regulation allows for the report card of this stage."""
        self.ensure_one()
        month, day, offset = REPORT_CARD_DEADLINES[sequence]
        return date(self.date_start.year + offset, month, day)

    def _legal_first_communication(self):
        self.ensure_one()
        month, day, offset = FIRST_COMMUNICATION
        return date(self.date_start.year + offset, month, day)

    @api.constrains("first_communication_date", "deadline_override_reason", "date_start")
    def _check_first_communication(self):
        for year in self.filtered("first_communication_date"):
            if (year.first_communication_date > year._legal_first_communication()
                    and not year.deadline_override_reason):
                raise ValidationError(_(
                    "The first written communication is due on %(legal)s at the latest "
                    "(Education Regulation s. 29). To set %(date)s, fill in the deadline "
                    "exception with the order that allows it.",
                    legal=year._legal_first_communication(), date=year.first_communication_date))

    @api.constrains("term_ids")
    def _check_term_weights(self):
        for year in self.filtered("term_ids"):
            total = sum(year.term_ids.mapped("weight"))
            if len(year.term_ids) == 3 and abs(total - 100.0) > 0.001:
                raise ValidationError(_(
                    "The weights of the three terms of %(year)s total %(total)s %%, not 100 %%.",
                    year=year.name, total=total))

    def write(self, vals):
        if "term_ids" not in vals:
            return super().write(vals)
        # The terms of a year are saved together: their weights are checked once, by the year, when all are written.
        token = _WEIGHTS_SAVED_TOGETHER.set(_WEIGHTS_SAVED_TOGETHER.get() | set(self.ids))
        try:
            return super().write(vals)
        finally:
            _WEIGHTS_SAVED_TOGETHER.reset(token)

    def action_create_terms(self):
        """The three terms of the regulation, with its dates and its 20/20/60 weighting."""
        for year in self.filtered(lambda y: not y.term_ids):
            starts = year.date_start
            for sequence in (1, 2, 3):
                deadline = year._legal_deadline(sequence)
                end = year.date_end if sequence == 3 else deadline - timedelta(days=14)
                self.env["bf.school.term"].create({
                    "year_id": year.id, "sequence": sequence,
                    "date_start": starts, "date_end": max(end, starts),
                    "report_deadline": deadline, "weight": DEFAULT_WEIGHTS[sequence]})
                starts = end + timedelta(days=1)
        return True


class SchoolTerm(models.Model):
    """A term (étape): the stage of the school year a report card closes.

    Three terms under the regulation. Dates and weights are data: the government can move a
    deadline by order and a school fixes its own dates under the legal ceilings.
    """

    _name = "bf.school.term"
    _description = "School term"
    _order = "year_id desc, sequence"

    year_id = fields.Many2one("bf.school.year", "School year", required=True, ondelete="cascade", index=True)
    school_id = fields.Many2one(related="year_id.school_id", store=True, index=True)
    company_id = fields.Many2one(related="year_id.company_id", store=True)
    sequence = fields.Integer("Term number", required=True, default=1)
    date_start = fields.Date("Starts", required=True)
    date_end = fields.Date("Ends", required=True)
    report_deadline = fields.Date(
        "Report card deadline", required=True,
        help="The date by which the report card of this term reaches the families. The "
             "regulation sets November 20, March 15 and July 10 at the latest.")
    legal_deadline = fields.Date("Regulation deadline", compute="_compute_legal_deadline")
    weight = fields.Float(
        "Weight in the final result (%)", required=True, default=20.0,
        help="Education Regulation s. 30.2: 20 % for the first term, 20 % for the second, "
             "60 % for the third.")
    state = fields.Selection(
        [("open", "Open"), ("closed", "Closed")], default="open", required=True,
        help="A closed term no longer takes grades from the teachers; the office can reopen it.")

    _sql_constraints = [
        ("sequence_year_unique", "UNIQUE(year_id, sequence)",
         "A school year has one term of each number."),
        ("sequence_range", "CHECK(sequence BETWEEN 1 AND 3)",
         "The regulation has three terms: the number is 1, 2 or 3."),
        ("weight_range", "CHECK(weight >= 0 AND weight <= 100)",
         "The weight of a term is a percentage."),
    ]

    @api.depends("year_id.date_start", "sequence")
    def _compute_legal_deadline(self):
        for term in self:
            term.legal_deadline = (
                term.year_id._legal_deadline(term.sequence)
                if term.year_id.date_start and term.sequence in REPORT_CARD_DEADLINES else False)

    @api.depends_context("lang")
    @api.depends("sequence", "year_id.name")
    def _compute_display_name(self):
        for term in self:
            term.display_name = _("Term %(number)s, %(year)s", number=term.sequence,
                                  year=term.year_id.name or "")

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for term in self:
            if term.date_end < term.date_start:
                raise ValidationError(_("A term ends after it starts."))

    @api.constrains("weight", "year_id")
    def _check_year_weights(self):
        """The weight of a term moves with the others: 20/20/60 is the regulation's (s. 30.2), the total stays 100 %."""
        saved_together = _WEIGHTS_SAVED_TOGETHER.get()
        self.year_id.filtered(lambda year: year.id not in saved_together)._check_term_weights()

    @api.constrains("report_deadline", "year_id", "sequence")
    def _check_deadline(self):
        for term in self:
            legal = term.legal_deadline
            if legal and term.report_deadline > legal and not term.year_id.deadline_override_reason:
                raise ValidationError(_(
                    "The report card of term %(number)s is due on %(legal)s at the latest "
                    "(Education Regulation s. 29.1). To set %(date)s, fill in the deadline "
                    "exception of the school year with the order that allows it.",
                    number=term.sequence, legal=legal, date=term.report_deadline))

    def _assert_open_for(self, user=None):
        """Grades of a closed term are the office's to change."""
        user = user or self.env.user
        if user.has_group("bf_school_core.group_school_manager"):
            return
        closed = self.filtered(lambda t: t.state == "closed")
        if closed:
            raise AccessError(_(
                "%s is closed: ask the school office to reopen it before changing grades.",
                closed[0].display_name))

    def action_close(self):
        self._check_manager()
        self.write({"state": "closed"})
        return True

    def action_reopen(self):
        self._check_manager()
        self.write({"state": "open"})
        return True

    def _check_manager(self):
        if not self.env.user.has_group("bf_school_core.group_school_manager"):
            raise AccessError(_("Only the school administration closes or reopens a term."))

    def write(self, vals):
        # 🔴 `readonly` is no guard against RPC: the state of a term moves by its buttons only.
        if "state" in vals:
            self._check_manager()
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get("state") == "closed" for vals in vals_list):
            self._check_manager()
        return super().create(vals_list)
