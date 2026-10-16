import base64
import io
import json
import logging
import zipfile
from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, new_test_user, tagged


class ReportCardCase(HttpCase):

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
        cls.office = new_test_user(env, login="rc_office", groups="bf_school_core.group_school_manager")
        cls.homeroom = new_test_user(env, login="rc_homeroom", groups="bf_school_core.group_school_user")
        cls.math_teacher = new_test_user(env, login="rc_math", groups="bf_school_core.group_school_user")
        cls.outsider = new_test_user(env, login="rc_outsider", groups="bf_school_core.group_school_user")
        cls.level3 = env.ref("bf_school_core.level_e3")
        cls.group = env["bf.school.group"].create({
            "name": "301", "school_id": cls.school.id, "year_id": cls.year.id,
            "level_ids": [(6, 0, cls.level3.ids)], "teacher_ids": [(6, 0, cls.homeroom.ids)]})
        Partner = env["res.partner"]
        cls.alpha = Partner.create({"name": "Alpha Essai", "is_student": True, "student_birthdate": date(2017, 3, 1),
                                    "student_permanent_code": "ESSA17030112"})
        cls.bravo = Partner.create({"name": "Bravo Essai", "is_student": True, "student_birthdate": date(2017, 5, 1)})
        for student in (cls.alpha, cls.bravo):
            env["bf.school.enrollment"].create({"student_id": student.id, "group_id": cls.group.id})
        cls.pe_course = env["bf.school.course"].create({
            "group_id": cls.group.id, "subject_id": env.ref("bf_school_gradebook.subject_el_eps").id,
            "teacher_ids": [(6, 0, cls.homeroom.ids)]})
        cls.math_course = env["bf.school.course"].create({
            "group_id": cls.group.id, "subject_id": env.ref("bf_school_gradebook.subject_el_mat").id,
            "teacher_ids": [(6, 0, cls.math_teacher.ids)]})
        solve, reason = cls.math_course.component_ids.sorted("sequence")
        solve.weight, reason.weight = 30.0, 70.0
        cls.solve, cls.reason = solve, reason

    # --- helpers ---------------------------------------------------------------------------------

    def _grades(self, term, student, pe=None, solve=None, reason=None):
        env = self.env
        for course, component, score, name in ((self.pe_course, None, pe, "PE"),
                                               (self.math_course, self.solve, solve, "S"),
                                               (self.math_course, self.reason, reason, "R")):
            if score is None:
                continue
            assessment = env["bf.school.assessment"].search([
                ("course_id", "=", course.id), ("term_id", "=", term.id), ("name", "=", "%s%s" % (name, term.sequence))])
            if not assessment:
                assessment = env["bf.school.assessment"].create({
                    "name": "%s%s" % (name, term.sequence), "course_id": course.id, "term_id": term.id,
                    "component_id": component.id if component else False})
            assessment.result_ids.filtered(lambda r: r.student_id == student).write({"score": float(score)})

    def _generate(self, term, group=None):
        return self.env["bf.school.report.card"].with_user(self.office)._generate(group or self.group, term)

    def _card(self, student, term):
        return self.env["bf.school.report.card"].sudo().search(
            [("student_id", "=", student.id), ("term_id", "=", term.id)], order="version desc", limit=1)

    def _line(self, card, course):
        return card.line_ids.filtered(lambda l: l.course_id == course)


@tagged("post_install", "-at_install")
class TestGeneration(ReportCardCase):

    def test_a_draft_per_student_with_a_line_per_course(self):
        self._grades(self.t1, self.alpha, pe=80, solve=60, reason=80)
        cards = self._generate(self.t1)
        self.assertEqual(cards.student_id, self.alpha | self.bravo)
        card = self._card(self.alpha, self.t1)
        self.assertEqual((card.state, card.kind, card.stage), ("draft", "interim", "elementary"))
        self.assertEqual(card.level_id, self.level3)
        self.assertEqual(sorted(card.line_ids.mapped("subject_name")), ["Mathematics", "Physical education and health"])
        pe = self._line(card, self.pe_course)
        self.assertEqual((pe.has_percent, pe.percent, pe.group_average), (True, 80, 80))
        maths = self._line(card, self.math_course)
        self.assertEqual((maths.percent, maths.group_average), (74, 74))
        self.assertEqual({c.name: c.percent for c in maths.component_ids},
                         {"Solve a situational problem": 60, "Use mathematical reasoning": 80})

    def test_a_student_with_no_grade_has_blank_lines_not_zeros(self):
        self._grades(self.t1, self.alpha, pe=80)
        self._generate(self.t1)
        bravo = self._line(self._card(self.bravo, self.t1), self.pe_course)
        self.assertFalse(bravo.has_percent)

    def test_regenerating_a_draft_keeps_the_comments_and_follows_the_grades(self):
        self._grades(self.t1, self.alpha, pe=80)
        self._generate(self.t1)
        card = self._card(self.alpha, self.t1)
        self._line(card, self.pe_course).comment = "Good effort"
        self._grades(self.t1, self.alpha, pe=90)
        self._generate(self.t1)
        line = self._line(self._card(self.alpha, self.t1), self.pe_course)
        self.assertEqual((line.percent, line.comment), (90, "Good effort"))
        self.assertEqual(self.env["bf.school.report.card"].sudo().search_count(
            [("student_id", "=", self.alpha.id), ("term_id", "=", self.t1.id)]), 1)

    def test_terms_one_and_two_show_only_what_was_evaluated(self):
        self._grades(self.t1, self.alpha, solve=60)
        self._generate(self.t1)
        maths = self._line(self._card(self.alpha, self.t1), self.math_course)
        self.assertEqual(maths.component_ids.mapped("name"), ["Solve a situational problem"])

    def test_a_preschool_group_has_no_percentage_report_card(self):
        kindergarten = self.env["bf.school.group"].create({
            "name": "K5", "school_id": self.school.id, "year_id": self.year.id,
            "level_ids": [(6, 0, self.env.ref("bf_school_core.level_k5").ids)]})
        with self.assertRaises(UserError):
            self._generate(self.t1, kindergarten)

    def test_the_office_generates_from_the_wizard_in_the_language_it_picks(self):
        self._grades(self.t1, self.alpha, pe=80)
        wizard = self.env["bf.school.report.card.generate"].with_user(self.office).create({
            "group_id": self.group.id, "term_id": self.t1.id, "lang": "en_US"})
        action = wizard.action_generate()
        self.assertEqual(sorted(self.env["bf.school.report.card"].browse(action["domain"][0][2]).student_id.ids),
                         sorted((self.alpha | self.bravo).ids))
        self.assertEqual(self._card(self.alpha, self.t1).lang, "en_US")

    def test_the_correction_wizard_asks_why(self):
        self._grades(self.t1, self.alpha, pe=80, solve=60, reason=80)
        self._generate(self.t1)
        card = self._card(self.alpha, self.t1)
        card.with_user(self.office).action_issue()
        wizard = self.env["bf.school.report.card.correct"].with_user(self.office).create(
            {"card_id": card.id, "reason": "A grade was entered twice"})
        action = wizard.action_correct()
        self.assertEqual(self.env["bf.school.report.card"].browse(action["res_id"]).correction_reason,
                         "A grade was entered twice")

    def test_only_the_office_generates_through_the_orm(self):
        with self.assertRaises(AccessError):
            self.env["bf.school.report.card"].with_user(self.homeroom).create({
                "student_id": self.alpha.id, "group_id": self.group.id, "term_id": self.t1.id})


