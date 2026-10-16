from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from . import engine


class SchoolAssessment(models.Model):
    """One evaluation of a course: a test, an essay, a practical work."""

    _name = "bf.school.assessment"
    _description = "Evaluation"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"

    name = fields.Char(required=True, tracking=True)
    course_id = fields.Many2one("bf.school.course", required=True, ondelete="cascade", index=True)
    school_id = fields.Many2one(related="course_id.school_id", store=True)
    year_id = fields.Many2one(related="course_id.year_id", store=True)
    company_id = fields.Many2one(related="course_id.company_id", store=True)
    term_id = fields.Many2one(
        "bf.school.term", "Term", required=True, ondelete="restrict", index=True,
        domain="[('year_id', '=', year_id)]")
    component_id = fields.Many2one(
        "bf.school.course.component", "Competency or component", ondelete="restrict",
        domain="[('course_id', '=', course_id)]",
        help="What this evaluation judges. Required in the subjects that have a result by "
             "competency or by component.")
    result_mode = fields.Selection(related="course_id.result_mode", string="Result mode")
    date = fields.Date(required=True, default=fields.Date.context_today)
    max_score = fields.Float("Out of", required=True, default=100.0)
    weight = fields.Float(
        "Weight", required=True, default=1.0,
        help="Relative importance of this evaluation among those that judge the same "
             "competency or component in the same term.")
    result_ids = fields.One2many("bf.school.assessment.result", "assessment_id", "Results")
    graded_count = fields.Integer("Graded results", compute="_compute_stats")
    average = fields.Float("Average (%)", compute="_compute_stats")

    _sql_constraints = [
        ("max_positive", "CHECK(max_score > 0)", "An evaluation is marked out of a positive number."),
        ("weight_positive", "CHECK(weight > 0)", "The weight of an evaluation is positive."),
    ]

    @api.depends("result_ids.status", "result_ids.score", "max_score")
    def _compute_stats(self):
        for assessment in self:
            graded = assessment.result_ids.filtered(lambda r: r.status == "graded")
            assessment.graded_count = len(graded)
            assessment.average = (
                sum(engine.score_percent(r.score, assessment.max_score) for r in graded) / len(graded)
                if graded else 0.0)

    @api.constrains("max_score")
    def _check_max_score_over_the_grades(self):
        """A maximum lowered under a grade already entered would give a result above 100 %."""
        for assessment in self:
            over = assessment.sudo().result_ids.filtered(
                lambda r: r.status == "graded" and r.score > assessment.max_score)
            if over:
                raise ValidationError(_(
                    "%(score)s is already entered for %(student)s: the evaluation cannot be marked out of "
                    "less than that (%(max)s).",
                    score=over[0].score, student=over[0].student_id.name, max=assessment.max_score))

    @api.constrains("course_id", "term_id", "component_id")
    def _check_links(self):
        for assessment in self:
            if assessment.term_id.year_id != assessment.course_id.year_id:
                raise ValidationError(_("The term belongs to another school year than the course."))
            if assessment.component_id and assessment.component_id.course_id != assessment.course_id:
                raise ValidationError(_("The component is not one of this course's."))
            if assessment.course_id.result_mode != "discipline" and not assessment.component_id:
                raise ValidationError(_(
                    "Choose the competency or component this evaluation judges: %s has results "
                    "by competency or component.", assessment.course_id.subject_id.name))
            if assessment.course_id.result_mode == "discipline" and assessment.component_id:
                raise ValidationError(_(
                    "%s has a single discipline result: an evaluation judges no component.",
                    assessment.course_id.subject_id.name))

    @api.model_create_multi
    def create(self, vals_list):
        terms = self.env["bf.school.term"].browse([v["term_id"] for v in vals_list if v.get("term_id")])
        terms._assert_open_for()
        assessments = super().create(vals_list)
        Result = self.env["bf.school.assessment.result"].sudo()
        for assessment in assessments:
            have = assessment.result_ids.student_id
            Result.create([
                {"assessment_id": assessment.id, "student_id": student.id}
                for student in assessment.course_id._students() - have])
        assessments._refresh_results()
        return assessments

    def write(self, vals):
        # 🔴 An evaluation stays in its course: the teacher's record rule reads the course it
        # started in, so moving it would hand it to a colleague's course.
        if "course_id" in vals and any(a.course_id.id != vals["course_id"] for a in self):
            raise UserError(_("An evaluation cannot be moved to another course."))
        # 🔴 `school_id`, `year_id` and `company_id` are related to the course and writing them writes
        # the course's (and its group's): they are not an evaluation's to change.
        if {"school_id", "year_id", "company_id"} & set(vals) and not self.env.su:
            raise UserError(_("An evaluation stays in its course, its year and its school."))
        self.term_id._assert_open_for()
        if vals.get("term_id"):
            self.env["bf.school.term"].browse(vals["term_id"])._assert_open_for()
        before = [(a.course_id, a.term_id) for a in self]
        result = super().write(vals)
        if {"term_id", "component_id", "weight", "max_score"} & set(vals):
            self._refresh_results(extra=before)
        return result

    def unlink(self):
        self.term_id._assert_open_for()
        pairs = [(a.course_id, a.term_id) for a in self]
        result = super().unlink()
        self.env["bf.school.course.result"].sudo()._refresh_pairs(pairs)
        return result

    def _refresh_results(self, extra=()):
        pairs = [(a.course_id, a.term_id) for a in self] + list(extra)
        self.env["bf.school.course.result"].sudo()._refresh_pairs(pairs)


