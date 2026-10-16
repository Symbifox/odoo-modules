from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from . import engine

#: The states that say the Ministry's answer is in: the office enters them, a teacher does not.
RECEIVED_STATES = ("graded", "absent")


class SchoolExamResult(models.Model):
    """A student's result on the ministerial exam of a course.

    Two figures with two readers: `percent` is what the report card uses (the exam is worth
    a share of the final result, Education Regulation s. 30.3); the raw scores are what the
    school transmits to the Ministry through Charlemagne.
    """

    _name = "bf.school.exam.result"
    _description = "Ministerial exam result"
    _order = "course_id, student_id"
    _rec_name = "student_id"

    course_id = fields.Many2one(
        "bf.school.course", required=True, ondelete="cascade", index=True,
        domain=[("ministerial_exam", "=", True)])
    company_id = fields.Many2one(related="course_id.company_id", store=True)
    student_id = fields.Many2one(
        "res.partner", "Student", required=True, ondelete="restrict", index=True,
        domain=[("is_student", "=", True)])
    state = fields.Selection(
        [("pending", "To take"), ("taken", "Taken"), ("graded", "Result received"), ("absent", "Absent")],
        default="pending", required=True,
        help="Taken: the student sat the exam and the school entered what it marks itself. Result received: the "
             "Ministry's result is in and counts for its share of the final result.")
    exam_year = fields.Integer("Exam year", help="Calendar year of the exam sitting.")
    exam_month = fields.Integer("Exam month", help="Month of the exam sitting, 1 to 12 (the Ministry's session).")
    percent = fields.Integer(
        "Result (%)", help="The student's result on the exam, as a percentage, once the Ministry's result is "
                           "received: the one the report card uses. Meaningless before that.")
    objective_score = fields.Integer("Objective part")
    development_score = fields.Integer("Development part")
    short_development_score = fields.Integer("Short development part")

    _sql_constraints = [
        ("course_student_unique", "UNIQUE(course_id, student_id)", "One exam result per student and course."),
        ("percent_range", "CHECK(percent >= 0 AND percent <= 100)", "A result is a percentage."),
        ("month_range", "CHECK(exam_month BETWEEN 0 AND 12)", "The month is 1 to 12."),
    ]

    def _assert_sitting_open(self, courses):
        """The result of an exam is the last thing a term holds: when the third term is closed only the office changes it."""
        if self.env.su or self.env.user.has_group("bf_school_core.group_school_manager"):
            return
        for course in courses:
            third = course.year_id.term_ids.filtered(lambda t: t.sequence == 3)
            third._assert_open_for()

    def _is_office(self):
        return self.env.su or self.env.user.has_group("bf_school_core.group_school_manager")

    def _refuse_ministry_result(self):
        raise AccessError(_(
            "The Ministry's result is entered by the school administration: a teacher marks the exam as taken "
            "and enters the raw scores."))

    @api.model_create_multi
    def create(self, vals_list):
        self._assert_sitting_open(self.env["bf.school.course"].browse([v["course_id"] for v in vals_list if v.get("course_id")]))
        if not self._is_office():
            # 🔴 `state` and `percent` are what the Ministry answered: the teacher of the course does not enter them.
            # An empty value forges nothing: the web client sends the screen's defaults as they are.
            for vals in vals_list:
                if vals.get("state") in RECEIVED_STATES or vals.get("percent"):
                    self._refuse_ministry_result()
        if not self.env.su:
            # A result received with no figure would be 0 %: the figure is entered with it.
            for vals in vals_list:
                if vals.get("state") == "graded" and "percent" not in vals:
                    raise UserError(_("Enter the percentage the Ministry gave with the state “Result received”."))
        return super().create(vals_list)

    def write(self, vals):
        # 🔴 A result stays with its student and its course: the teacher's rule reads the course it started in.
        if not self.env.su and {"course_id", "student_id", "company_id"} & set(vals):
            raise UserError(_("An exam result stays with its student and its course."))
        self._assert_sitting_open(self.course_id)
        if not self._is_office():
            if vals.get("state") in RECEIVED_STATES:
                self._refuse_ministry_result()
            for exam in self:
                if exam.state in RECEIVED_STATES and (
                        ("state" in vals and vals["state"] != exam.state)
                        or ("percent" in vals and vals["percent"] != exam.percent)):
                    self._refuse_ministry_result()
                if "percent" in vals and vals["percent"] != exam.percent:
                    self._refuse_ministry_result()
        if not self.env.su and vals.get("state") == "graded" and "percent" not in vals:
            if self.filtered(lambda e: e.state != "graded" and not e.percent):
                raise UserError(_("Enter the percentage the Ministry gave with the state “Result received”."))
        return super().write(vals)

    def unlink(self):
        self._assert_sitting_open(self.course_id)
        return super().unlink()

    @api.constrains("course_id", "student_id")
    def _check_course(self):
        for exam in self:
            if not exam.course_id.ministerial_exam:
                raise ValidationError(_("%s has no ministerial exam.", exam.course_id.display_name))
            if exam.student_id not in exam.course_id.group_id.enrollment_ids.student_id:
                raise ValidationError(_("%s is not in the group of this course.", exam.student_id.name))

    @api.constrains("state", "exam_year", "exam_month")
    def _check_sitting(self):
        for exam in self.filtered(lambda e: e.state in ("taken", "graded")):
            if not exam.exam_year or not exam.exam_month:
                raise ValidationError(_("Enter the year and the month of the sitting of a taken exam."))
