from collections import defaultdict

from odoo import api, fields, models
from odoo.osv import expression

from . import engine


class SchoolCourseResult(models.Model):
    """A student's result in a course for one term, computed from the evaluations.

    Written by the system only (`_refresh_pairs`): the figures of a report card come from the
    teachers' grades through the engine, never typed twice.
    """

    _name = "bf.school.course.result"
    _description = "Course result for a term"
    _order = "course_id, term_id, student_id"
    _rec_name = "student_id"

    course_id = fields.Many2one("bf.school.course", required=True, ondelete="cascade", index=True)
    term_id = fields.Many2one("bf.school.term", required=True, ondelete="cascade", index=True)
    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="restrict", index=True)
    school_id = fields.Many2one(related="course_id.school_id", store=True)
    company_id = fields.Many2one(related="course_id.company_id", store=True)
    has_percent = fields.Boolean(
        "Has a result", help="False when the evaluations cannot give a discipline result yet "
                             "(for example the component weights are not entered).")
    percent = fields.Integer("Result (%)")
    below_threshold = fields.Boolean(
        "Below the threshold", compute="_compute_below_threshold", search="_search_below_threshold",
        help="A result under the success threshold of the course's school year (60 % unless the year says otherwise).")
    # Never stored: a student who leaves changes it without any grade being written, and the report card seals it.
    group_average = fields.Integer("Group average (%)", compute="_compute_group_average")
    line_ids = fields.One2many("bf.school.course.result.line", "result_id", "Components")

    _sql_constraints = [
        ("course_term_student_unique", "UNIQUE(course_id, term_id, student_id)",
         "One result per student, course and term."),
    ]

    @api.depends("percent", "has_percent", "course_id.year_id.success_threshold")
    def _compute_below_threshold(self):
        for result in self:
            result.below_threshold = bool(
                result.has_percent and result.percent < result.course_id._success_threshold())

    def _search_below_threshold(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise NotImplementedError(operator)
        # One clause per school year the user can read: each year has its own threshold.
        below = [[("course_id.year_id", "=", year.id), ("has_percent", "=", True),
                  ("percent", "<", year.success_threshold or engine.SUCCESS_THRESHOLD)]
                 for year in self.env["bf.school.year"].search([])]
        domain = expression.OR(below) if below else expression.FALSE_DOMAIN
        return domain if (operator == "=") == value else ["!", *domain]

    @api.depends("percent", "has_percent", "course_id.group_id.enrollment_ids.state")
    def _compute_group_average(self):
        """The average of the group as it is now: a student who left is not in it."""
        averages = {}
        for result in self:
            key = (result.course_id.id, result.term_id.id)
            if key not in averages:
                siblings = self.sudo().search([("course_id", "=", key[0]), ("term_id", "=", key[1])])
                active = set(result.course_id.sudo()._students().ids)
                averages[key] = engine.group_average(
                    [r.percent for r in siblings if r.has_percent and r.student_id.id in active]) or 0
            result.group_average = averages[key]

    @api.model
    def _refresh_pairs(self, pairs):
        """Recompute the results of the (course, term) pairs touched by a change.

        A course whose third term rests on the whole year also refreshes that third term
        when an earlier term changes.
        """
        todo = set()
        for course, term in pairs:
            if not course.exists() or not term.exists():
                continue
            todo.add((course.id, term.id))
            if course.third_term_basis == "year":
                third = course.year_id.term_ids.filtered(lambda t: t.sequence == 3)
                if third:
                    todo.add((course.id, third.id))
        for course_id, term_id in sorted(todo):
            self._refresh_course_term(
                self.env["bf.school.course"].browse(course_id), self.env["bf.school.term"].browse(term_id))

    @api.model
    def _refresh_course_term(self, course, term):
        terms = term
        if course.third_term_basis == "year" and term.sequence == 3:
            terms = course.year_id.term_ids
        grades = self.env["bf.school.assessment.result"].search([
            ("course_id", "=", course.id), ("term_id", "in", terms.ids), ("status", "=", "graded")])
        evaluations = defaultdict(lambda: defaultdict(list))
        for grade in grades:
            assessment = grade.assessment_id
            evaluations[grade.student_id.id][assessment.component_id.id or False].append(
                (grade.score, assessment.max_score, assessment.weight))
        weights = {c.id: c.weight for c in course.component_ids}
        existing = {r.student_id.id: r for r in self.search([
            ("course_id", "=", course.id), ("term_id", "=", term.id)])}
        computed = {}
        for student_id, by_component in evaluations.items():
            lines = {cid: engine.component_result(items) for cid, items in by_component.items()
                     if cid is not False}
            if course.result_mode == "discipline":
                percent = engine.component_result(by_component.get(False, []))
                lines = {}
            else:
                percent = engine.discipline_result([(r, weights.get(cid, 0.0)) for cid, r in lines.items()])
            computed[student_id] = (lines, percent)
        for student_id, record in list(existing.items()):
            if student_id not in computed:
                record.unlink()
                existing.pop(student_id)
        for student_id, (lines, percent) in computed.items():
            vals = {"has_percent": percent is not None, "percent": percent or 0}
            record = existing.get(student_id)
            if record:
                record.write(vals)
            else:
                record = self.create(dict(vals, course_id=course.id, term_id=term.id, student_id=student_id))
                existing[student_id] = record
            wanted = {cid: r for cid, r in lines.items() if r is not None}
            for line in record.line_ids:
                if line.component_id.id not in wanted:
                    line.unlink()
                else:
                    line.percent = wanted.pop(line.component_id.id)
            for cid, r in wanted.items():
                self.env["bf.school.course.result.line"].create(
                    {"result_id": record.id, "component_id": cid, "percent": r})


class SchoolCourseResultLine(models.Model):
    """The result of one competency or component inside a course result."""

    _name = "bf.school.course.result.line"
    _description = "Component result"
    _order = "result_id, component_id"

    result_id = fields.Many2one("bf.school.course.result", required=True, ondelete="cascade", index=True)
    component_id = fields.Many2one("bf.school.course.component", required=True, ondelete="cascade")
    name = fields.Char(related="component_id.name")
    percent = fields.Integer("Result (%)")

    _sql_constraints = [
        ("result_component_unique", "UNIQUE(result_id, component_id)", "One result per component."),
    ]