class SchoolAssessmentResult(models.Model):
    """A student's grade on one evaluation."""

    _name = "bf.school.assessment.result"
    _description = "Grade on an evaluation"
    _order = "assessment_id, student_id"
    _rec_name = "student_id"

    assessment_id = fields.Many2one("bf.school.assessment", required=True, ondelete="cascade", index=True)
    course_id = fields.Many2one(related="assessment_id.course_id", store=True, index=True)
    term_id = fields.Many2one(related="assessment_id.term_id", store=True, index=True)
    company_id = fields.Many2one(related="assessment_id.company_id", store=True)
    student_id = fields.Many2one(
        "res.partner", "Student", required=True, ondelete="restrict", index=True,
        domain=[("is_student", "=", True)])
    status = fields.Selection(
        [("pending", "Not graded"), ("graded", "Graded"), ("exempt", "Exempt")],
        default="pending", required=True,
        help="Only a graded evaluation counts. Exempt: the student does not take part and "
             "nothing is held against them.")
    score = fields.Float()
    percent = fields.Float("Percent", compute="_compute_percent")
    comment = fields.Char()

    _sql_constraints = [
        ("assessment_student_unique", "UNIQUE(assessment_id, student_id)",
         "A student has one grade per evaluation."),
    ]

    @api.depends("score", "status", "assessment_id.max_score")
    def _compute_percent(self):
        for result in self:
            result.percent = (
                engine.score_percent(result.score, result.assessment_id.max_score)
                if result.status == "graded" else 0.0)

    @api.constrains("score", "status", "assessment_id")
    def _check_score(self):
        for result in self.filtered(lambda r: r.status == "graded"):
            if result.score < 0 or result.score > result.assessment_id.max_score:
                raise ValidationError(_(
                    "%(score)s is not between 0 and %(max)s, the maximum of %(name)s.",
                    score=result.score, max=result.assessment_id.max_score,
                    name=result.assessment_id.name))

    @api.constrains("student_id", "assessment_id")
    def _check_student_in_group(self):
        for result in self:
            enrolled = result.assessment_id.course_id.group_id.enrollment_ids.student_id
            if result.student_id not in enrolled:
                raise ValidationError(_(
                    "%(student)s is not in the group of this course.", student=result.student_id.name))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "score" in vals and not vals.get("status"):
                vals["status"] = "graded"
        assessments = self.env["bf.school.assessment"].browse(
            [v["assessment_id"] for v in vals_list if v.get("assessment_id")])
        assessments.term_id._assert_open_for()
        results = super().create(vals_list)
        results._refresh()
        return results

    def write(self, vals):
        # 🔴 A grade stays on its student and its evaluation: moving it is a way to hide or
        # forge one, and the record rule only looks at the course it started in.
        # 🔴 `course_id`, `term_id` and `company_id` are related to the evaluation: writing them by RPC writes the
        # evaluation's own (a grade dragged into a colleague's course or a closed term).
        if {"course_id", "term_id", "company_id"} & set(vals) and not self.env.su:
            raise UserError(_("A grade stays with its evaluation: its course and its term are the evaluation's."))
        if {"assessment_id", "student_id"} & set(vals):
            for result in self:
                if ("assessment_id" in vals and vals["assessment_id"] != result.assessment_id.id) or \
                        ("student_id" in vals and vals["student_id"] != result.student_id.id):
                    raise UserError(_("A grade cannot be moved to another evaluation or student."))
        self.term_id._assert_open_for()
        if "score" in vals and "status" not in vals:
            # Typing a score in a "not graded" line grades it.
            pending = self.filtered(lambda r: r.status == "pending")
            rest = self - pending
            if pending:
                super(SchoolAssessmentResult, pending).write(dict(vals, status="graded"))
            if rest:
                super(SchoolAssessmentResult, rest).write(vals)
        else:
            super().write(vals)
        if {"score", "status"} & set(vals):
            self._refresh()
        return True

    def unlink(self):
        self.term_id._assert_open_for()
        pairs = [(r.course_id, r.term_id) for r in self]
        result = super().unlink()
        self.env["bf.school.course.result"].sudo()._refresh_pairs(pairs)
        return result

    def _refresh(self):
        self.env["bf.school.course.result"].sudo()._refresh_pairs(
            [(r.course_id, r.term_id) for r in self])
