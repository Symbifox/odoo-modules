from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from . import engine

#: The levels of the second cycle of secondary school (Education Regulation s. 23.1). The units that ship
#: with the subjects are those of the first cycle (s. 23): they are not these courses' units.
SECOND_CYCLE_CODES = frozenset({"S3", "S4", "S5"})
#: The fourth and fifth years: the programs whose units count towards the diploma (s. 32). For a program with an
#: exam the Minister imposes there, the Minister counts 50 % of the school's evaluation (s. 34, second paragraph);
#: elsewhere the exam is worth 20 % of the final result (s. 30.3).
EXAM_AT_HALF_CODES = frozenset({"S4", "S5"})


class SchoolCourse(models.Model):
    """A subject taught to a group for a school year: where the evaluations hang."""

    _name = "bf.school.course"
    _description = "Course (a subject taught to a group)"
    _inherit = ["mail.thread"]
    _order = "year_id desc, group_id, subject_id"

    group_id = fields.Many2one("bf.school.group", required=True, ondelete="cascade", index=True, tracking=True)
    school_id = fields.Many2one(related="group_id.school_id", store=True, index=True)
    year_id = fields.Many2one(related="group_id.year_id", store=True, index=True)
    company_id = fields.Many2one(related="group_id.company_id", store=True)
    subject_id = fields.Many2one("bf.school.subject", required=True, ondelete="restrict", tracking=True)
    result_mode = fields.Selection(related="subject_id.result_mode")
    teacher_ids = fields.Many2many(
        "res.users", "bf_school_course_teacher_rel", "course_id", "user_id", string="Teachers",
        domain=[("share", "=", False)],
        help="Who gives the grades of this course. Taken from the group when it has only one teacher.")
    ministry_course_code = fields.Char(
        "Ministry course code", size=10, tracking=True,
        help="The course code (code de cours) the Ministry uses in Charlemagne, up to 10 characters.")
    units = fields.Integer(
        compute="_compute_units", store=True, readonly=False,
        help="Units earned when the final result reaches the threshold (secondary school). Copied from the subject, "
             "except for a group of the second cycle: the subjects ship with the units of the first cycle (s. 23), "
             "so the school enters those of its second-cycle courses.")
    units_to_enter = fields.Boolean(
        "Units to enter", compute="_compute_units_to_enter", store=True,
        help="A secondary course of the second cycle whose units are not entered yet.")
    component_ids = fields.One2many("bf.school.course.component", "course_id", "Components")
    weights_total = fields.Float("Weights total (%)", compute="_compute_weights_total", store=True)
    weights_ok = fields.Boolean(compute="_compute_weights_total", store=True)
    ministerial_exam = fields.Boolean(
        "Ministerial exam", tracking=True,
        help="The course ends with an exam the Minister imposes: its result is worth a share "
             "of the final result (Education Regulation s. 30.3).")
    exam_weight = fields.Float(
        "Exam share (%)", compute="_compute_exam_weight", store=True, readonly=False,
        help="Education Regulation s. 30.3: the result on a ministerial exam is worth 20 % of the final result, "
             "subject to s. 34, under which the Minister counts 50 % of the school's evaluation for the programs of "
             "the fourth and fifth years of secondary school. Proposed from the levels of the group; a group whose "
             "levels do not say which applies keeps 20 %: check it, the share stays editable.")
    exam_weight_hint = fields.Char("Exam share check", compute="_compute_exam_weight_hint")
    third_term_basis = fields.Selection(
        [("term", "The third term's own evaluations"),
         ("year", "All the year's evaluations")],
        string="Third term result rests on", default="term", required=True,
        help="Under the regulation the result of the third term is a summary of the whole "
             "program (s. 30.1). Whether it is computed from the evaluations of that term "
             "alone or from the whole year's is the school's choice.")
    assessment_ids = fields.One2many("bf.school.assessment", "course_id", "Evaluations")
    exam_result_ids = fields.One2many("bf.school.exam.result", "course_id", "Exam results")
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("group_subject_unique", "UNIQUE(group_id, subject_id)", "A subject is taught once to a group."),
        ("exam_weight_range", "CHECK(exam_weight >= 0 AND exam_weight <= 100)", "A share is a percentage."),
        ("units_positive", "CHECK(units >= 0)", "Units cannot be negative."),
    ]

    @api.depends_context("lang")
    @api.depends("group_id.name", "subject_id.name")
    def _compute_display_name(self):
        for course in self:
            course.display_name = "%s : %s" % (course.group_id.name or "", course.subject_id.name or "")

    def _level_codes(self):
        self.ensure_one()
        return set(self.group_id.sudo().level_ids.mapped("code"))

    def _is_second_cycle(self):
        """Some student of the group is in the second cycle of secondary school (s. 23.1)."""
        return bool(self._level_codes() & SECOND_CYCLE_CODES)

    @api.depends("subject_id", "group_id.level_ids")
    def _compute_units(self):
        for course in self:
            # The table of units of the second cycle (s. 23.1) is not shipped: the school enters them.
            course.units = 0 if course._is_second_cycle() else course.subject_id.units

    @api.depends("units", "subject_id.stage", "group_id.level_ids")
    def _compute_units_to_enter(self):
        for course in self:
            course.units_to_enter = bool(
                course.subject_id.stage == "secondary" and course._is_second_cycle() and not course.units)

    def _regulation_exam_weight(self):
        """The share of a ministerial exam that the levels of the group say: 50 % for the fourth and fifth years
        (s. 34), 20 % otherwise (s. 30.3). A group of several cycles keeps 20 % and the screen says to check."""
        self.ensure_one()
        codes = self._level_codes()
        return 50.0 if codes and codes <= EXAM_AT_HALF_CODES else 20.0

    @api.depends("group_id.level_ids")
    def _compute_exam_weight(self):
        for course in self:
            course.exam_weight = course._regulation_exam_weight()

    @api.depends("ministerial_exam", "exam_weight", "group_id.level_ids")
    def _compute_exam_weight_hint(self):
        for course in self:
            hint = False
            if course.ministerial_exam:
                codes = course._level_codes()
                said = course._regulation_exam_weight()
                if codes and codes & EXAM_AT_HALF_CODES and not codes <= EXAM_AT_HALF_CODES:
                    hint = _(
                        "This group has students of the fourth or fifth year and of other years: the exam is worth "
                        "50 % in the first (s. 34) and 20 % in the others (s. 30.3). Check the share.")
                elif not codes:
                    hint = _(
                        "The group has no level, so the share cannot be said: 20 % (s. 30.3), or 50 % for the "
                        "fourth and fifth years of secondary school (s. 34). Check the share.")
                elif course.exam_weight != said:
                    hint = _(
                        "The regulation gives %(said)s %% for the levels of this group (s. 30.3 and 34). "
                        "This course uses %(used)s %%.", said=int(said), used=course.exam_weight)
            course.exam_weight_hint = hint

    @api.depends("component_ids.weight", "result_mode")
    def _compute_weights_total(self):
        for course in self:
            total = sum(course.component_ids.mapped("weight"))
            course.weights_total = total
            course.weights_ok = (
                course.result_mode == "discipline" or abs(total - 100.0) <= 0.001)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "teacher_ids" not in vals and vals.get("group_id"):
                # A group of one teacher (elementary): that teacher. A secondary group lists all its
                # teachers: copying them all would let each grade every subject of the group.
                group = self.env["bf.school.group"].browse(vals["group_id"])
                if len(group.teacher_ids) == 1:
                    vals["teacher_ids"] = [(6, 0, group.teacher_ids.ids)]
        courses = super().create(vals_list)
        for course in courses:
            if not course.component_ids:
                course.component_ids = [(0, 0, {
                    "component_id": component.id, "weight": component.default_weight})
                    for component in course.subject_id.component_ids]
        return courses

    def _students(self, active_only=True):
        """The students of the course: those enrolled in its group."""
        self.ensure_one()
        enrollments = self.group_id.enrollment_ids
        if active_only:
            enrollments = enrollments.filtered(lambda e: e.state == "active")
        return enrollments.student_id

    def _success_threshold(self):
        return self.year_id.success_threshold or engine.SUCCESS_THRESHOLD

    # --- Final results ---------------------------------------------------------

    def _compute_finals(self):
        """Final results of every student who has results in this course.

        Returns {student_id: {...}}: the final result of each component (20/20/60 over the
        terms), the school's own final discipline result, the ministerial exam result when
        there is one, the final result, whether it passes, the units earned and the group's
        final average. Pure reading: nothing is written.
        """
        self.ensure_one()
        terms = self.year_id.term_ids.sorted("sequence")
        results = self.env["bf.school.course.result"].search([("course_id", "=", self.id)])
        exams = {e.student_id.id: e for e in self.exam_result_ids}
        weights = {c.id: c.weight for c in self.component_ids}
        threshold = self._success_threshold()
        finals = {}
        for student in results.student_id:
            by_term = {r.term_id.id: r for r in results.filtered(lambda r, s=student: r.student_id == s)}
            components = {}
            if self.result_mode != "discipline":
                for component in self.component_ids:
                    stages = []
                    for term in terms:
                        result = by_term.get(term.id)
                        line = result.line_ids.filtered(lambda l, c=component: l.component_id == c) if result else None
                        stages.append((line.percent if line else None, term.weight))
                    components[component.id] = engine.final_result(stages)
                school_final = engine.discipline_result(
                    [(components[cid], weights[cid]) for cid in components])
            else:
                school_final = engine.final_result([
                    (by_term[t.id].percent if t.id in by_term and by_term[t.id].has_percent else None, t.weight)
                    for t in terms])
            exam = exams.get(student.id)
            exam_percent = exam.percent if exam and exam.state == "graded" else None
            # A course with a ministerial exam has no final result before the Ministry's: until then the
            # figure is the school's alone, said to be provisional, and the student neither passes nor earns units.
            provisional = bool(self.ministerial_exam and exam_percent is None)
            final = (engine.with_exam(school_final, exam_percent, self.exam_weight)
                     if self.ministerial_exam else school_final)
            passed = None if provisional else engine.passed(final, threshold)
            finals[student.id] = {
                "components": components, "school_final": school_final, "exam": exam_percent,
                "final": final, "provisional": provisional, "passed": passed,
                "units": self.units if passed else 0,
            }
        # The group's average is that of the group as it is: students who left are not in it.
        active = set(self._students().ids)
        average = engine.group_average([f["final"] for sid, f in finals.items() if sid in active])
        for data in finals.values():
            data["group_average"] = average
        return finals

    def _fifth_ranks(self):
        """Rank in fifths of the students of the group as it is, from the school's results (RANG_CINQM)."""
        self.ensure_one()
        active = set(self._students().ids)
        return engine.fifth_ranks({sid: f["school_final"] for sid, f in self._compute_finals().items() if sid in active})

    def _refresh_all_results(self):
        """Recompute every term of the course: a weight or a rule that feeds them changed."""
        pairs = [(course, term) for course in self for term in course.year_id.term_ids]
        self.env["bf.school.course.result"].sudo()._refresh_pairs(pairs)

    def write(self, vals):
        result = super().write(vals)
        if "third_term_basis" in vals:
            self._refresh_all_results()
        return result