@tagged("post_install", "-at_install")
class TestIssuing(ReportCardCase):

    def _ready(self, term=None, **grades):
        term = term or self.t1
        self._grades(term, self.alpha, **(grades or dict(pe=80, solve=60, reason=80)))
        self._generate(term)
        return self._card(self.alpha, term)

    def test_issuing_seals_the_card_and_freezes_it(self):
        card = self._ready()
        card.with_user(self.office).action_issue()
        self.assertEqual(card.state, "issued")
        self.assertTrue(card.issued_on)
        self.assertEqual(card.issued_by_id, self.office)
        data = json.loads(card.sudo().snapshot)
        self.assertEqual(data["student"]["name"], "Alpha Essai")
        self.assertEqual(data["term"], 1)
        self.assertEqual({l["subject"]: l["result"] for l in data["lines"]},
                         {"Mathematics": 74, "Physical education and health": 80})
        self.assertEqual(len(card.snapshot_sha256), 64)
        with self.assertRaises(UserError):
            card.with_user(self.office).write({"comment": "Late edit"})
        with self.assertRaises(AccessError):
            self._line(card, self.pe_course).with_user(self.office).write({"percent": 100})

    def test_the_seal_detects_a_change_behind_the_orm(self):
        card = self._ready()
        card.with_user(self.office).action_issue()
        self.assertEqual(card.with_user(self.office).action_verify()["params"]["type"], "success")
        self.env.cr.execute("UPDATE bf_school_report_card_line SET percent = 99 WHERE card_id = %s AND course_id = %s",
                            (card.id, self.pe_course.id))
        self.env.invalidate_all()
        self.assertEqual(card.with_user(self.office).action_verify()["params"]["type"], "danger")

    def test_only_the_office_issues_and_the_state_does_not_move_by_rpc(self):
        card = self._ready()
        with self.assertRaises(AccessError):
            card.with_user(self.homeroom).action_issue()
        for field, value in (("state", "issued"), ("snapshot_sha256", "x"), ("published_on", fields.Datetime.now()),
                             ("issued_on", fields.Datetime.now())):
            with self.assertRaises(AccessError):
                card.with_user(self.office).write({field: value})
            with self.assertRaises(AccessError):
                card.with_user(self.homeroom).write({field: value})

    def test_a_deadline_missed_is_recorded(self):
        self.t1.report_deadline = date(2026, 10, 1)
        card = self._ready()
        self.assertTrue(card.is_overdue)
        card.with_user(self.office).action_issue()
        self.assertTrue(card.issued_late)
        self.assertTrue(card.is_overdue, "the deadline is for the families to have it: issued but not shown is still overdue")
        card.with_user(self.office).action_publish()
        self.assertTrue(card.published_late)
        self.assertFalse(card.is_overdue)

    def test_an_on_time_card_is_not_late(self):
        card = self._ready()
        card.with_user(self.office).action_issue()
        self.assertFalse(card.issued_late)

    def test_weights_not_entered_stop_the_issue(self):
        self.reason.weight = 0.0
        card = self._ready()
        with self.assertRaises(UserError):
            card.with_user(self.office).action_issue()

    def test_the_last_report_card_needs_the_year_end_decision(self):
        card = self._ready(term=self.t3, pe=70, solve=60, reason=80)
        self.assertEqual(card.kind, "final")
        with self.assertRaises(UserError):
            card.with_user(self.office).action_issue()
        card.with_user(self.office).write({"decision": "promoted"})
        card.with_user(self.office).action_issue()
        self.assertEqual(card.state, "issued")

    def test_the_decision_belongs_to_the_last_report_card(self):
        card = self._ready()
        with self.assertRaises(ValidationError):
            card.with_user(self.office).write({"decision": "promoted"})

    def test_the_card_is_filed_as_the_results_piece_of_the_students_file(self):
        card = self._ready()
        card.with_user(self.office).action_issue()
        record = self.env["bf.school.record"].search([("student_id", "=", self.alpha.id)])
        docs = record.document_ids.filtered(lambda d: d.type_id.code == "results")
        self.assertEqual(len(docs), 1)
        self.assertEqual((docs.state, docs.source_model, docs.source_res_id), ("received", "bf.school.report.card", card.id))
        card._file_in_record()
        self.assertEqual(len(record.document_ids.filtered(lambda d: d.type_id.code == "results")), 1,
                         "filing twice files once")

    def test_a_correction_is_a_new_version_that_replaces_the_old(self):
        card = self._ready()
        self._line(card, self.pe_course).comment = "Good effort"
        card.with_user(self.office).action_issue()
        self._grades(self.t1, self.alpha, pe=85)
        with self.assertRaises(UserError):
            card.with_user(self.office).action_correct()
        action = card.with_user(self.office).with_context(correction_reason="Grade corrected").action_correct()
        new = self.env["bf.school.report.card"].browse(action["res_id"])
        self.assertEqual((new.version, new.state, new.supersedes_id, new.correction_reason),
                         (2, "draft", card, "Grade corrected"))
        line = self._line(new, self.pe_course)
        self.assertEqual((line.percent, line.comment), (85, "Good effort"))
        self.assertEqual(self._line(card, self.pe_course).percent, 80, "the issued one is untouched")
        new.with_user(self.office).action_issue()
        self.assertEqual((card.state, card.replaced_by_id, new.state), ("superseded", new, "issued"))
        self.assertEqual(self.env["bf.school.record.document"].search_count(
            [("source_model", "=", "bf.school.report.card"), ("student_id", "=", self.alpha.id)]), 2)

    def test_an_issued_card_is_not_deleted(self):
        card = self._ready()
        card.with_user(self.office).action_issue()
        with self.assertRaises(UserError):
            card.with_user(self.office).unlink()


