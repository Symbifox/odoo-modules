from datetime import date

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger


class GradebookCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.school = env["bf.school"].create({"name": "École des Essais"})
        cls.year = env["bf.school.year"].create({
            "name": "2026-2027", "school_id": cls.school.id,
            "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        cls.year.action_set_current()
        cls.year.action_create_terms()
        cls.t1, cls.t2, cls.t3 = cls.year.term_ids.sorted("sequence")
        cls.teacher = new_test_user(env, login="gb_teacher", groups="bf_school_core.group_school_user")
        cls.colleague = new_test_user(env, login="gb_colleague", groups="bf_school_core.group_school_user")
        cls.office = new_test_user(env, login="gb_office", groups="bf_school_core.group_school_manager")
        cls.group = env["bf.school.group"].create({
            "name": "301", "school_id": cls.school.id, "year_id": cls.year.id,
            "teacher_ids": [(6, 0, cls.teacher.ids)]})
        cls.other_group = env["bf.school.group"].create({
            "name": "302", "school_id": cls.school.id, "year_id": cls.year.id,
            "teacher_ids": [(6, 0, cls.colleague.ids)]})
        Partner = env["res.partner"]
        cls.alpha, cls.bravo, cls.charlie = (
            Partner.create({"name": n, "is_student": True}) for n in ("Alpha Essai", "Bravo Essai", "Charlie Essai"))
        cls.stranger = Partner.create({"name": "Delta Essai", "is_student": True})
        for student in (cls.alpha, cls.bravo, cls.charlie):
            env["bf.school.enrollment"].create({"student_id": student.id, "group_id": cls.group.id})
        env["bf.school.enrollment"].create({"student_id": cls.stranger.id, "group_id": cls.other_group.id})
        cls.pe = env.ref("bf_school_gradebook.subject_el_eps")
        cls.math = env.ref("bf_school_gradebook.subject_el_mat")
        cls.science = env.ref("bf_school_gradebook.subject_el_sct")
        cls.pe_course = env["bf.school.course"].create({"group_id": cls.group.id, "subject_id": cls.pe.id})
        cls.math_course = env["bf.school.course"].create({"group_id": cls.group.id, "subject_id": cls.math.id})
        cls.other_course = env["bf.school.course"].create({"group_id": cls.other_group.id, "subject_id": cls.pe.id})

    def _set_math_weights(self, solve=30.0, reason=70.0):
        lines = self.math_course.component_ids.sorted("sequence")
        lines[0].weight, lines[1].weight = solve, reason
        return lines

    def _assess(self, course, term, name="Test", max_score=100.0, weight=1.0, component=None, user=None):
        Assessment = self.env["bf.school.assessment"]
        if user:
            Assessment = Assessment.with_user(user)
        return Assessment.create({
            "name": name, "course_id": course.id, "term_id": term.id, "max_score": max_score,
            "weight": weight, "component_id": component.id if component else False})

    def _grade(self, assessment, student, score, **extra):
        grade = assessment.result_ids.filtered(lambda r: r.student_id == student)
        grade.write(dict(extra, score=score))
        return grade

    def _result(self, course, term, student):
        return self.env["bf.school.course.result"].search([
            ("course_id", "=", course.id), ("term_id", "=", term.id), ("student_id", "=", student.id)])


@tagged("post_install", "-at_install")
class TestTerms(GradebookCase):

    def test_the_three_terms_follow_the_regulation(self):
        self.assertEqual(self.year.term_ids.mapped("sequence"), [1, 2, 3])
        self.assertEqual(self.t1.report_deadline, date(2026, 11, 20))
        self.assertEqual(self.t2.report_deadline, date(2027, 3, 15))
        self.assertEqual(self.t3.report_deadline, date(2027, 7, 10))
        self.assertEqual([self.t1.weight, self.t2.weight, self.t3.weight], [20.0, 20.0, 60.0])
        self.assertEqual(self.year.first_communication_date, date(2026, 10, 15))
        self.assertEqual(self.t1.date_start, self.year.date_start)
        self.assertEqual(self.t3.date_end, self.year.date_end)
        self.assertLess(self.t1.date_end, self.t2.date_start)
        self.assertLess(self.t2.date_end, self.t3.date_start)

    def test_creating_the_terms_twice_creates_nothing_more(self):
        self.year.action_create_terms()
        self.assertEqual(len(self.year.term_ids), 3)

    def test_a_deadline_after_the_regulation_needs_the_order_that_allows_it(self):
        with self.assertRaises(ValidationError):
            self.t2.report_deadline = date(2027, 3, 28)
        self.year.deadline_override_reason = "D. 318-2024, a. 5"
        self.t2.report_deadline = date(2027, 3, 28)
        self.assertEqual(self.t2.report_deadline, date(2027, 3, 28))

    def test_an_earlier_deadline_is_always_allowed(self):
        self.t1.report_deadline = date(2026, 11, 10)
        self.assertEqual(self.t1.report_deadline, date(2026, 11, 10))

    def test_the_first_communication_is_capped_at_october_15(self):
        with self.assertRaises(ValidationError):
            self.year.first_communication_date = date(2026, 10, 30)
        self.year.first_communication_date = date(2026, 10, 1)

    def test_the_weights_of_the_three_terms_total_100(self):
        with self.assertRaises(ValidationError):
            self.year.write({"term_ids": [(1, self.t1.id, {"weight": 30.0})]})
        self.year.write({"term_ids": [(1, self.t1.id, {"weight": 30.0}), (1, self.t3.id, {"weight": 50.0})]})
        self.assertEqual([self.t1.weight, self.t3.weight], [30.0, 50.0], "a new decree can move them, in one save")

    def test_a_term_number_is_one_to_three(self):
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"), self.cr.savepoint():
            self.env["bf.school.term"].create({
                "year_id": self.year.id, "sequence": 4, "date_start": date(2027, 6, 1),
                "date_end": date(2027, 6, 2), "report_deadline": date(2027, 6, 2), "weight": 0})

    def test_only_the_office_closes_a_term_and_not_by_rpc_either(self):
        with self.assertRaises(AccessError):
            self.t1.with_user(self.teacher).action_close()
        with self.assertRaises(AccessError):
            self.t1.with_user(self.teacher).sudo(False).write({"state": "closed"})
        self.t1.with_user(self.office).action_close()
        self.assertEqual(self.t1.state, "closed")
        with self.assertRaises(AccessError):
            self.env["bf.school.term"].with_user(self.teacher).create({
                "year_id": self.year.id, "sequence": 9, "date_start": date(2027, 1, 1),
                "date_end": date(2027, 1, 2), "report_deadline": date(2027, 1, 2), "state": "closed"})


@tagged("post_install", "-at_install")
class TestCourse(GradebookCase):

    def test_a_course_takes_the_group_teachers_and_the_subject_components(self):
        self.assertEqual(self.pe_course.teacher_ids, self.teacher)
        self.assertEqual(self.math_course.component_ids.mapped("name"),
                         ["Solve a situational problem", "Use mathematical reasoning"])
        self.assertEqual(self.math_course.component_ids.mapped("weight"), [0.0, 0.0],
                         "the evaluation framework's weights are the school's to enter")

    def test_weights_must_total_100_for_a_subject_with_components(self):
        self.assertFalse(self.math_course.weights_ok)
        self._set_math_weights(30.0, 70.0)
        self.assertTrue(self.math_course.weights_ok)
        self._set_math_weights(30.0, 60.0)
        self.assertFalse(self.math_course.weights_ok)
        self.assertTrue(self.pe_course.weights_ok, "a discipline result has no weights to enter")

    def test_a_subject_is_taught_once_to_a_group(self):
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"), self.cr.savepoint():
            self.env["bf.school.course"].create({"group_id": self.group.id, "subject_id": self.pe.id})

    def test_a_discipline_subject_takes_no_components(self):
        with self.assertRaises(ValidationError):
            self.env["bf.school.subject.component"].create(
                {"subject_id": self.pe.id, "name": "Nope", "kind": "competency"})

    def test_a_component_kind_must_fit_the_subject(self):
        with self.assertRaises(ValidationError):
            self.env["bf.school.subject.component"].create(
                {"subject_id": self.math.id, "name": "Nope", "kind": "theory"})
        self.assertEqual(self.science.component_ids.mapped("kind"), ["theory", "practice"])


@tagged("post_install", "-at_install")
class TestGrades(GradebookCase):

    def test_an_evaluation_opens_a_pending_grade_for_every_student_of_the_group(self):
        assessment = self._assess(self.pe_course, self.t1)
        self.assertEqual(assessment.result_ids.student_id, self.alpha | self.bravo | self.charlie)
        self.assertEqual(set(assessment.result_ids.mapped("status")), {"pending"})
        self.assertEqual(assessment.graded_count, 0)

    def test_typing_a_score_grades_the_line(self):
        assessment = self._assess(self.pe_course, self.t1)
        grade = self._grade(assessment, self.alpha, 80.0)
        self.assertEqual(grade.status, "graded")
        self.assertEqual(assessment.graded_count, 1)

    def test_a_score_beyond_the_maximum_is_refused(self):
        assessment = self._assess(self.pe_course, self.t1, max_score=20.0)
        with self.assertRaises(ValidationError):
            self._grade(assessment, self.alpha, 21.0)
        with self.assertRaises(ValidationError):
            self._grade(assessment, self.alpha, -1.0)

    def test_a_student_outside_the_group_gets_no_grade(self):
        assessment = self._assess(self.pe_course, self.t1)
        with self.assertRaises(ValidationError):
            self.env["bf.school.assessment.result"].create(
                {"assessment_id": assessment.id, "student_id": self.stranger.id, "score": 50.0})

    def test_a_grade_cannot_be_moved_to_another_student_or_evaluation(self):
        first, second = self._assess(self.pe_course, self.t1), self._assess(self.pe_course, self.t1, name="Two")
        grade = first.result_ids.filtered(lambda r: r.student_id == self.alpha)
        with self.assertRaises(UserError):
            grade.student_id = self.bravo
        with self.assertRaises(UserError):
            grade.assessment_id = second

    def test_an_evaluation_cannot_be_moved_to_another_course(self):
        assessment = self._assess(self.pe_course, self.t1)
        with self.assertRaises(UserError):
            assessment.course_id = self.other_course

    def test_discipline_result_is_the_weighted_mean_of_the_evaluations(self):
        quiz = self._assess(self.pe_course, self.t1, name="Quiz", max_score=20.0, weight=1.0)
        project = self._assess(self.pe_course, self.t1, name="Project", max_score=50.0, weight=3.0)
        self._grade(quiz, self.alpha, 15.0)       # 75 %
        self._grade(project, self.alpha, 40.0)    # 80 %
        result = self._result(self.pe_course, self.t1, self.alpha)
        self.assertEqual((result.has_percent, result.percent), (True, 79), "(75 + 3 x 80) / 4 = 78.75")
        self.assertFalse(self._result(self.pe_course, self.t1, self.bravo), "nothing graded: no result")

    def test_a_pending_or_exempt_grade_counts_for_nothing(self):
        quiz = self._assess(self.pe_course, self.t1, name="Quiz")
        project = self._assess(self.pe_course, self.t1, name="Project")
        self._grade(quiz, self.alpha, 70.0)
        self._grade(project, self.alpha, 0.0, status="exempt")
        self.assertEqual(self._result(self.pe_course, self.t1, self.alpha).percent, 70)

    def test_group_average_is_the_mean_of_the_students_results(self):
        quiz = self._assess(self.pe_course, self.t1)
        for student, score in ((self.alpha, 70.0), (self.bravo, 80.0), (self.charlie, 81.0)):
            self._grade(quiz, student, score)
        averages = set(self.env["bf.school.course.result"].search(
            [("course_id", "=", self.pe_course.id)]).mapped("group_average"))
        self.assertEqual(averages, {77})

    def test_changing_a_grade_refreshes_the_result(self):
        quiz = self._assess(self.pe_course, self.t1)
        grade = self._grade(quiz, self.alpha, 50.0)
        self.assertEqual(self._result(self.pe_course, self.t1, self.alpha).percent, 50)
        grade.score = 90.0
        self.assertEqual(self._result(self.pe_course, self.t1, self.alpha).percent, 90)
        grade.status = "pending"
        self.assertFalse(self._result(self.pe_course, self.t1, self.alpha))
        grade.status = "graded"
        quiz.unlink()
        self.assertFalse(self._result(self.pe_course, self.t1, self.alpha))

    def test_competency_subject_needs_a_competency_on_every_evaluation(self):
        with self.assertRaises(ValidationError):
            self._assess(self.math_course, self.t1)
        with self.assertRaises(ValidationError):
            self._assess(self.pe_course, self.t1, component=self.math_course.component_ids[0])

    def test_competency_results_feed_the_discipline_result_by_the_framework_weights(self):
        solve, reason = self._set_math_weights(30.0, 70.0)
        a = self._assess(self.math_course, self.t1, name="Problem", component=solve)
        b = self._assess(self.math_course, self.t1, name="Reasoning", component=reason)
        self._grade(a, self.alpha, 60.0)
        self._grade(b, self.alpha, 80.0)
        result = self._result(self.math_course, self.t1, self.alpha)
        self.assertEqual(result.percent, 74, "0.3 x 60 + 0.7 x 80 = 74")
        self.assertEqual({line.name: line.percent for line in result.line_ids},
                         {"Solve a situational problem": 60, "Use mathematical reasoning": 80})

    def test_a_term_shows_only_the_components_that_were_evaluated(self):
        solve, reason = self._set_math_weights(30.0, 70.0)
        a = self._assess(self.math_course, self.t1, component=solve)
        self._grade(a, self.alpha, 60.0)
        result = self._result(self.math_course, self.t1, self.alpha)
        self.assertEqual(result.line_ids.mapped("name"), ["Solve a situational problem"])
        self.assertEqual(result.percent, 60, "the evaluated component alone, re-weighted")

    def test_weights_not_entered_give_no_discipline_result_rather_than_a_guess(self):
        solve, reason = self.math_course.component_ids.sorted("sequence")
        a = self._assess(self.math_course, self.t1, component=solve)
        self._grade(a, self.alpha, 60.0)
        result = self._result(self.math_course, self.t1, self.alpha)
        self.assertFalse(result.has_percent)
        self.assertEqual(len(result.line_ids), 1, "the component result is still shown")

    def test_third_term_on_the_whole_year_counts_earlier_evaluations(self):
        self.pe_course.third_term_basis = "year"
        first = self._assess(self.pe_course, self.t1, name="Fall")
        third = self._assess(self.pe_course, self.t3, name="Spring")
        self._grade(first, self.alpha, 60.0)
        self._grade(third, self.alpha, 80.0)
        self.assertEqual(self._result(self.pe_course, self.t3, self.alpha).percent, 70)
        self._grade(first, self.alpha, 100.0)
        self.assertEqual(self._result(self.pe_course, self.t3, self.alpha).percent, 90,
                         "changing a first-term grade refreshes the third term")
        self.assertEqual(self._result(self.pe_course, self.t1, self.alpha).percent, 100)

    def test_third_term_on_its_own_evaluations_ignores_earlier_ones(self):
        first = self._assess(self.pe_course, self.t1, name="Fall")
        third = self._assess(self.pe_course, self.t3, name="Spring")
        self._grade(first, self.alpha, 60.0)
        self._grade(third, self.alpha, 80.0)
        self.assertEqual(self._result(self.pe_course, self.t3, self.alpha).percent, 80)

    def test_a_closed_term_takes_no_grade_from_a_teacher(self):
        quiz = self._assess(self.pe_course, self.t1)
        self.t1.with_user(self.office).action_close()
        grade = quiz.result_ids[0]
        with self.assertRaises(AccessError):
            grade.with_user(self.teacher).score = 50.0
        with self.assertRaises(AccessError):
            self._assess(self.pe_course, self.t1, name="Late", user=self.teacher)
        grade.with_user(self.office).score = 50.0
        self.assertEqual(grade.score, 50.0, "the office can still correct")
        self.t1.with_user(self.office).action_reopen()
        grade.with_user(self.teacher).score = 55.0
        self.assertEqual(grade.score, 55.0)

    def test_a_closed_term_takes_no_evaluation_from_a_teacher_either(self):
        # A group nobody is enrolled in has no grade line to refuse a late evaluation: the evaluation's own guard does.
        empty_group = self.env["bf.school.group"].create({
            "name": "303", "school_id": self.school.id, "year_id": self.year.id, "teacher_ids": [(6, 0, self.teacher.ids)]})
        empty_course = self.env["bf.school.course"].create({"group_id": empty_group.id, "subject_id": self.pe.id})
        quiz = self._assess(self.pe_course, self.t1)
        self.t1.with_user(self.office).action_close()
        with self.assertRaises(AccessError):
            self._assess(empty_course, self.t1, name="Late", user=self.teacher)
        with self.assertRaises(AccessError):
            quiz.with_user(self.teacher).write({"name": "Renamed"})
        with self.assertRaises(AccessError):
            quiz.with_user(self.teacher).unlink()
        quiz.with_user(self.office).write({"name": "Renamed by the office"})
        self.assertEqual(quiz.name, "Renamed by the office")

    def test_a_closed_term_takes_no_new_grade_line_from_a_teacher(self):
        # A student who joined late has no grade line yet: the teacher cannot add one once the term is closed.
        quiz = self._assess(self.pe_course, self.t1)
        late = quiz.result_ids.filtered(lambda r: r.student_id == self.charlie)
        late.with_user(self.office).unlink()
        self.t1.with_user(self.office).action_close()
        values = {"assessment_id": quiz.id, "student_id": self.charlie.id, "score": 80.0}
        with self.assertRaises(AccessError):
            self.env["bf.school.assessment.result"].with_user(self.teacher).create(values)
        made = self.env["bf.school.assessment.result"].with_user(self.office).create(values)
        self.assertEqual(made.score, 80.0)


@tagged("post_install", "-at_install")
class TestFinals(GradebookCase):

    def _grade_course(self, student, scores):
        """scores: (term, score) for the physical-education course, one evaluation per term."""
        for term, score in scores:
            assessment = self.pe_course.assessment_ids.filtered(lambda a, t=term: a.term_id == t)[:1]
            if not assessment:
                assessment = self._assess(self.pe_course, term, name="E%s" % term.sequence)
            self._grade(assessment, student, score)

    def test_final_result_is_20_20_60_over_the_terms(self):
        self._grade_course(self.alpha, [(self.t1, 70.0), (self.t2, 80.0), (self.t3, 90.0)])
        final = self.pe_course._compute_finals()[self.alpha.id]
        self.assertEqual(final["school_final"], 84, "0.2 x 70 + 0.2 x 80 + 0.6 x 90")
        self.assertEqual(final["final"], 84)
        self.assertTrue(final["passed"])

    def test_a_term_without_result_is_left_out_and_the_others_re_weighted(self):
        self._grade_course(self.alpha, [(self.t1, 70.0), (self.t3, 80.0)])
        self.assertEqual(self.pe_course._compute_finals()[self.alpha.id]["final"], 78, "(1400 + 4800) / 80")

    def test_competency_finals_come_from_the_component_finals(self):
        solve, reason = self._set_math_weights(30.0, 70.0)
        for term, s, r in ((self.t1, 60.0, 80.0), (self.t2, 70.0, 80.0), (self.t3, 80.0, 90.0)):
            a = self._assess(self.math_course, term, name="P%s" % term.sequence, component=solve)
            b = self._assess(self.math_course, term, name="R%s" % term.sequence, component=reason)
            self._grade(a, self.alpha, s)
            self._grade(b, self.alpha, r)
        final = self.math_course._compute_finals()[self.alpha.id]
        self.assertEqual(final["components"][solve.id], 74, "0.2 x 60 + 0.2 x 70 + 0.6 x 80")
        self.assertEqual(final["components"][reason.id], 86, "0.2 x 80 + 0.2 x 80 + 0.6 x 90")
        self.assertEqual(final["school_final"], 82, "0.3 x 74 + 0.7 x 86 = 82.4")

    def test_the_ministerial_exam_is_worth_20_percent_of_the_final_result(self):
        self.pe_course.ministerial_exam = True
        self._grade_course(self.alpha, [(self.t1, 70.0), (self.t2, 70.0), (self.t3, 70.0)])
        self.env["bf.school.exam.result"].create({
            "course_id": self.pe_course.id, "student_id": self.alpha.id, "state": "taken",
            "exam_year": 2027, "exam_month": 6, "percent": 50})
        self.assertTrue(self.pe_course._compute_finals()[self.alpha.id]["provisional"], "taken: the Ministry's result is not in")
        self.env["bf.school.exam.result"].search([("student_id", "=", self.alpha.id)]).state = "graded"
        final = self.pe_course._compute_finals()[self.alpha.id]
        self.assertEqual((final["school_final"], final["exam"], final["final"], final["provisional"]), (70, 50, 66, False))

    def test_an_exam_not_taken_leaves_the_school_result(self):
        self.pe_course.ministerial_exam = True
        self._grade_course(self.alpha, [(self.t3, 70.0)])
        self.env["bf.school.exam.result"].create({
            "course_id": self.pe_course.id, "student_id": self.alpha.id, "state": "pending"})
        final = self.pe_course._compute_finals()[self.alpha.id]
        self.assertEqual(final["final"], 70)
        self.assertEqual((final["provisional"], final["passed"], final["units"]), (True, None, 0),
                         "no final result before the Ministry's: neither passed nor units")

    def test_threshold_and_units(self):
        self.pe_course.units = 4
        self._grade_course(self.alpha, [(self.t3, 60.0)])
        self._grade_course(self.bravo, [(self.t3, 59.0)])
        finals = self.pe_course._compute_finals()
        self.assertEqual((finals[self.alpha.id]["passed"], finals[self.alpha.id]["units"]), (True, 4))
        self.assertEqual((finals[self.bravo.id]["passed"], finals[self.bravo.id]["units"]), (False, 0))
        self.assertEqual(finals[self.alpha.id]["group_average"], 60, "(60 + 59) / 2 = 59.5, half up")

    def test_the_threshold_is_data(self):
        self.year.success_threshold = 50.0
        self._grade_course(self.bravo, [(self.t3, 55.0)])
        self.assertTrue(self.pe_course._compute_finals()[self.bravo.id]["passed"])

    def test_fifth_ranks_come_from_the_school_results_of_the_students_who_are_still_there(self):
        for student, score in ((self.alpha, 90.0), (self.bravo, 80.0), (self.charlie, 70.0)):
            self._grade_course(student, [(self.t3, score)])
        self.assertEqual(self.pe_course._fifth_ranks(), {self.alpha.id: 1, self.bravo.id: 2, self.charlie.id: 3})
        self.env["bf.school.enrollment"].search([("student_id", "=", self.alpha.id)]).action_leave()
        self.assertEqual(self.pe_course._fifth_ranks(), {self.bravo.id: 1, self.charlie.id: 2},
                         "whoever left is not ranked, nor counted in the group's average")

    def test_the_group_average_does_not_count_a_student_who_left(self):
        for student, score in ((self.alpha, 80.0), (self.bravo, 70.0), (self.charlie, 60.0)):
            self._grade_course(student, [(self.t3, score)])
        self.env["bf.school.enrollment"].search([("student_id", "=", self.charlie.id)]).action_leave()
        finals = self.pe_course._compute_finals()
        self.assertEqual(finals[self.alpha.id]["group_average"], 75)
        self._grade_course(self.alpha, [(self.t3, 80.0)])  # the next grade written refreshes the term's average
        self.assertEqual(self._result(self.pe_course, self.t3, self.alpha).group_average, 75)

    def test_an_exam_result_needs_a_course_with_an_exam(self):
        with self.assertRaises(ValidationError):
            self.env["bf.school.exam.result"].create({
                "course_id": self.pe_course.id, "student_id": self.alpha.id})

    def test_a_taken_exam_needs_its_sitting(self):
        self.pe_course.ministerial_exam = True
        with self.assertRaises(ValidationError):
            self.env["bf.school.exam.result"].create({
                "course_id": self.pe_course.id, "student_id": self.alpha.id, "state": "taken", "percent": 50})


@tagged("post_install", "-at_install")
class TestAccess(GradebookCase):

    def test_a_teacher_grades_their_own_courses_only(self):
        mine = self._assess(self.pe_course, self.t1, user=self.teacher)
        self.assertTrue(mine)
        with self.assertRaises(AccessError):
            self._assess(self.other_course, self.t1, user=self.teacher)
        theirs = self._assess(self.other_course, self.t1, user=self.colleague)
        with self.assertRaises(AccessError):
            theirs.with_user(self.teacher).write({"name": "Mine now"})
        with self.assertRaises(AccessError):
            theirs.result_ids.with_user(self.teacher).write({"score": 100.0})

    def test_a_teacher_cannot_hand_an_evaluation_to_a_colleague_course(self):
        mine = self._assess(self.pe_course, self.t1, user=self.teacher)
        with self.assertRaises(UserError):
            mine.with_user(self.teacher).course_id = self.other_course

    def test_a_teacher_reads_only_the_courses_of_their_groups(self):
        seen = self.env["bf.school.course"].with_user(self.teacher).search([])
        mine = self.pe_course | self.math_course | self.other_course
        self.assertEqual(seen & mine, self.pe_course | self.math_course)
        self.assertEqual(self.env["bf.school.course"].with_user(self.office).search([]) & mine, mine)

    def test_a_group_teacher_reads_the_results_of_the_group_but_does_not_grade(self):
        homeroom = new_test_user(self.env, login="gb_homeroom", groups="bf_school_core.group_school_user")
        self.group.teacher_ids = [(4, homeroom.id)]
        self.pe_course.teacher_ids = [(6, 0, self.colleague.ids)]
        assessment = self._assess(self.pe_course, self.t1, user=self.colleague)
        self._grade(assessment, self.alpha, 70.0)
        results = self.env["bf.school.course.result"].with_user(homeroom).search([])
        self.assertEqual(results.student_id, self.alpha)
        with self.assertRaises(AccessError):
            assessment.result_ids.with_user(homeroom).write({"score": 99.0})

    def test_nobody_but_the_system_writes_a_computed_result(self):
        assessment = self._assess(self.pe_course, self.t1)
        self._grade(assessment, self.alpha, 70.0)
        result = self._result(self.pe_course, self.t1, self.alpha)
        with self.assertRaises(AccessError):
            result.with_user(self.teacher).write({"percent": 100})
        with self.assertRaises(AccessError):
            result.with_user(self.office).write({"percent": 100})
        with self.assertRaises(AccessError):
            self.env["bf.school.course.result"].with_user(self.office).create({
                "course_id": self.pe_course.id, "term_id": self.t1.id, "student_id": self.bravo.id})

    def test_only_the_office_sets_up_courses_subjects_and_terms(self):
        with self.assertRaises(AccessError):
            self.env["bf.school.course"].with_user(self.teacher).create(
                {"group_id": self.group.id, "subject_id": self.science.id})
        with self.assertRaises(AccessError):
            self.pe_course.with_user(self.teacher).ministerial_exam = True
        with self.assertRaises(AccessError):
            self.t1.with_user(self.teacher).weight = 99.0
        course = self.env["bf.school.course"].with_user(self.office).create(
            {"group_id": self.group.id, "subject_id": self.science.id})
        self.assertEqual(course.teacher_ids, self.teacher)


@tagged("post_install", "-at_install")
class TestEdgeCases(GradebookCase):
    """Edge cases and guards of the gradebook, each as it was found."""

    def _science_course(self):
        return self.env["bf.school.course"].create({
            "group_id": self.group.id, "subject_id": self.science.id, "teacher_ids": [(6, 0, self.colleague.ids)]})

    def test_a_grade_cannot_be_dragged_into_a_colleagues_course_by_its_related_fields(self):
        other = self._science_course()
        mine = self._assess(self.pe_course, self.t1, user=self.teacher)
        grade = mine.result_ids.filtered(lambda r: r.student_id == self.alpha).with_user(self.teacher)
        with self.assertRaises(UserError):
            grade.write({"course_id": other.id, "score": 100.0})
        with self.assertRaises(UserError):
            grade.write({"company_id": self.env.company.id})
        self.assertEqual(mine.course_id, self.pe_course)
        self.assertFalse(self._result(other, self.t1, self.alpha))

    def test_nor_into_a_closed_term(self):
        mine = self._assess(self.pe_course, self.t1, user=self.teacher)
        self.t2.with_user(self.office).action_close()
        grade = mine.result_ids.filtered(lambda r: r.student_id == self.alpha).with_user(self.teacher)
        with self.assertRaises(UserError):
            grade.write({"term_id": self.t2.id, "score": 100.0})
        self.assertEqual(mine.term_id, self.t1)

    def test_an_evaluation_keeps_its_course_year_and_school(self):
        mine = self._assess(self.pe_course, self.t1, user=self.teacher)
        for field in ("year_id", "school_id", "company_id"):
            with self.assertRaises(UserError):
                mine.with_user(self.teacher).write({field: mine[field].id})

    def _exam_course(self):
        self.pe_course.ministerial_exam = True
        return self.env["bf.school.exam.result"].create({
            "course_id": self.pe_course.id, "student_id": self.alpha.id, "state": "taken",
            "exam_year": 2027, "exam_month": 6, "percent": 60})

    def test_an_exam_result_is_for_a_student_of_the_group(self):
        self.pe_course.ministerial_exam = True
        with self.assertRaises(ValidationError):
            self.env["bf.school.exam.result"].create({
                "course_id": self.pe_course.id, "student_id": self.stranger.id, "state": "taken",
                "exam_year": 2027, "exam_month": 6})

    def test_an_exam_result_stays_with_its_student_and_course(self):
        exam = self._exam_course()
        other = self._science_course()
        other.ministerial_exam = True
        with self.assertRaises(UserError):
            exam.with_user(self.teacher).write({"course_id": other.id})
        with self.assertRaises(UserError):
            exam.with_user(self.teacher).write({"student_id": self.bravo.id})

    def test_exam_results_are_the_offices_once_the_third_term_is_closed(self):
        exam = self._exam_course()
        self.t3.with_user(self.office).action_close()
        with self.assertRaises(AccessError):
            exam.with_user(self.teacher).write({"percent": 99})
        with self.assertRaises(AccessError):
            self.env["bf.school.exam.result"].with_user(self.teacher).create({
                "course_id": self.pe_course.id, "student_id": self.bravo.id, "state": "pending"})
        with self.assertRaises(AccessError):
            exam.with_user(self.teacher).unlink()
        exam.with_user(self.office).write({"percent": 61})
        self.assertEqual(exam.percent, 61)

    def test_a_homeroom_teacher_is_the_only_one_of_a_group_by_default_and_said_for_a_larger_one(self):
        self.assertEqual(self.group.homeroom_ids, self.teacher)
        big = self.env["bf.school.group"].create({
            "name": "Sec 1", "school_id": self.school.id, "year_id": self.year.id,
            "teacher_ids": [(6, 0, (self.teacher | self.colleague).ids)]})
        self.assertFalse(big.homeroom_ids, "with several teachers, the office says who is in charge")

    def test_a_group_of_several_teachers_does_not_give_each_of_them_every_course(self):
        big = self.env["bf.school.group"].create({
            "name": "Sec 1", "school_id": self.school.id, "year_id": self.year.id,
            "teacher_ids": [(6, 0, (self.teacher | self.colleague).ids)]})
        course = self.env["bf.school.course"].create({"group_id": big.id, "subject_id": self.pe.id})
        self.assertFalse(course.teacher_ids)

    def test_entering_the_weights_after_the_grades_gives_the_results_that_were_waiting(self):
        solve, reason = self.math_course.component_ids.sorted("sequence")
        a = self._assess(self.math_course, self.t1, name="P", component=solve)
        b = self._assess(self.math_course, self.t1, name="R", component=reason)
        self._grade(a, self.alpha, 60.0)
        self._grade(b, self.alpha, 80.0)
        self.assertFalse(self._result(self.math_course, self.t1, self.alpha).has_percent)
        solve.weight, reason.weight = 30.0, 70.0
        self.assertEqual(self._result(self.math_course, self.t1, self.alpha).percent, 74)

    def test_correcting_a_weight_corrects_the_results_of_the_terms(self):
        solve, reason = self._set_math_weights(40.0, 60.0)
        a = self._assess(self.math_course, self.t1, name="P", component=solve)
        b = self._assess(self.math_course, self.t1, name="R", component=reason)
        self._grade(a, self.alpha, 80.0)
        self._grade(b, self.alpha, 60.0)
        self.assertEqual(self._result(self.math_course, self.t1, self.alpha).percent, 68)
        solve.weight, reason.weight = 50.0, 50.0
        self.assertEqual(self._result(self.math_course, self.t1, self.alpha).percent, 70)
        self.assertEqual(self.math_course._compute_finals()[self.alpha.id]["school_final"], 70,
                         "the term and the final agree")

    def test_changing_what_the_third_term_rests_on_recomputes_it(self):
        first = self._assess(self.pe_course, self.t1, name="Fall")
        third = self._assess(self.pe_course, self.t3, name="Spring")
        self._grade(first, self.alpha, 60.0)
        self._grade(third, self.alpha, 80.0)
        self.assertEqual(self._result(self.pe_course, self.t3, self.alpha).percent, 80)
        self.pe_course.third_term_basis = "year"
        self.assertEqual(self._result(self.pe_course, self.t3, self.alpha).percent, 70)


@tagged("post_install", "-at_install")
class TestIsolationAndLimits(GradebookCase):
    """Company isolation and limits of the gradebook, each as it was found."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.other_company = env["res.company"].create({"name": "Autre école inc."})
        cls.stranger_office = new_test_user(
            env, login="gb_other_office", groups="bf_school_core.group_school_manager",
            company_id=cls.other_company.id, company_ids=[(6, 0, cls.other_company.ids)])

    def _as_stranger(self, model):
        return self.env[model].with_user(self.stranger_office).with_context(
            allowed_company_ids=self.other_company.ids)

    def _second_cycle_group(self, level_codes, name="Sec"):
        levels = self.env["bf.school.level"].search([("code", "in", level_codes)])
        return self.env["bf.school.group"].create({
            "name": name, "school_id": self.school.id, "year_id": self.year.id, "level_ids": [(6, 0, levels.ids)]})

    # --- A1 / C2: the children of a result and of a course have their company's rule

    def test_the_office_of_another_company_reads_no_result_by_competency_nor_component(self):
        solve, reason = self._set_math_weights(30.0, 70.0)
        self._grade(self._assess(self.math_course, self.t1, component=solve), self.alpha, 60.0)
        self.assertTrue(self.env["bf.school.course.result.line"].with_user(self.office).search([]))
        for model in ("bf.school.course.result.line", "bf.school.course.component",
                      "bf.school.course.result", "bf.school.course"):
            self.assertFalse(self._as_stranger(model).search([]), model)
        self.assertFalse(self._as_stranger("bf.school.course.result.line").search_read([], ["percent"]))

    # --- A3: the Ministry's result is the office's to enter

    def _exam(self, **extra):
        self.pe_course.ministerial_exam = True
        values = {"course_id": self.pe_course.id, "student_id": self.alpha.id, "state": "taken",
                  "exam_year": 2027, "exam_month": 6}
        values.update(extra)
        return self.env["bf.school.exam.result"].create(values)

    def test_a_teacher_does_not_enter_the_result_the_ministry_gave(self):
        exam = self._exam()
        teacher_exam = exam.with_user(self.teacher)
        with self.assertRaises(AccessError):
            teacher_exam.write({"state": "graded", "percent": 99})
        with self.assertRaises(AccessError):
            teacher_exam.write({"state": "absent"})
        with self.assertRaises(AccessError):
            teacher_exam.write({"percent": 99})
        self.assertEqual((exam.state, exam.percent), ("taken", 0))
        # what the school marks itself, the teacher keeps
        teacher_exam.write({"objective_score": 60, "development_score": 40, "state": "taken"})
        self.assertEqual(exam.objective_score, 60)
        with self.assertRaises(AccessError):
            self.env["bf.school.exam.result"].with_user(self.teacher).create({
                "course_id": self.pe_course.id, "student_id": self.bravo.id, "state": "graded",
                "exam_year": 2027, "exam_month": 6, "percent": 80})
        # a default of the screen is not a forged value
        made = self.env["bf.school.exam.result"].with_user(self.teacher).create({
            "course_id": self.pe_course.id, "student_id": self.bravo.id, "state": "pending", "percent": 0})
        self.assertEqual(made.state, "pending")
        exam.with_user(self.office).write({"state": "graded", "percent": 71})
        self.assertEqual((exam.state, exam.percent), ("graded", 71))
        with self.assertRaises(AccessError):
            teacher_exam.write({"state": "taken"})
        with self.assertRaises(AccessError):
            teacher_exam.write({"percent": 100})

    def test_the_final_result_does_not_move_by_a_teachers_hand(self):
        exam = self._exam()
        for term in (self.t1, self.t2, self.t3):
            self._grade(self._assess(self.pe_course, term, name="E%s" % term.sequence), self.alpha, 70.0)
        self.assertTrue(self.pe_course._compute_finals()[self.alpha.id]["provisional"])
        with self.assertRaises(AccessError):
            exam.with_user(self.teacher).write({"state": "graded", "percent": 99})
        self.assertTrue(self.pe_course._compute_finals()[self.alpha.id]["provisional"])

    # --- B20: a result received with no figure would be 0 %

    def test_a_result_received_needs_its_percentage_entered_with_it(self):
        exam = self._exam()
        with self.assertRaises(UserError):
            exam.with_user(self.office).write({"state": "graded"})
        self.assertEqual(exam.state, "taken")
        with self.assertRaises(UserError):
            self.env["bf.school.exam.result"].with_user(self.office).create({
                "course_id": self.pe_course.id, "student_id": self.bravo.id, "state": "graded",
                "exam_year": 2027, "exam_month": 6})
        exam.with_user(self.office).write({"state": "graded", "percent": 71})
        self.assertEqual(exam.percent, 71)

    # --- A5: the shared subjects are the system administration's

    def test_the_office_of_another_company_does_not_change_a_shared_subject(self):
        shared = self.math
        component = shared.component_ids[0]
        units = shared.units
        self.assertFalse(shared.company_id)
        subject = self._as_stranger("bf.school.subject").browse(shared.id)
        with self.assertRaises(AccessError):
            subject.write({"units": 99})
        with self.assertRaises(AccessError):
            subject.component_ids[0].write({"default_weight": 50.0})
        with self.assertRaises(AccessError):
            self._as_stranger("bf.school.subject.component").create(
                {"subject_id": shared.id, "name": "Another", "kind": "competency"})
        with self.assertRaises(AccessError):
            subject.unlink()
        self.assertEqual((shared.units, component.default_weight), (units, 0.0))
        # nor the office of the company that uses them
        with self.assertRaises(AccessError):
            self.math.with_user(self.office).write({"units": 99})
        with self.assertRaises(AccessError):
            component.with_user(self.office).write({"default_weight": 50.0})
        # but the system administration does
        admin = new_test_user(self.env, login="gb_admin", groups="bf_school_core.group_school_manager,base.group_system")
        self.math.with_user(admin).write({"units": 99})
        self.assertEqual(shared.units, 99)

    def test_a_subject_a_school_adds_carries_its_company_and_stays_its_own(self):
        Subject = self._as_stranger("bf.school.subject")
        own = Subject.create({"name": "Chess", "code": "CHS", "stage": "secondary", "result_mode": "competency"})
        self.assertEqual(own.company_id, self.other_company)
        own.write({"units": 2})
        self.assertEqual(own.units, 2)
        with self.assertRaises(AccessError):
            own.write({"company_id": False})
        with self.assertRaises(AccessError):
            Subject.create({"name": "Fake", "code": "FKE", "stage": "secondary", "company_id": self.env.company.id})
        self.assertIn(own, Subject.search([]), "its own, and the shared ones")
        self.assertIn(self.math, Subject.search([]))
        self.assertNotIn(own, self.env["bf.school.subject"].with_user(self.office).search([]))
        component = self._as_stranger("bf.school.subject.component").create(
            {"subject_id": own.id, "name": "Opening", "kind": "competency"})
        self.assertTrue(component)
        with self.assertRaises(AccessError):
            component.with_user(self.office).write({"default_weight": 10.0})

    # --- B1

    def test_lowering_the_maximum_under_a_grade_already_entered_is_refused(self):
        assessment = self._assess(self.pe_course, self.t3, max_score=100.0)
        self._grade(assessment, self.alpha, 80.0)
        with self.assertRaises(ValidationError):
            assessment.with_user(self.teacher).write({"max_score": 10.0})
        self.assertEqual(assessment.max_score, 100.0)
        self.assertLessEqual(self._result(self.pe_course, self.t3, self.alpha).percent, 100)
        assessment.with_user(self.teacher).write({"max_score": 80.0})
        self.assertEqual(self._result(self.pe_course, self.t3, self.alpha).percent, 100)

    # --- B2

    def test_the_group_average_follows_the_students_who_are_there(self):
        quiz = self._assess(self.pe_course, self.t1, name="Q")
        for student, score in ((self.alpha, 80.0), (self.bravo, 70.0), (self.charlie, 60.0)):
            self._grade(quiz, student, score)
        result = self._result(self.pe_course, self.t1, self.alpha)
        self.assertEqual(result.group_average, 70)
        self.env["bf.school.enrollment"].search([("student_id", "=", self.charlie.id)]).action_leave()
        self.assertEqual(result.group_average, 75, "without a grade written since: the average is read, not kept")
        self.assertEqual(self._result(self.pe_course, self.t1, self.bravo).group_average, 75)
        # a grade written later is in the averages already read in the same transaction
        self._grade(quiz, self.bravo, 50.0)
        self.assertEqual(result.group_average, 65, "(80 + 50) / 2")

    # --- B3

    def test_a_term_weight_cannot_leave_the_three_terms_short_of_100(self):
        with self.assertRaises(ValidationError):
            self.t3.with_user(self.office).write({"weight": 40.0})
        self.assertEqual(self.t3.weight, 60.0)
        # the legitimate way to move them is one save of the year
        self.year.with_user(self.office).write({"term_ids": [
            (1, self.t1.id, {"weight": 30.0}), (1, self.t3.id, {"weight": 50.0})]})
        self.assertEqual([self.t1.weight, self.t3.weight], [30.0, 50.0])

    # --- B4: the exam is worth 20 % (s. 30.3), 50 % in the fourth and fifth years (s. 34)

    def test_a_course_of_the_fourth_or_fifth_year_takes_the_exam_at_half(self):
        subject = self.env.ref("bf_school_gradebook.subject_sec_his")
        for codes, expected in ((["S4"], 50.0), (["S5"], 50.0), (["S4", "S5"], 50.0), (["S1"], 20.0), (["S2"], 20.0),
                                (["S3"], 20.0), (["S3", "S4"], 20.0), ([], 20.0)):
            group = self._second_cycle_group(codes, name="G%s" % "".join(codes) or "none")
            course = self.env["bf.school.course"].create(
                {"group_id": group.id, "subject_id": subject.id, "ministerial_exam": True})
            self.assertEqual(course.exam_weight, expected, codes)

    def test_the_exam_share_stays_editable_and_the_screen_says_when_it_departs_from_the_regulation(self):
        group = self._second_cycle_group(["S4"], name="S4A")
        course = self.env["bf.school.course"].create({
            "group_id": group.id, "subject_id": self.env.ref("bf_school_gradebook.subject_sec_his").id,
            "ministerial_exam": True})
        self.assertFalse(course.exam_weight_hint)
        course.with_user(self.office).write({"exam_weight": 20.0})
        self.assertEqual(course.exam_weight, 20.0)
        self.assertIn("50", course.exam_weight_hint)
        mixed = self.env["bf.school.course"].create({
            "group_id": self._second_cycle_group(["S3", "S4"], name="S34").id,
            "subject_id": self.env.ref("bf_school_gradebook.subject_sec_his").id, "ministerial_exam": True})
        self.assertEqual(mixed.exam_weight, 20.0)
        self.assertTrue(mixed.exam_weight_hint, "a group of several cycles: the screen says to check")

    def test_the_final_result_counts_the_exam_at_the_share_of_the_course(self):
        group = self._second_cycle_group(["S4"], name="S4B")
        cara = self.env["res.partner"].create({"name": "Cara Essai", "is_student": True})
        self.env["bf.school.enrollment"].create({"student_id": cara.id, "group_id": group.id})
        course = self.env["bf.school.course"].create({
            "group_id": group.id, "subject_id": self.env.ref("bf_school_gradebook.subject_sec_his").id,
            "ministerial_exam": True})
        for term in (self.t1, self.t2, self.t3):
            assessment = self._assess(course, term, name="H%s" % term.sequence)
            self._grade(assessment, cara, 70.0)
        self.env["bf.school.exam.result"].create({
            "course_id": course.id, "student_id": cara.id, "state": "graded", "exam_year": 2027,
            "exam_month": 6, "percent": 50})
        self.assertEqual(course._compute_finals()[cara.id]["final"], 60, "half the school's 70 and half the exam's 50")

    # --- B5: the units of the first cycle are not the second cycle's

    def test_a_course_of_the_second_cycle_does_not_take_the_units_of_the_first(self):
        subject = self.env.ref("bf_school_gradebook.subject_sec_mat")
        self.assertEqual(subject.units, 12)
        first = self.env["bf.school.course"].create(
            {"group_id": self._second_cycle_group(["S1"], name="S1A").id, "subject_id": subject.id})
        self.assertEqual((first.units, first.units_to_enter), (12, False))
        second = self.env["bf.school.course"].create(
            {"group_id": self._second_cycle_group(["S3"], name="S3A").id, "subject_id": subject.id})
        self.assertEqual((second.units, second.units_to_enter), (0, True), "the school enters them")
        second.with_user(self.office).units = 6
        self.assertEqual((second.units, second.units_to_enter), (6, False))
        mixed = self.env["bf.school.course"].create(
            {"group_id": self._second_cycle_group(["S2", "S3"], name="S23").id, "subject_id": subject.id})
        self.assertEqual(mixed.units, 0)
        elementary = self.env["bf.school.course"].create(
            {"group_id": self._second_cycle_group([], name="E1").id, "subject_id": self.math.id})
        self.assertFalse(elementary.units_to_enter)


@tagged("post_install", "-at_install")
class TestThresholdAndHomeroom(GradebookCase):

    def test_below_the_threshold_follows_the_year_threshold(self):
        quiz = self._assess(self.pe_course, self.t1, name="Quiz", max_score=100.0)
        self._grade(quiz, self.alpha, 65.0)
        self._grade(quiz, self.bravo, 55.0)
        Result = self.env["bf.school.course.result"]
        below = Result.search([("course_id", "=", self.pe_course.id), ("below_threshold", "=", True)])
        self.assertEqual(below.student_id, self.bravo, "60 % by default: 55 is below, 65 is not")
        self.year.success_threshold = 70
        below = Result.search([("course_id", "=", self.pe_course.id), ("below_threshold", "=", True)])
        self.assertEqual(below.student_id, self.alpha | self.bravo, "the year asks 70 %: 65 is below too")
        above = Result.search([("course_id", "=", self.pe_course.id), ("below_threshold", "=", False)])
        self.assertFalse(above & below)
        self.assertTrue(self._result(self.pe_course, self.t1, self.alpha).below_threshold)

    def test_the_group_form_shows_its_homeroom_teachers(self):
        arch = self.env["bf.school.group"].with_user(self.office).get_views([(False, "form")])["views"]["form"]["arch"]
        self.assertIn('name="homeroom_ids"', arch)
        self.assertEqual(self.group.homeroom_ids, self.teacher, "taken from the group's only teacher")
        self.group.with_user(self.office).homeroom_ids = self.colleague
        self.assertEqual(self.group.homeroom_ids, self.colleague)