class SchoolCourseComponent(models.Model):
    """The weight of one competency or component in a course's discipline result."""

    _name = "bf.school.course.component"
    _description = "Course component"
    _order = "course_id, sequence, id"

    course_id = fields.Many2one("bf.school.course", required=True, ondelete="cascade", index=True)
    component_id = fields.Many2one("bf.school.subject.component", required=True, ondelete="restrict")
    name = fields.Char(related="component_id.name")
    kind = fields.Selection(related="component_id.kind")
    sequence = fields.Integer(related="component_id.sequence", store=True)
    weight = fields.Float("Weight (%)", help="From the evaluation framework of the subject.")

    _sql_constraints = [
        ("course_component_unique", "UNIQUE(course_id, component_id)", "A component is listed once per course."),
        ("weight_range", "CHECK(weight >= 0 AND weight <= 100)", "A weight is a percentage."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.course_id._refresh_all_results()
        return lines

    def write(self, vals):
        result = super().write(vals)
        if "weight" in vals:
            self.course_id._refresh_all_results()
        return result

    def unlink(self):
        courses = self.course_id
        result = super().unlink()
        courses._refresh_all_results()
        return result

    @api.constrains("component_id", "course_id")
    def _check_component_subject(self):
        for line in self:
            if line.component_id.subject_id != line.course_id.subject_id:
                raise ValidationError(_("%s is not a component of the subject of this course.", line.component_id.name))