@tagged("post_install", "-at_install")
class TestFinalReportCard(ReportCardCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.sec1 = env.ref("bf_school_core.level_s1")
        cls.sec_group = env["bf.school.group"].create({
            "name": "Sec 1A", "school_id": cls.school.id, "year_id": cls.year.id,
            "level_ids": [(6, 0, cls.sec1.ids)], "teacher_ids": [(6, 0, cls.homeroom.ids)]})
        cls.cara = env["res.partner"].create({"name": "Cara Essai", "is_student": True, "student_birthdate": date(2013, 2, 2)})
        cls.dan = env["bf.school.enrollment"].create({
            "student_id": env["res.partner"].create({"name": "Dan Essai", "is_student": True}).id,
            "group_id": cls.sec_group.id}).student_id
        env["bf.school.enrollment"].create({"student_id": cls.cara.id, "group_id": cls.sec_group.id})
        cls.hist = env["bf.school.course"].create({
            "group_id": cls.sec_group.id, "subject_id": env.ref("bf_school_gradebook.subject_sec_his").id})
        cls.hist.ministerial_exam = True

    def _sec_grades(self, student, scores):
        for term, score in zip((self.t1, self.t2, self.t3), scores):
            assessment = self.env["bf.school.assessment"].search(
                [("course_id", "=", self.hist.id), ("term_id", "=", term.id)])[:1] or \
                self.env["bf.school.assessment"].create({"name": "H%s" % term.sequence, "course_id": self.hist.id,
                                                         "term_id": term.id})
            assessment.result_ids.filtered(lambda r: r.student_id == student).write({"score": float(score)})

    def test_the_last_card_carries_the_final_result_the_exam_and_the_units(self):
        self._sec_grades(self.cara, (70, 80, 90))
        self._sec_grades(self.dan, (50, 50, 50))
        for student, percent in ((self.cara, 50), (self.dan, 40)):
            self.env["bf.school.exam.result"].create({
                "course_id": self.hist.id, "student_id": student.id, "state": "graded",
                "exam_year": 2027, "exam_month": 6, "percent": percent})
        self.env["bf.school.report.card"].with_user(self.office)._generate(self.sec_group, self.t3)
        cara = self._card(self.cara, self.t3)
        line = self._line(cara, self.hist)
        self.assertEqual(cara.stage, "secondary")
        self.assertEqual((line.final_percent, line.exam_percent, line.outcome), (77, 50, "passed"),
                         "school final 84 = 0.2 x 70 + 0.2 x 80 + 0.6 x 90; with a 50 exam at 20 %: 77.2")
        self.assertEqual((line.units, line.units_earned), (6, 6))
        dan = self._line(self._card(self.dan, self.t3), self.hist)
        self.assertEqual((dan.final_percent, dan.outcome, dan.units_earned), (48, "failed", 0), "0.8 x 50 + 0.2 x 40")
        self.assertEqual(line.final_group_average, 63, "(77 + 48) / 2 = 62.5, half up")

    def test_the_units_of_a_course_whose_exam_is_not_marked_are_not_zero_but_not_earned_yet(self):
        self._sec_grades(self.cara, (70, 80, 90))
        self.env["bf.school.exam.result"].create({
            "course_id": self.hist.id, "student_id": self.cara.id, "state": "taken",
            "exam_year": 2027, "exam_month": 6})
        self.env["bf.school.report.card"].with_user(self.office)._generate(self.sec_group, self.t3)
        card = self._card(self.cara, self.t3)
        card.with_user(self.office).write({"decision": "promoted"})
        card.with_user(self.office).action_issue()
        html, _kind = self.env["ir.actions.report"]._render_qweb_html(
            "bf_school_report_card.report_card_document", card.ids)
        text = html.decode()
        self.assertIn("Provisional", text)
        self.assertIn("— / 6", text)
        self.assertNotIn("0 / 6", text)

    def test_the_pdf_is_rendered_from_the_sealed_content_and_carries_the_final_columns(self):
        self._sec_grades(self.cara, (70, 80, 90))
        self.env["bf.school.report.card"].with_user(self.office)._generate(self.sec_group, self.t3)
        card = self._card(self.cara, self.t3)
        card.with_user(self.office).write({"decision": "promoted"})
        card.with_user(self.office).action_issue()
        html, _kind = self.env["ir.actions.report"]._render_qweb_html("bf_school_report_card.report_card_document", card.ids)
        text = html.decode()
        for expected in ("Cara Essai", "History and citizenship education", "84 %", "Year-end decision", "Moves on"):
            self.assertIn(expected, text)
        self.assertIn(card.snapshot_sha256[:16], text)


@tagged("post_install", "-at_install")
class TestPortal(ReportCardCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.mother = new_test_user(env, login="rc_mother", name="Maman Essai", email="maman.rc@example.invalid",
                                   groups="base.group_portal")
        cls.mother.partner_id.lang = "fr_CA"
        cls.grandma = new_test_user(env, login="rc_grandma", name="Mamie Essai", email="mamie.rc@example.invalid",
                                    groups="base.group_portal")
        cls.stranger = new_test_user(env, login="rc_stranger", name="Etranger", email="etr.rc@example.invalid",
                                     groups="base.group_portal")
        Link = env["bf.school.guardian.link"]
        Link.create({"student_id": cls.alpha.id, "guardian_id": cls.mother.partner_id.id, "relationship": "mother"})
        Link.create({"student_id": cls.alpha.id, "guardian_id": cls.grandma.partner_id.id, "relationship": "grandparent"})
        cls._grades(cls, cls.t1, cls.alpha, pe=80, solve=60, reason=80)
        cls._generate(cls, cls.t1)
        cls.card = cls._card(cls, cls.alpha, cls.t1)
        cls.card.with_user(cls.office).action_issue()

    def _mails_to(self, partner):
        return self.env["mail.mail"].sudo().search([("recipient_ids", "in", partner.ids)])

    def test_an_issued_card_is_hidden_until_it_is_shown(self):
        self.authenticate("rc_mother", "rc_mother")
        self.assertNotIn("Alpha Essai", self.url_open("/my/school/report-cards").text)
        self.card.with_user(self.office).action_publish()
        self.assertIn("Alpha Essai", self.url_open("/my/school/report-cards").text)

    def test_publishing_tells_the_adults_with_parental_authority_by_name_of_the_child(self):
        before = self._mails_to(self.mother.partner_id)
        self.card.with_user(self.office).action_publish()
        sent = self._mails_to(self.mother.partner_id) - before
        self.assertEqual(len(sent), 1)
        self.assertIn("Alpha Essai", sent.subject)
        prefix = self.env["bf.school"]._school_url_lang("fr_CA")
        self.assertIn("%s/my/school/report-cards" % prefix, sent.body_html,
                      "a French family lands on the French page when the site has one")
        self.assertFalse(self._mails_to(self.grandma.partner_id), "no authority, no report card")
        self.card.with_user(self.office).action_publish()
        self.assertEqual(len(self._mails_to(self.mother.partner_id) - before), 1, "published once")

    def test_the_grandparent_and_a_stranger_see_nothing_and_get_nothing(self):
        self.card.with_user(self.office).action_publish()
        self.card._render_pdf()
        for login in ("rc_grandma", "rc_stranger"):
            self.authenticate(login, login)
            self.assertNotIn("Alpha Essai", self.url_open("/my/school/report-cards").text)
            self.assertEqual(self.url_open("/school/report-card/%s/pdf" % self.card.id).status_code, 404)

    def test_the_pdf_is_prepared_by_the_cron_not_in_the_family_request(self):
        self.card.with_user(self.office).action_publish()
        self.authenticate("rc_mother", "rc_mother")
        self.assertFalse(self.card.sudo().pdf)
        response = self.url_open("/school/report-card/%s/pdf" % self.card.id)
        self.assertEqual(response.status_code, 202)
        self.assertFalse(self.card.sudo().pdf, "the request renders nothing")
        self.env["bf.school.report.card"]._cron_render_pdfs()
        self.assertTrue(self.card.sudo().pdf)

    def test_a_download_leaves_a_read_receipt_once(self):
        self.card.with_user(self.office).action_publish()
        self.card._render_pdf()
        self.authenticate("rc_mother", "rc_mother")
        for _i in range(2):
            self.assertEqual(self.url_open("/school/report-card/%s/pdf" % self.card.id).status_code, 200)
        self.assertEqual(self.card.read_ids.guardian_id, self.mother.partner_id)
        self.assertEqual(len(self.card.read_ids), 1)

    def test_an_unpublished_card_is_not_served_to_the_family(self):
        self.card._render_pdf()
        self.authenticate("rc_mother", "rc_mother")
        self.assertEqual(self.url_open("/school/report-card/%s/pdf" % self.card.id).status_code, 404)

    def test_a_student_of_age_gets_the_card_not_the_parents(self):
        self.alpha.student_birthdate = date(2007, 1, 1)
        self.card.invalidate_recordset()
        self.assertEqual(self.card.recipient, "student")
        before = self._mails_to(self.mother.partner_id)
        self.card.with_user(self.office).action_publish()
        self.assertEqual(self._mails_to(self.mother.partner_id), before)
        self.card._render_pdf()
        self.authenticate("rc_mother", "rc_mother")
        self.assertEqual(self.url_open("/school/report-card/%s/pdf" % self.card.id).status_code, 404)

    def test_a_superseded_card_is_no_longer_offered(self):
        self.card.with_user(self.office).action_publish()
        action = self.card.with_user(self.office).with_context(correction_reason="Fix").action_correct()
        new = self.env["bf.school.report.card"].browse(action["res_id"])
        new.with_user(self.office).action_issue()
        new.with_user(self.office).action_publish()
        self.authenticate("rc_mother", "rc_mother")
        page = self.url_open("/my/school/report-cards").text
        self.assertIn("/school/report-card/%s/pdf" % new.id, page)
        self.assertNotIn("/school/report-card/%s/pdf" % self.card.id, page)

    def test_staff_download_what_they_may_read(self):
        self.card._render_pdf()
        self.authenticate("rc_homeroom", "rc_homeroom")
        self.assertEqual(self.url_open("/school/report-card/%s/pdf" % self.card.id).status_code, 200)
        self.authenticate("rc_outsider", "rc_outsider")
        self.assertEqual(self.url_open("/school/report-card/%s/pdf" % self.card.id).status_code, 404)


@tagged("post_install", "-at_install")
class TestAccess(ReportCardCase):

    def test_a_teacher_comments_their_own_subject_and_never_the_figures(self):
        self._grades(self.t1, self.alpha, pe=80, solve=60, reason=80)
        self._generate(self.t1)
        card = self._card(self.alpha, self.t1)
        maths = self._line(card, self.math_course)
        maths.with_user(self.math_teacher).write({"comment": "Careful work"})
        self.assertEqual(maths.comment, "Careful work")
        with self.assertRaises(AccessError):
            maths.with_user(self.math_teacher).write({"percent": 100})
        with self.assertRaises(AccessError):
            self._line(card, self.pe_course).with_user(self.math_teacher).write({"comment": "Not mine"})

    def test_the_homeroom_teacher_writes_the_general_comment_and_a_course_teacher_does_not(self):
        self._grades(self.t1, self.alpha, pe=80, solve=60, reason=80)
        self._generate(self.t1)
        card = self._card(self.alpha, self.t1)
        card.with_user(self.homeroom).write({"comment": "A good term"})
        self.assertEqual(card.comment, "A good term")
        with self.assertRaises(AccessError):
            card.with_user(self.math_teacher).write({"comment": "Mine"})
        with self.assertRaises(AccessError):
            card.with_user(self.homeroom).write({"student_id": self.bravo.id})

    def test_teachers_read_only_the_cards_of_their_groups_and_subjects(self):
        self._grades(self.t1, self.alpha, pe=80, solve=60, reason=80)
        self._generate(self.t1)
        Card = self.env["bf.school.report.card"]
        self.assertEqual(Card.with_user(self.homeroom).search_count([]), 2)
        # a teacher of one subject reads the lines of their subject (the screen of their comments), not the cards
        self.assertEqual(Card.with_user(self.math_teacher).search_count([]), 0)
        Line = self.env["bf.school.report.card.line"]
        self.assertEqual(Line.with_user(self.math_teacher).search([]).mapped("subject_name"), ["Mathematics"] * 2)
        self.assertEqual(Card.with_user(self.outsider).search_count([]), 0)

    def test_nobody_creates_lines_or_read_receipts_by_hand(self):
        self._grades(self.t1, self.alpha, pe=80)
        self._generate(self.t1)
        card = self._card(self.alpha, self.t1)
        with self.assertRaises(AccessError):
            self.env["bf.school.report.card.line"].with_user(self.office).create({"card_id": card.id, "subject_name": "X"})
        with self.assertRaises(AccessError):
            self.env["bf.school.report.card.read"].with_user(self.office).create(
                {"card_id": card.id, "guardian_id": self.alpha.id})


@tagged("post_install", "-at_install")
class TestCommunications(ReportCardCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.mother = env["res.partner"].create({"name": "Maman Essai", "email": "maman.com@example.invalid"})
        cls.grandma = env["res.partner"].create({"name": "Mamie Essai", "email": "mamie.com@example.invalid"})
        Link = env["bf.school.guardian.link"]
        Link.create({"student_id": cls.alpha.id, "guardian_id": cls.mother.id, "relationship": "mother"})
        Link.create({"student_id": cls.alpha.id, "guardian_id": cls.grandma.id, "relationship": "grandparent"})

    def _mails_to(self, partner):
        return self.env["mail.mail"].sudo().search([("recipient_ids", "in", partner.ids)])

    def test_the_first_communication_is_prepared_for_each_student_due_october_15(self):
        Communication = self.env["bf.school.communication"]
        created = Communication.with_user(self.office)._prepare_first(self.year)
        self.assertEqual(created.student_id, self.alpha | self.bravo)
        self.assertEqual(set(created.mapped("due_date")), {date(2026, 10, 15)})
        self.assertFalse(Communication._prepare_first(self.year), "prepared once")

    def test_a_late_first_communication_is_flagged(self):
        self.year.first_communication_date = date(2026, 10, 1)
        created = self.env["bf.school.communication"]._prepare_first(self.year)
        self.assertTrue(all(created.mapped("is_overdue")))

    def test_monthly_information_is_prepared_for_a_student_below_the_threshold_once_a_month(self):
        self._grades(self.t1, self.alpha, pe=45)
        self._grades(self.t1, self.bravo, pe=75)
        Communication = self.env["bf.school.communication"]
        created = Communication._prepare_monthly_performance(self.year)
        self.assertEqual(created.student_id, self.alpha)
        self.assertEqual((created.kind, created.reason), ("monthly", "performance"))
        self.assertIn("Physical education and health", created.body)
        self.assertFalse(Communication._prepare_monthly_performance(self.year), "once this month")

    def test_sending_tells_the_adults_with_parental_authority_and_is_kept_as_it_went(self):
        item = self.env["bf.school.communication"].with_user(self.office).create({
            "student_id": self.alpha.id, "year_id": self.year.id, "kind": "other", "subject": "A note",
            "body": "Alpha did very well this week."})
        before_mother, before_grandma = self._mails_to(self.mother), self._mails_to(self.grandma)
        item.with_user(self.office).action_send_email()
        self.assertEqual((item.state, item.channel), ("sent", "email"))
        self.assertEqual(len(self._mails_to(self.mother) - before_mother), 1)
        self.assertEqual(self._mails_to(self.grandma), before_grandma)
        with self.assertRaises(UserError):
            item.with_user(self.office).write({"body": "Changed"})
        with self.assertRaises(UserError):
            item.with_user(self.office).unlink()

    def test_the_state_of_a_communication_does_not_move_by_rpc(self):
        item = self.env["bf.school.communication"].with_user(self.office).create({
            "student_id": self.alpha.id, "year_id": self.year.id, "kind": "other", "body": "x"})
        with self.assertRaises(AccessError):
            item.with_user(self.office).write({"state": "sent"})
        with self.assertRaises(AccessError):
            self.env["bf.school.communication"].with_user(self.office).create({
                "student_id": self.alpha.id, "year_id": self.year.id, "kind": "other", "state": "sent"})

    def test_a_monthly_information_says_which_case_it_answers(self):
        with self.assertRaises(ValidationError):
            self.env["bf.school.communication"].create(
                {"student_id": self.alpha.id, "year_id": self.year.id, "kind": "monthly"})

    def test_a_teacher_writes_to_the_families_of_their_own_groups_only(self):
        mine = self.env["bf.school.communication"].with_user(self.homeroom).create({
            "student_id": self.alpha.id, "year_id": self.year.id, "kind": "other", "body": "Hello"})
        self.assertTrue(mine)
        with self.assertRaises(AccessError):
            self.env["bf.school.communication"].with_user(self.outsider).create({
                "student_id": self.alpha.id, "year_id": self.year.id, "kind": "other", "body": "Hello"})

    def test_nothing_to_tell_nobody_is_an_error_not_a_silent_success(self):
        item = self.env["bf.school.communication"].with_user(self.office).create({
            "student_id": self.bravo.id, "year_id": self.year.id, "kind": "other", "body": "Hello"})
        with self.assertRaises(UserError):
            item.with_user(self.office).action_send_email()
        item.with_user(self.office).action_mark_sent()
        self.assertEqual((item.state, item.channel), ("sent", "other"))


@tagged("post_install", "-at_install")
class TestRecordHooks(ReportCardCase):

    def test_the_students_file_carries_the_issued_report_cards(self):
        self._grades(self.t1, self.alpha, pe=80)
        self._generate(self.t1)
        card = self._card(self.alpha, self.t1)
        card.with_user(self.office).action_issue()
        record = self.env["bf.school.record"].search([("student_id", "=", self.alpha.id)])
        payload = record._portable_payload()
        self.assertEqual([c["term"] for c in payload["report_cards"]], [1])
        pe = [l for l in payload["report_cards"][0]["lines"] if l["subject"] == "Physical education and health"]
        self.assertEqual(pe[0]["result"], 80)
        transfer = self.env["bf.school.record.transfer"].with_user(self.office).create(
            {"record_id": record.id, "destination_name": "Destination"})
        transfer.with_user(self.office).action_prepare()
        import base64
        import io
        import zipfile
        archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(transfer.sudo().package)))
        self.assertEqual(len(json.loads(archive.read("transfer.json"))["report_cards"]), 1)
        bare = self.env["bf.school.record.transfer"].with_user(self.office).create(
            {"record_id": record.id, "destination_name": "Destination", "include_results": False})
        bare.with_user(self.office).action_prepare()
        archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(bare.sudo().package)))
        self.assertNotIn("report_cards", json.loads(archive.read("transfer.json")))


@tagged("post_install", "-at_install")
class TestEdgeCases(ReportCardCase):
    """Edge cases and guards of the report card, each as it was found."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.mother = new_test_user(env, login="rcf_mother", name="Maman Essai", email="m.rcf@example.invalid",
                                   groups="base.group_portal")
        env["bf.school.guardian.link"].create({
            "student_id": cls.alpha.id, "guardian_id": cls.mother.partner_id.id, "relationship": "mother"})
        cls._grades(cls, cls.t1, cls.alpha, pe=80, solve=60, reason=80)
        cls._generate(cls, cls.t1)
        cls.card = cls._card(cls, cls.alpha, cls.t1)

    def test_the_report_route_does_not_serve_a_report_card_to_who_may_not_read_it(self):
        self.card.with_user(self.office).action_issue()
        url = "/report/html/bf_school_report_card.report_card_document/%s" % self.card.id
        for login in ("rcf_mother", "rc_outsider", "rc_math"):
            self.authenticate(login, login)
            self.assertNotIn("Alpha Essai", self.url_open(url).text, login)
        self.authenticate("rc_office", "rc_office")
        self.assertIn("Alpha Essai", self.url_open(url).text)
        self.authenticate("rc_homeroom", "rc_homeroom")
        self.assertIn("Alpha Essai", self.url_open(url).text)

    def test_a_teacher_of_one_subject_does_not_download_the_whole_report_card(self):
        self.card.with_user(self.office).action_issue()
        self.card._render_pdf()
        self.authenticate("rc_math", "rc_math")
        self.assertEqual(self.url_open("/school/report-card/%s/pdf" % self.card.id).status_code, 404)
        self.authenticate("rc_homeroom", "rc_homeroom")
        self.assertEqual(self.url_open("/school/report-card/%s/pdf" % self.card.id).status_code, 200)

    def test_a_draft_is_not_shown_to_the_families(self):
        self._grades(self.t1, self.alpha, pe=80, solve=60, reason=80)
        self._generate(self.t1)
        card = self._card(self.alpha, self.t1)
        with self.assertRaises(UserError):
            card.with_user(self.office).action_publish()
        self.assertFalse(card.published_on)

    def test_a_report_card_stays_on_its_student_its_group_and_its_term_even_for_the_office(self):
        self._grades(self.t1, self.alpha, pe=80, solve=60, reason=80)
        self._generate(self.t1)
        card = self._card(self.alpha, self.t1)
        for vals in ({"student_id": self.bravo.id}, {"term_id": self.t2.id}, {"group_id": self.group.id}):
            with self.assertRaises(UserError, msg=vals):
                card.with_user(self.office).write(vals)
        self.assertEqual((card.student_id, card.term_id), (self.alpha, self.t1))

    def test_the_homeroom_teacher_not_just_any_teacher_of_the_group_writes_the_general_comment(self):
        colleague = new_test_user(self.env, login="rcf_colleague", groups="bf_school_core.group_school_user")
        self.group.teacher_ids = [(4, colleague.id)]
        with self.assertRaises(AccessError):
            self.card.with_user(colleague).write({"comment": "Mine"})
        self.card.with_user(self.homeroom).write({"comment": "Hers"})
        self.assertEqual(self.card.comment, "Hers")

    def test_refreshing_a_report_card_is_the_offices(self):
        with self.assertRaises(AccessError):
            self.card.with_user(self.homeroom).action_refresh()
        self.card.with_user(self.office).action_refresh()

    def test_one_report_card_that_cannot_be_rendered_does_not_stop_the_others(self):
        other = self._card(self.bravo, self.t1)
        self._grades(self.t1, self.bravo, pe=70)
        self._generate(self.t1)
        other = self._card(self.bravo, self.t1)
        (self.card | other).with_user(self.office).action_issue()
        real = type(self.env["ir.actions.report"])._render_qweb_pdf

        def flaky(report, report_ref, res_ids=None, data=None):
            if res_ids == self.card.ids:
                raise ValueError("wkhtmltopdf broke")
            return real(report, report_ref, res_ids, data)

        with patch.object(type(self.env["ir.actions.report"]), "_render_qweb_pdf", flaky):
            self.env["bf.school.report.card"]._cron_render_pdfs()
        self.assertTrue(other.sudo().pdf)
        self.assertFalse(self.card.sudo().pdf)
        self.assertIn("wkhtmltopdf broke", self.card.pdf_error)
        waiting = self.env["bf.school.report.card"].sudo().search_count(
            [("state", "=", "issued"), ("pdf", "=", False), ("pdf_error", "=", False)])
        self.assertEqual(waiting, 0, "the cron does not retry it for ever")
        self.card.with_user(self.office).action_render_pdf()
        self.assertTrue(self.card.sudo().pdf)
        self.assertFalse(self.card.pdf_error)

    def test_a_name_outside_latin_1_downloads(self):
        self.alpha.name = "Nguyễn – Alpha"
        self.card.with_user(self.office).action_issue()
        self.card.with_user(self.office).action_publish()
        self.card._render_pdf()
        self.authenticate("rcf_mother", "rcf_mother")
        response = self.url_open("/school/report-card/%s/pdf" % self.card.id)
        self.assertEqual(response.status_code, 200)
        self.assertIn("filename*=UTF-8''", response.headers["Content-Disposition"])
        self.assertEqual(len(self.card.read_ids), 1)

    def test_a_student_of_age_gets_the_card_by_email_and_in_their_own_portal(self):
        self.alpha.write({"student_birthdate": date(2007, 1, 1), "email": "alpha.rcf@example.invalid"})
        new_test_user(self.env, login="rcf_student", groups="base.group_portal", partner_id=self.alpha.id)
        self.card.with_user(self.office).action_issue()
        self.card.invalidate_recordset()
        self.assertEqual(self.card._recipients(), self.alpha)
        self.card.with_user(self.office).action_publish()
        mails = self.env["mail.mail"].sudo().search([("recipient_ids", "in", self.alpha.ids)])
        self.assertTrue(mails)
        self.assertFalse(self.env["mail.mail"].sudo().search([("recipient_ids", "in", self.mother.partner_id.ids)]))
        self.authenticate("rcf_student", "rcf_student")
        self.card._render_pdf()
        self.assertEqual(self.url_open("/school/report-card/%s/pdf" % self.card.id).status_code, 200)
        self.authenticate("rcf_mother", "rcf_mother")
        self.assertEqual(self.url_open("/school/report-card/%s/pdf" % self.card.id).status_code, 404)

    def _communication(self, **extra):
        values = {"student_id": self.alpha.id, "year_id": self.year.id, "kind": "other", "body": "Hello"}
        values.update(extra)
        return self.env["bf.school.communication"].with_user(self.office).create(values)

    def test_a_teacher_sends_only_the_communications_they_may_write(self):
        self.mother.partner_id.email = "m.rcf@example.invalid"
        item = self._communication()
        with self.assertRaises(AccessError):
            item.with_user(self.outsider).action_send_email()
        with self.assertRaises(AccessError):
            item.with_user(self.outsider).action_mark_sent()
        self.assertEqual(item.state, "to_send")

    def test_a_communication_cannot_be_moved_to_another_student(self):
        item = self.env["bf.school.communication"].with_user(self.homeroom).create({
            "student_id": self.alpha.id, "year_id": self.year.id, "kind": "other", "body": "Hello"})
        for field, value in (("student_id", self.bravo.id), ("year_id", self.year.id)):
            with self.assertRaises(UserError):
                item.with_user(self.homeroom).write({field: value})

    def test_a_teacher_who_no_longer_has_the_student_no_longer_reaches_their_communications(self):
        item = self._communication()
        self.assertTrue(item.with_user(self.homeroom).read(["body"]))
        self.env["bf.school.enrollment"].search([("student_id", "=", self.alpha.id)]).action_leave()
        with self.assertRaises(AccessError):
            item.with_user(self.homeroom).read(["body"])

    def test_the_year_buttons_that_prepare_communications_are_the_offices(self):
        with self.assertRaises(AccessError):
            self.year.with_user(self.homeroom).action_prepare_first_communications()
        with self.assertRaises(AccessError):
            self.year.with_user(self.homeroom).action_prepare_monthly_information()
        self.year.with_user(self.office).action_prepare_first_communications()

    def test_the_pdf_prints_what_was_sealed_not_what_the_records_say_now(self):
        self.card.with_user(self.office).action_issue()
        self.alpha.name = "Alpha Renamed"
        html, _kind = self.env["ir.actions.report"]._render_qweb_html(
            "bf_school_report_card.report_card_document", self.card.ids)
        self.assertIn("Alpha Essai", html.decode())
        self.assertNotIn("Alpha Renamed", html.decode())
        self.assertEqual(self.card.with_user(self.office).action_verify()["params"]["type"], "success",
                         "a name corrected since is not a tampering")

    def test_a_grade_corrected_after_the_draft_is_on_the_card_when_it_is_issued(self):
        self._grades(self.t1, self.alpha, pe=95)
        self.card.with_user(self.office).action_issue()
        pe = [l for l in json.loads(self.card.sudo().snapshot)["lines"] if l["subject"] == "Physical education and health"]
        self.assertEqual(pe[0]["result"], 95)

    def test_the_seal_covers_the_pdf_too(self):
        self.card.with_user(self.office).action_issue()
        self.card._render_pdf()
        self.assertEqual(self.card.with_user(self.office).action_verify()["params"]["type"], "success")
        self.card.sudo().pdf = base64.b64encode(b"%PDF-forged")
        self.assertEqual(self.card.with_user(self.office).action_verify()["params"]["type"], "danger")

    def test_a_student_who_changed_group_keeps_what_was_graded_before(self):
        other_group = self.env["bf.school.group"].create({
            "name": "302", "school_id": self.school.id, "year_id": self.year.id,
            "level_ids": [(6, 0, self.level3.ids)], "teacher_ids": [(6, 0, self.homeroom.ids)]})
        self.env["bf.school.enrollment"].search([("student_id", "=", self.alpha.id)]).action_leave()
        self.env["bf.school.enrollment"].create({"student_id": self.alpha.id, "group_id": other_group.id})
        self.env["bf.school.course"].create({
            "group_id": other_group.id, "subject_id": self.env.ref("bf_school_gradebook.subject_el_eps").id})
        self._generate(self.t1, other_group)
        card = self._card(self.alpha, self.t1)
        self.assertEqual(card.group_id, other_group)
        subjects = card.line_ids.mapped("subject_name")
        self.assertIn("Mathematics", subjects, "graded in the group the student left")
        self.assertEqual(self.env["bf.school.report.card"].sudo().search_count(
            [("student_id", "=", self.alpha.id), ("term_id", "=", self.t1.id)]), 1)

    def test_a_course_with_an_exam_not_yet_marked_gives_a_provisional_final(self):
        exam_course = self.env["bf.school.course"].create({
            "group_id": self.group.id, "subject_id": self.env.ref("bf_school_gradebook.subject_el_ccq").id,
            "ministerial_exam": True, "units": 4})
        assessment = self.env["bf.school.assessment"].create(
            {"name": "E", "course_id": exam_course.id, "term_id": self.t3.id})
        assessment.result_ids.filtered(lambda r: r.student_id == self.alpha).write({"score": 80.0})
        self.env["bf.school.exam.result"].create({
            "course_id": exam_course.id, "student_id": self.alpha.id, "state": "taken", "exam_year": 2027, "exam_month": 6})
        self._generate(self.t3)
        card = self._card(self.alpha, self.t3)
        line = card.line_ids.filtered(lambda l: l.course_id == exam_course)
        self.assertEqual((line.has_final, line.final_percent, line.provisional, line.outcome, line.units_earned),
                         (True, 80, True, False, 0))
        card.with_user(self.office).write({"decision": "promoted"})
        card.with_user(self.office).action_issue()
        html, _kind = self.env["ir.actions.report"]._render_qweb_html(
            "bf_school_report_card.report_card_document", card.ids)
        self.assertIn("Provisional", html.decode())

    def test_monthly_information_follows_the_latest_graded_term_of_each_course(self):
        # failing in term 1; term 3 (cumulative) exists but has no grade yet for this student's math
        self.math_course.third_term_basis = "year"
        a = self.env["bf.school.assessment"].create({
            "name": "P", "course_id": self.math_course.id, "term_id": self.t1.id,
            "component_id": self.solve.id})
        a.result_ids.filtered(lambda r: r.student_id == self.bravo).write({"score": 40.0})
        created = self.env["bf.school.communication"]._prepare_monthly_performance(self.year)
        self.assertEqual(created.student_id, self.bravo)
        self.assertIn("Mathematics", created.body)


@tagged("post_install", "-at_install")
class TestIsolationAndLimits(ReportCardCase):
    """Company isolation and limits of the report card, each as it was found."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.other_company = env["res.company"].create({"name": "Autre école inc."})
        cls.stranger_office = new_test_user(
            env, login="rc2_stranger", groups="bf_school_core.group_school_manager",
            company_id=cls.other_company.id, company_ids=[(6, 0, cls.other_company.ids)])
        cls.mother = new_test_user(env, login="rc2_mother", name="Maman Essai", email="m.rc2@example.invalid",
                                   groups="base.group_portal")
        cls.grandma = new_test_user(env, login="rc2_grandma", name="Mamie Essai", email="mamie.rc2@example.invalid",
                                    groups="base.group_portal")
        Link = env["bf.school.guardian.link"]
        Link.create({"student_id": cls.alpha.id, "guardian_id": cls.mother.partner_id.id, "relationship": "mother"})
        Link.create({"student_id": cls.alpha.id, "guardian_id": cls.grandma.partner_id.id,
                     "relationship": "grandparent"})
        cls._grades(cls, cls.t1, cls.alpha, pe=80, solve=60, reason=80)
        cls._generate(cls, cls.t1)
        cls.card = cls._card(cls, cls.alpha, cls.t1)

    # --- helpers ---------------------------------------------------------------------------------

    def _as_stranger(self, record):
        return record.with_user(self.stranger_office).with_context(allowed_company_ids=self.other_company.ids)

    def _today(self):
        return fields.Date.context_today(self.card)

    def _shown(self, card=None):
        card = card or self.card
        card.with_user(self.office).action_issue()
        card.with_user(self.office).action_publish()
        card._render_pdf()
        return card

    def _corrected_and_issued(self, card=None):
        card = card or self.card
        action = card.with_user(self.office).with_context(correction_reason="A grade was corrected").action_correct()
        new = self.env["bf.school.report.card"].browse(action["res_id"])
        new.with_user(self.office).action_issue()
        return new

    def _pdf_attachment(self, card=None):
        card = card or self.card
        return self.env["ir.attachment"].sudo().search([
            ("res_model", "=", card._name), ("res_id", "=", card.id), ("res_field", "=", "pdf")])

    def _log(self, action):
        return self.env["bf.school.record.log"].search_count([("action", "=", action)])

    @contextmanager
    def no_read_only_retry(self):
        """No "bad query ... read-only transaction" error and no "retrying with a read/write cursor" warning."""
        seen = []

        class Catcher(logging.Handler):
            def emit(self, record):
                seen.append(record.getMessage())

        handler, loggers = Catcher(level=logging.WARNING), [logging.getLogger(n) for n in ("odoo.sql_db", "odoo.http")]
        for logger in loggers:
            logger.addHandler(handler)
        try:
            yield
        finally:
            for logger in loggers:
                logger.removeHandler(handler)
        self.assertFalse([m for m in seen if "read-only" in m or "retrying" in m], seen)

    # --- A1 = C2 : the company's rule on the lines ---------------------------------------------

    def test_the_office_of_another_company_reads_no_line_nor_component(self):
        self.assertTrue(self.card.line_ids.component_ids)
        for model in ("bf.school.report.card.line", "bf.school.report.card.component"):
            self.assertFalse(self._as_stranger(self.env[model]).search([]), model)
        self.assertFalse(self._as_stranger(self.env["bf.school.report.card.line"]).search_read(
            [], ["percent", "comment", "teacher_names"]))
        self.assertTrue(self.env["bf.school.report.card.line"].with_user(self.office).search([]),
                        "the office of the company reads them")

    # --- A2 = C1 : issue, show, render, correct, generate and prepare, as the user ----------------

    def test_the_office_of_another_company_issues_shows_renders_and_corrects_nothing(self):
        with self.assertRaises(AccessError):
            self._as_stranger(self.card).action_issue()
        self.assertEqual(self.card.state, "draft")
        self.card.with_user(self.office).action_issue()
        # What the superuser read in the same transaction is in the cache the user's environment shares.
        self.card.sudo().read()
        for call in ("action_publish", "action_render_pdf", "action_verify"):
            with self.assertRaises(AccessError, msg=call):
                getattr(self._as_stranger(self.card), call)()
        with self.assertRaises(AccessError):
            self._as_stranger(self.card).with_context(correction_reason="x").action_correct()
        self.assertFalse(self.card.published_on)
        self.assertFalse(self.card.pdf_sha256)
        self.assertEqual(self.card.issued_by_id, self.office)

    def test_the_group_the_term_and_the_year_of_another_company_are_refused(self):
        Card, Communication = self.env["bf.school.report.card"], self.env["bf.school.communication"]
        cards = Card.sudo().search_count([])
        wizard = self.env["bf.school.report.card.generate"].with_user(self.stranger_office).with_context(
            allowed_company_ids=self.other_company.ids).create({"group_id": self.group.id, "term_id": self.t2.id})
        with self.assertRaises(AccessError):
            wizard.action_generate()
        with self.assertRaises(AccessError):
            self._as_stranger(Card)._generate(self.group, self.t2)
        year = self._as_stranger(self.year)
        for call in ("action_prepare_first_communications", "action_prepare_monthly_information"):
            with self.assertRaises(AccessError, msg=call):
                getattr(year, call)()
        self.assertEqual(Card.sudo().search_count([]), cards)
        self.assertFalse(Communication.sudo().search([("year_id", "=", self.year.id)]))

    # --- C3 : the file that leaves is the one that was sealed ----------------------------------

    def test_a_pdf_replaced_behind_the_application_is_not_given_out_and_the_log_says_so(self):
        card = self._shown()
        card.sudo().pdf = base64.b64encode(b"%PDF-1.4 FORGED")  # as code under the superuser, or the database
        before = self._log("seal_mismatch")
        for login in ("rc2_mother", "rc_office", "rc_homeroom"):
            self.authenticate(login, login)
            response = self.url_open("/school/report-card/%s/pdf" % card.id)
            self.assertEqual(response.status_code, 409, login)
            self.assertNotIn(b"FORGED", response.content)
        self.assertEqual(self._log("seal_mismatch"), before + 3)
        self.assertEqual(card.read_ids.guardian_id.ids, [], "a refused file is no receipt")
        # the standard route serves nothing either, and the line of the refusal stays
        self.authenticate("rc_office", "rc_office")
        self.assertEqual(self.url_open("/web/content/bf.school.report.card/%s/pdf" % card.id).status_code, 404)
        self.assertEqual(self._log("seal_mismatch"), before + 4)
        # the office renders it again from the sealed content: it is given out again
        card.with_user(self.office).action_render_pdf()
        self.assertEqual(self.url_open("/school/report-card/%s/pdf" % card.id).status_code, 200)

    def test_the_attachment_of_the_pdf_is_not_replaced_nor_deleted_by_hand(self):
        card = self._shown()
        attachment = self._pdf_attachment(card)
        self.assertTrue(attachment)
        with self.assertRaises(AccessError):
            attachment.with_user(self.office).write({"datas": base64.b64encode(b"%PDF forged")})
        with self.assertRaises(AccessError):
            attachment.with_user(self.office).unlink()
        with self.assertRaises(AccessError):
            self.env["ir.attachment"].with_user(self.office).create({
                "name": "second", "res_model": card._name, "res_id": card.id, "res_field": "pdf",
                "datas": base64.b64encode(b"%PDF forged")})
        self.assertTrue(attachment.exists())

    def test_rendering_again_leaves_the_fingerprint_it_replaces(self):
        card = self._shown()
        before = card.pdf_sha256
        self.assertTrue(before)
        lines = self._log("piece_replaced")
        card.with_user(self.office).action_render_pdf()
        notes = card.message_ids.filtered(lambda m: "rendered again" in (m.body or ""))
        self.assertEqual(len(notes), 1)
        self.assertIn(before, notes.body)
        self.assertIn(card.pdf_sha256, notes.body)
        self.assertEqual(self._log("piece_replaced"), lines + 1)
        # the file lost behind the application, rendered again by the cron: same trace, the old fingerprint kept
        lost = card.pdf_sha256
        self._pdf_attachment(card).sudo().unlink()
        card.invalidate_recordset()
        self.env["bf.school.report.card"]._cron_render_pdfs()
        notes = card.message_ids.filtered(lambda m: "rendered again" in (m.body or ""))
        self.assertEqual(len(notes), 2)
        self.assertTrue(any(lost in (note.body or "") for note in notes))

    # --- C4 / C13 : the standard routes of the PDF, the headers -------------------------------

    def test_the_pdf_leaves_by_the_standard_route_with_a_line_in_the_log_and_by_the_office_only(self):
        card = self._shown()
        self.authenticate("rc_homeroom", "rc_homeroom")
        self.assertEqual(self.url_open("/web/content/bf.school.report.card/%s/pdf" % card.id).status_code, 404)
        self.authenticate("rc_office", "rc_office")
        before = self._log("piece_downloaded")
        with self.no_read_only_retry():
            response = self.url_open("/web/content/bf.school.report.card/%s/pdf?download=true" % card.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._log("piece_downloaded"), before + 1)

    def test_the_route_of_the_pdf_logs_each_download_and_is_never_kept_by_a_cache(self):
        card = self._shown()
        before = self._log("piece_downloaded")
        for login in ("rc2_mother", "rc_homeroom"):
            self.authenticate(login, login)
            response = self.url_open("/school/report-card/%s/pdf" % card.id)
            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response.headers.get("Cache-Control", ""), login)
        self.assertEqual(self._log("piece_downloaded"), before + 2)

    def test_a_page_reloaded_on_a_pdf_not_ready_adds_no_trigger_row(self):
        cron = self.env.ref("bf_school_report_card.ir_cron_render_pdfs")
        cron.sudo().active = True  # an inactive cron keeps no trigger (the bench switches them off)
        self.card.with_user(self.office).action_issue()
        self.card.with_user(self.office).action_publish()
        Trigger = self.env["ir.cron.trigger"].sudo()
        self.authenticate("rc2_mother", "rc2_mother")
        response = self.url_open("/school/report-card/%s/pdf" % self.card.id)
        self.assertEqual(response.status_code, 202)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        waiting = Trigger.search_count([("cron_id", "=", cron.id)])
        self.assertTrue(waiting)
        for _i in range(3):
            self.url_open("/school/report-card/%s/pdf" % self.card.id)
        self.assertEqual(Trigger.search_count([("cron_id", "=", cron.id)]), waiting)

    # --- C6 : the replaced versions stay in the exports ------------------------------------------

    def test_the_exports_keep_the_replaced_versions_and_their_receipts(self):
        card = self._shown()
        self.authenticate("rc2_mother", "rc2_mother")
        self.assertEqual(self.url_open("/school/report-card/%s/pdf" % card.id).status_code, 200)
        new = self._corrected_and_issued(card)
        self.assertEqual((card.state, new.state), ("superseded", "issued"))
        record = self.env["bf.school.record"].search([("student_id", "=", self.alpha.id)])
        payload = record._portable_payload()
        self.assertEqual(sorted((c["version"], c["status"]) for c in payload["report_cards"]),
                         [(1, "replaced"), (2, "current")])
        first = [c for c in payload["report_cards"] if c["version"] == 1][0]
        self.assertEqual([r["read_by"] for r in first["read_receipts"]], ["Maman Essai"])
        self.assertTrue(first["seal_sha256"])
        transfer = self.env["bf.school.record.transfer"].with_user(self.office).create(
            {"record_id": record.id, "destination_name": "Destination"})
        transfer.with_user(self.office).action_prepare()
        archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(transfer.sudo().package)))
        sent = json.loads(archive.read("transfer.json"))["report_cards"]
        self.assertEqual(sorted(c["version"] for c in sent), [1, 2])
        self.assertTrue(all("read_receipts" not in c for c in sent), "who read it is not the receiving school's")
        # and the closure of the school, which holds the same copy of each file
        self.authenticate("rc_office", "rc_office")
        response = self.url_open(self.school.with_user(self.office).action_export_closure()["url"])
        self.assertEqual(response.status_code, 200)
        closure = zipfile.ZipFile(io.BytesIO(response.content))
        name = [n for n in closure.namelist() if n.endswith("/record.json") and n.startswith("files/%s-" % self.alpha.id)]
        self.assertEqual(len(name), 1)
        self.assertEqual(sorted(c["version"] for c in json.loads(closure.read(name[0]))["report_cards"]), [1, 2])

    # --- C7 + A8 : the teacher of one subject and the header of the card -----------------------

    def test_a_teacher_of_one_subject_reads_no_header_no_draft_no_version_and_no_thread(self):
        self.card.with_user(self.office).message_post(body="The homeroom teacher's note")
        specialist = self.card.with_user(self.math_teacher)
        for fields_ in (["comment"], ["decision", "decision_note"], ["message_ids"], ["state"]):
            with self.assertRaises(AccessError, msg=fields_):
                specialist.read(fields_)
        self.assertFalse(self.env["mail.message"].with_user(self.math_teacher).search(
            [("model", "=", self.card._name), ("res_id", "=", self.card.id)]))
        # the lines of their subject stay theirs to read and to comment, on a draft only
        line = self.env["bf.school.report.card.line"].with_user(self.math_teacher).search(
            [("student_id", "=", self.alpha.id)])
        self.assertEqual(line.subject_name, "Mathematics")
        # the screen of their comments loads and reads for them (the card behind each line is not theirs to read)
        Line = self.env["bf.school.report.card.line"].with_user(self.math_teacher)
        self.assertTrue(Line.get_view(view_type="list")["arch"])
        rows = Line.web_search_read([], {
            "student_id": {"fields": {"display_name": {}}}, "term_id": {"fields": {"display_name": {}}},
            "subject_name": {}, "state": {}, "percent": {}, "group_average": {}, "comment": {}})["records"]
        self.assertEqual({row["subject_name"] for row in rows}, {"Mathematics"})
        line.write({"comment": "Careful work"})
        self.card.with_user(self.office).action_issue()
        self.env.invalidate_all()  # as a request of its own: nothing the superuser read is in the cache
        line = Line.search([("student_id", "=", self.alpha.id)])  # issuing wrote the lines again
        self.assertEqual(line.subject_name, "Mathematics")
        with self.assertRaisesRegex(UserError, "frozen"):  # the card is frozen, not "you may not read it"
            line.write({"comment": "Late"})
        # while the group's teacher reads the whole card
        self.assertTrue(self.card.with_user(self.homeroom).read(["comment", "decision"]))

    # --- C8, A11 : who follows and who may post ----------------------------------------------------

    def _communication(self, **extra):
        values = {"student_id": self.alpha.id, "year_id": self.year.id, "kind": "other", "body": "Hello"}
        values.update(extra)
        return self.env["bf.school.communication"].with_user(self.office).create(values)

    def test_nobody_outside_the_staff_follows_a_report_card_or_a_communication_nor_hears_its_thread(self):
        item = self._communication()
        for record in (self.card, item):
            office = record.with_user(self.office)
            with self.assertRaises(AccessError, msg=record._name):
                office.message_subscribe(partner_ids=self.grandma.partner_id.ids)
            with self.assertRaises(AccessError, msg=record._name):
                office.message_post(body="x", partner_ids=self.grandma.partner_id.ids,
                                    message_type="comment", subtype_xmlid="mail.mt_comment")
            office.message_subscribe(partner_ids=self.homeroom.partner_id.ids)
            self.assertEqual(record.message_partner_ids & (self.grandma | self.homeroom).partner_id,
                             self.homeroom.partner_id)
            office.message_post(body="A note for the team", message_type="comment", subtype_xmlid="mail.mt_comment")
            self.assertFalse(self.env["mail.message"].with_user(self.grandma).search(
                [("model", "=", record._name), ("res_id", "=", record.id)]))

    def test_who_may_not_write_a_report_card_does_not_follow_it_nor_post_on_it(self):
        colleague = new_test_user(self.env, login="rc2_colleague", groups="bf_school_core.group_school_user")
        self.group.teacher_ids = [(4, colleague.id)]
        self.card.with_user(self.office).action_issue()
        mine = self.card.with_user(colleague)
        self.assertTrue(mine.read(["state"]), "the group's teacher reads it")
        with self.assertRaises(AccessError):
            mine.message_subscribe(partner_ids=colleague.partner_id.ids)
        with self.assertRaises(AccessError):
            mine.message_post(body="Issued after the deadline of 2026-11-20.", subtype_xmlid="mail.mt_note")
        self.assertNotIn(colleague.partner_id, self.card.message_partner_ids)

    # --- C10 : the trail of the thread is not edited ------------------------------------------------

    def test_the_messages_of_a_report_card_and_of_a_communication_are_not_edited_nor_deleted(self):
        item = self._communication()
        for record in (self.card, item):
            message = record.with_user(self.office).message_post(body="A note", subtype_xmlid="mail.mt_note")
            with self.assertRaises(AccessError, msg=record._name):
                message.with_user(self.office).write({"body": "<p>falsified</p>"})
            with self.assertRaises(AccessError, msg=record._name):
                message.with_user(self.office).unlink()
            self.assertIn("A note", message.body)
        # the marks a reader puts on a message are theirs: a star is not the message
        message.with_user(self.office).toggle_message_starred()
        self.assertTrue(message.with_user(self.office).starred)

    def test_the_chatters_own_edit_and_delete_of_a_message_are_refused_on_these_threads(self):
        # The web client's route runs the update as the superuser once it has seen that the author edits it.
        item = self._communication()
        self.authenticate("rc_office", "rc_office")
        for record in (self.card, item):
            message = record.with_user(self.office).message_post(
                body="A note", message_type="comment", subtype_xmlid="mail.mt_note")
            with self.assertRaises(Exception):
                self.make_jsonrpc_request("/mail/message/update_content", {
                    "message_id": message.id, "body": "<p>falsified</p>", "attachment_ids": []})
            with self.assertRaises(Exception):
                self.make_jsonrpc_request("/mail/message/update_content", {
                    "message_id": message.id, "body": "", "attachment_ids": []})
            self.assertEqual(message.sudo().body, "<p>A note</p>", record._name)

    # --- C9 : sent means queued, and who was told stays --------------------------------------------

    def _family_with_an_email(self):
        adult = self.env["res.partner"].create({"name": "Maman Courriel", "email": "maman.courriel@example.invalid"})
        self.env["bf.school.guardian.link"].create(
            {"student_id": self.alpha.id, "guardian_id": adult.id, "relationship": "mother"})
        return adult

    def test_a_communication_keeps_who_was_told_and_never_the_text(self):
        adult = self._family_with_an_email()
        item = self._communication(body="TEXT-OF-THE-MESSAGE", subject="A note")
        item.with_user(self.office).action_send_email()
        self.assertEqual(item.state, "sent")
        self.assertIn(adult, item.recipient_ids)
        self.assertNotIn(self.grandma.partner_id, item.recipient_ids, "no authority, not told")
        notes = item.message_ids.filtered(lambda m: "Maman Courriel" in (m.body or ""))
        self.assertEqual(len(notes), 1)
        self.assertNotIn("TEXT-OF-THE-MESSAGE", notes.body)
        with self.assertRaises(AccessError):
            item.with_user(self.office).write({"recipient_ids": [(6, 0, [])]})
        with self.assertRaises(AccessError):
            self._communication(recipient_ids=[(6, 0, adult.ids)])
        # the web client sends the screen's defaults as they are: an empty value forges nothing
        self.assertTrue(self._communication(recipient_ids=[(6, 0, [])]))

    def test_the_subject_of_a_communication_is_one_line(self):
        with self.assertRaises(ValidationError):
            self._communication(subject="Hello\r\nBcc: attacker@evil.invalid")
        item = self._communication()
        with self.assertRaises(ValidationError):
            item.with_user(self.office).write({"subject": "a\nb"})

    def test_a_communication_the_queue_did_not_take_is_not_sent(self):
        self._family_with_an_email()
        item = self._communication()
        template = type(self.env["mail.template"])
        with patch.object(template, "send_mail", lambda *args, **kwargs: False):
            with self.assertRaises(UserError):
                item.with_user(self.office).action_send_email()
        self.assertEqual((item.state, item.recipient_ids.ids), ("to_send", []))
        self.alpha.name = "Alpha\nEssai"
        with self.assertRaises(UserError):
            item.with_user(self.office).action_send_email()
        self.assertEqual(item.state, "to_send")

    def test_showing_a_report_card_keeps_who_was_told_without_the_text(self):
        adult = self._family_with_an_email()
        card = self.card
        card.with_user(self.office).action_issue()
        card.with_user(self.office).action_publish()
        notes = card.message_ids.filtered(lambda m: adult.name in (m.body or ""))
        self.assertEqual(len(notes), 1)

    # --- B17 : a correction is not late for a deadline the first version met -----------------------

    def test_a_correction_after_the_deadline_is_not_late_when_the_first_version_was_on_time(self):
        self.t1.report_deadline = self._today() + timedelta(days=10)
        card = self._shown()
        self.assertEqual((card.issued_late, card.published_late), (False, False))
        self.t1.report_deadline = self._today() - timedelta(days=3)
        new = self._corrected_and_issued(card)
        self.assertFalse(new.issued_late)
        new.with_user(self.office).action_publish()
        self.assertFalse(new.published_late)
        for record in (card, new):
            self.assertFalse(record.message_ids.filtered(lambda m: "after the deadline" in (m.body or "")))

    def test_a_correction_of_a_report_card_that_was_late_stays_late_and_says_it_once(self):
        self.t1.report_deadline = self._today() - timedelta(days=3)
        card = self._shown()
        self.assertEqual((card.issued_late, card.published_late), (True, True))
        new = self._corrected_and_issued(card)
        self.assertTrue(new.issued_late)
        new.with_user(self.office).action_publish()
        self.assertTrue(new.published_late)
        notes = (card | new).mapped("message_ids").filtered(lambda m: "after the deadline" in (m.body or ""))
        self.assertEqual(len(notes), 1)

    def test_a_correction_shown_for_the_first_time_after_the_deadline_is_late(self):
        self.t1.report_deadline = self._today() + timedelta(days=10)
        self.card.with_user(self.office).action_issue()  # issued, never shown
        self.t1.report_deadline = self._today() - timedelta(days=3)
        new = self._corrected_and_issued(self.card)
        self.assertFalse(new.issued_late, "the first version was issued on time")
        new.with_user(self.office).action_publish()
        self.assertTrue(new.published_late, "the families had nothing before it")

    def test_the_family_keeps_the_report_card_it_had_until_the_corrected_one_is_shown(self):
        card = self._shown()
        new = self._corrected_and_issued(card)
        new._render_pdf()
        self.authenticate("rc2_mother", "rc2_mother")
        page = self.url_open("/my/school/report-cards").text
        self.assertIn("/school/report-card/%s/pdf" % card.id, page)
        self.assertNotIn("/school/report-card/%s/pdf" % new.id, page)
        self.assertEqual(self.url_open("/school/report-card/%s/pdf" % card.id).status_code, 200)
        self.assertEqual(self.url_open("/school/report-card/%s/pdf" % new.id).status_code, 404)
        new.with_user(self.office).action_publish()
        page = self.url_open("/my/school/report-cards").text
        self.assertIn("/school/report-card/%s/pdf" % new.id, page)
        self.assertNotIn("/school/report-card/%s/pdf" % card.id, page)

    def test_showing_a_correction_says_it_is_a_correction(self):
        card = self._shown()
        new = self._corrected_and_issued(card)
        Template = type(self.env["mail.template"])
        real, used = Template.send_mail, []

        def spy(template, *args, **kwargs):
            used.append(template.id)
            return real(template, *args, **kwargs)

        with patch.object(Template, "send_mail", spy):
            new.with_user(self.office).action_publish()
        self.assertEqual(used, [self.env.ref("bf_school_report_card.mail_template_report_card_correction").id])

    # --- A7 : the receipts page is the office's -------------------------------------------------

    def test_the_read_receipts_are_the_offices_on_the_screen(self):
        Card = self.env["bf.school.report.card"]
        self.assertNotIn("read_ids", Card.with_user(self.homeroom).get_view(view_type="form")["arch"])
        self.assertIn("read_ids", Card.with_user(self.office).get_view(view_type="form")["arch"])
