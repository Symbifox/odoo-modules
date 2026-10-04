import re
from datetime import date, datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSchoolWork(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.school = env["bf.school"].create({"name": "École des Essais"})
        year = env["bf.school.year"].create({"name": "2026-2027", "school_id": cls.school.id,
                                             "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        year.action_set_current()
        cls.t1 = new_test_user(env, login="school_wk_t1", groups="bf_school_core.group_school_user")
        cls.t2 = new_test_user(env, login="school_wk_t2", groups="bf_school_core.group_school_user")
        Group = env["bf.school.group"]
        cls.g1 = Group.create({"name": "301", "school_id": cls.school.id, "year_id": year.id,
                               "teacher_ids": [(6, 0, cls.t1.ids)]})
        cls.g2 = Group.create({"name": "302", "school_id": cls.school.id, "year_id": year.id,
                               "teacher_ids": [(6, 0, cls.t2.ids)]})
        Partner = env["res.partner"]
        cls.a = Partner.create({"name": "Alpha Essai", "is_student": True})
        cls.b = Partner.create({"name": "Bravo Essai", "is_student": True})
        cls.c = Partner.create({"name": "Charlie Essai", "is_student": True})
        Enrollment = env["bf.school.enrollment"]
        Enrollment.create({"student_id": cls.a.id, "group_id": cls.g1.id})
        Enrollment.create({"student_id": cls.c.id, "group_id": cls.g1.id})
        Enrollment.create({"student_id": cls.b.id, "group_id": cls.g2.id})
        # p: parent of Alpha with authority; n: receives notices only; q: parent of Bravo.
        cls.p = new_test_user(env, login="school_wk_p", groups="base.group_portal")
        cls.n = new_test_user(env, login="school_wk_n", groups="base.group_portal")
        cls.q = new_test_user(env, login="school_wk_q", groups="base.group_portal")
        Link = env["bf.school.guardian.link"]
        Link.create({"student_id": cls.a.id, "guardian_id": cls.p.partner_id.id})
        Link.create({"student_id": cls.a.id, "guardian_id": cls.n.partner_id.id,
                     "relationship": "other", "has_parental_authority": False,
                     "receives_notices": True})
        Link.create({"student_id": cls.b.id, "guardian_id": cls.q.partner_id.id})
        # Charlie has their own portal account (bf_school_student_portal gives it from the directory).
        cls.cu = env["res.users"].with_context(no_reset_password=True).create({
            "login": "school_wk_charlie", "password": "school_wk_charlie",
            "partner_id": cls.c.id, "groups_id": [(6, 0, env.ref("base.group_portal").ids)]})
        cls.today = fields.Date.context_today(env["res.partner"])

    def _hw(self, group=None, user=None, **vals):
        Homework = self.env["bf.school.homework"]
        if user:
            Homework = Homework.with_user(user)
        return Homework.create(dict({
            "name": "Rédaction : mon été", "group_id": (group or self.g1).id,
            "date_due": self.today + timedelta(days=3), "submission_mode": "file",
            "points_total": 20}, **vals))

    def _sub(self, homework, student=None):
        return homework.submission_ids.filtered(lambda s: s.student_id == (student or self.a))

    def _post_files(self, homework, student, files):
        return self.url_open(
            "/my/school/work/%s/%s/hand-in" % (homework.id, student.id),
            data={"csrf_token": self.csrf_token()}, files=[("files", f) for f in files])

    def csrf_token(self):
        page = self.url_open("/my").text
        match = re.search(r'name="csrf_token" value="([^"]+)"', page) or re.search(
            r'csrf_token["\']?\s*:\s*["\']([^"\']+)', page)
        return match.group(1)

    # ---------------------------------------------------------------- model

    def test_hand_ins_prepared_for_the_group(self):
        homework = self._hw(user=self.t1)
        self.assertEqual(homework.submission_ids.student_id, self.a | self.c)
        self.assertEqual(set(homework.submission_ids.mapped("state")), {"todo"})
        self.assertFalse(self._hw(submission_mode="none").submission_ids)

    def test_teacher_cannot_write_system_fields(self):
        sub = self._sub(self._hw(user=self.t1))
        for vals in ({"state": "returned"}, {"submitted_on": fields.Datetime.now()},
                     {"is_late": True}, {"student_id": self.c.id}):
            with self.assertRaises(AccessError):
                sub.with_user(self.t1).write(vals)
        sub.with_user(self.t1).write({"grade": 15, "feedback": "Belle structure."})
        self.assertEqual(sub.grade, 15)

    def test_mark_within_total(self):
        sub = self._sub(self._hw())
        with self.assertRaises(ValidationError):
            sub.grade = 21
        with self.assertRaises(ValidationError):
            sub.grade = -1

    def test_other_teacher_cannot_see_nor_return(self):
        sub = self._sub(self._hw())
        with self.assertRaises(AccessError):
            sub.with_user(self.t2).read(["grade"])
        with self.assertRaises(AccessError):
            sub.with_user(self.t2).action_return()
        sub.with_user(self.t1).action_return()
        self.assertEqual(sub.state, "returned")
        self.assertEqual(sub.returned_by_id, self.t1)

    def test_quiz_needs_a_survey(self):
        with self.assertRaises(ValidationError):
            self._hw(submission_mode="survey")

    def test_late_is_the_end_of_the_due_day_at_school(self):
        homework = self._hw(date_due=date(2026, 10, 7))
        self.school.company_id.partner_id.tz = "America/Toronto"
        # 23:59 on the due day in Québec is 03:59 UTC the next day: still on time.
        self.assertEqual(homework._school_due_moment(), datetime(2026, 10, 8, 4, 0))

    # ---------------------------------------------------------------- portal

    def test_parent_hands_in_and_replaces(self):
        homework = self._hw()
        self.authenticate("school_wk_p", "school_wk_p")
        self.assertIn("Rédaction : mon été", self.url_open("/my/school/work").text)
        response = self._post_files(homework, self.a, [("ete.pdf", b"%PDF-1.4 v1", "application/pdf")])
        self.assertEqual(response.status_code, 200)
        sub = self._sub(homework)
        self.assertEqual(sub.state, "submitted")
        self.assertEqual(sub.submitted_by_id, self.p.partner_id)
        self.assertFalse(sub.is_late)
        first = sub.attachment_ids
        self._post_files(homework, self.a, [("ete-v2.docx", b"v2", "application/octet-stream"),
                                            ("dessin.png", b"\x89PNG v2", "image/png")])
        self.assertEqual(sorted(sub.attachment_ids.mapped("name")), ["dessin.png", "ete-v2.docx"])
        self.assertFalse(first.exists(), "a replaced file is gone")

    def test_student_own_account(self):
        homework = self._hw()
        self.authenticate("school_wk_charlie", "school_wk_charlie")
        page = self.url_open("/my/school/work").text
        self.assertIn("Charlie Essai", page)
        self.assertNotIn("Alpha Essai", page)
        self._post_files(homework, self.c, [("charlie.pdf", b"%PDF c", "application/pdf")])
        self.assertEqual(self._sub(homework, self.c).submitted_by_id, self.c)
        self.assertEqual(self.url_open("/my/school/work/%s/%s" % (homework.id, self.a.id)).status_code, 404)
        # The student's own homework list and calendar.
        self.assertIn("Rédaction : mon été", self.url_open("/my/school/homework").text)

    def test_notices_only_adult_sees_no_work(self):
        homework = self._hw()
        self.authenticate("school_wk_n", "school_wk_n")
        self.assertNotIn("Rédaction", self.url_open("/my/school/work").text)
        self.assertEqual(self.url_open("/my/school/work/%s/%s" % (homework.id, self.a.id)).status_code, 404)
        self._post_files(homework, self.a, [("x.pdf", b"%PDF x", "application/pdf")])
        self.assertEqual(self._sub(homework).state, "todo")
        # The homework list still shows the homework, without the link to the hand-in.
        page = self.url_open("/my/school/homework").text
        self.assertIn("Rédaction : mon été", page)
        self.assertNotIn("/my/school/work/%s/" % homework.id, page)

    def test_other_family_refused(self):
        homework = self._hw()
        self._sub(homework).sudo()._school_hand_in([("a.pdf", b"%PDF a")], self.p.partner_id)
        file_id = self._sub(homework).attachment_ids.id
        self.authenticate("school_wk_q", "school_wk_q")
        for url in ("/my/school/work/%s/%s" % (homework.id, self.a.id),
                    "/my/school/work/%s/%s/file/%s" % (homework.id, self.a.id, file_id),
                    "/my/school/work/%s/%s/file/%s" % (homework.id, self.b.id, file_id)):
            self.assertEqual(self.url_open(url).status_code, 404, url)

    def test_files_checked(self):
        homework = self._hw()
        sub = self._sub(homework)
        with self.assertRaises(UserError):
            sub._school_hand_in([("page.html", b"<script>")], self.p.partner_id)
        with self.assertRaises(UserError):
            sub._school_hand_in([("f%s.pdf" % i, b"%PDF") for i in range(6)], self.p.partner_id)
        with self.assertRaises(UserError):
            sub._school_hand_in([("vide.pdf", b"")], self.p.partner_id)
        with patch("odoo.addons.bf_school_work.models.submission.MAX_FILE_SIZE", 3):
            with self.assertRaises(UserError):
                sub._school_hand_in([("gros.pdf", b"%PDF")], self.p.partner_id)
        self.assertEqual(sub.state, "todo")

    def test_late_accepted_or_refused(self):
        late_ok = self._hw(date_assigned=self.today - timedelta(days=5), date_due=self.today - timedelta(days=2))
        sub = self._sub(late_ok)
        sub._school_hand_in([("tard.pdf", b"%PDF")], self.p.partner_id)
        self.assertTrue(sub.is_late)
        refused = self._hw(date_assigned=self.today - timedelta(days=5),
                           date_due=self.today - timedelta(days=2), accept_late=False)
        with self.assertRaises(UserError):
            self._sub(refused)._school_hand_in([("tard.pdf", b"%PDF")], self.p.partner_id)

    def test_mark_shown_only_once_returned(self):
        homework = self._hw()
        sub = self._sub(homework)
        sub._school_hand_in([("ete.pdf", b"%PDF")], self.p.partner_id)
        correction = self.env["ir.attachment"].create({
            "name": "ete-corrige.pdf", "raw": b"%PDF corrige",
            "res_model": sub._name, "res_id": sub.id})
        sub.with_user(self.t1).write({"grade": 17.5, "feedback": "Bon travail, attention aux accords.",
                                      "correction_ids": [(6, 0, correction.ids)]})
        self.authenticate("school_wk_p", "school_wk_p")
        url = "/my/school/work/%s/%s" % (homework.id, self.a.id)
        page = self.url_open(url).text
        self.assertNotIn("17.5", page)
        self.assertNotIn("attention aux accords", page)
        self.assertEqual(self.url_open("%s/file/%s" % (url, correction.id)).status_code, 404)
        sub.with_user(self.t1).action_return()
        page = self.url_open(url).text
        self.assertIn("17.5", page)
        self.assertIn("attention aux accords", page)
        response = self.url_open("%s/file/%s" % (url, correction.id))
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers.get("Content-Disposition", ""))
        # Returned: the family can no longer replace the work.
        self._post_files(homework, self.a, [("apres.pdf", b"%PDF", "application/pdf")])
        self.assertEqual(sub.attachment_ids.mapped("name"), ["ete.pdf"])
        sub.with_user(self.t1).action_reopen()
        self.assertEqual(sub.state, "submitted")
        self.assertNotIn("17.5", self.url_open(url).text)

    def test_teacher_documents_served_to_the_group_only(self):
        doc = self.env["ir.attachment"].create({"name": "consignes.pdf", "raw": b"%PDF consignes",
                                                "res_model": "bf.school.homework"})
        homework = self._hw(submission_mode="none", material_ids=[(6, 0, doc.ids)])
        url = "/my/school/work/%s/%s/file/%s" % (homework.id, self.a.id, doc.id)
        self.authenticate("school_wk_p", "school_wk_p")
        self.assertEqual(self.url_open(url).content, b"%PDF consignes")
        self.authenticate("school_wk_q", "school_wk_q")
        self.assertEqual(self.url_open(url).status_code, 404)
        self.assertEqual(self.url_open(
            "/my/school/work/%s/%s/file/%s" % (homework.id, self.b.id, doc.id)).status_code, 404)

    def test_student_who_left_the_group(self):
        homework = self._hw()
        self.a.student_enrollment_ids.filtered(lambda e: e.group_id == self.g1).state = "left"
        with self.assertRaises(UserError):
            self._sub(homework)._school_hand_in([("a.pdf", b"%PDF")], self.p.partner_id)

    # ---------------------------------------------------------------- quiz

    def _survey(self):
        survey = self.env["survey.survey"].create({
            "title": "Quiz des fractions", "access_mode": "token", "scoring_type": "scoring_with_answers",
            "question_and_page_ids": [(0, 0, {
                "title": "1/2 + 1/4 ?", "question_type": "simple_choice",
                "suggested_answer_ids": [
                    (0, 0, {"value": "3/4", "is_correct": True, "answer_score": 2}),
                    (0, 0, {"value": "2/6", "answer_score": 0})]}), (0, 0, {
                "title": "1/3 + 1/3 ?", "question_type": "simple_choice",
                "suggested_answer_ids": [
                    (0, 0, {"value": "2/3", "is_correct": True, "answer_score": 2}),
                    (0, 0, {"value": "2/6", "answer_score": 0})]})]})
        return survey

    def _answer_quiz(self, answer, right=1):
        for index, question in enumerate(answer.survey_id.question_ids):
            choice = question.suggested_answer_ids.filtered(
                lambda a: a.is_correct == (index < right))[:1]
            answer._save_lines(question, choice.id)
        answer._mark_done()

    def test_quiz_score_becomes_the_mark(self):
        survey = self._survey()
        homework = self._hw(submission_mode="survey", survey_id=survey.id, points_total=10)
        self.authenticate("school_wk_charlie", "school_wk_charlie")
        response = self.url_open("/my/school/work/%s/%s/quiz" % (homework.id, self.c.id),
                                 data={"csrf_token": self.csrf_token()}, allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        sub = self._sub(homework, self.c)
        answer = sub.survey_answer_id
        self.assertEqual(answer.partner_id, self.c)
        self.assertIn(answer.access_token, response.headers["Location"])
        self._answer_quiz(answer, right=1)
        self.assertEqual(sub.state, "submitted")
        self.assertEqual(sub.grade, 5)
        self.assertEqual(sub.submitted_by_id, self.c)
        with self.assertRaises(UserError):
            sub._school_quiz_url(self.c)

    def test_quiz_started_by_one_person_stays_theirs(self):
        homework = self._hw(submission_mode="survey", survey_id=self._survey().id)
        sub = self._sub(homework)
        sub._school_quiz_url(self.n.partner_id)  # opened, not started: follows the next person
        sub._school_quiz_url(self.p.partner_id)
        self.assertEqual(sub.survey_answer_id.partner_id, self.p.partner_id)
        sub.survey_answer_id.state = "in_progress"
        with self.assertRaises(UserError):
            sub._school_quiz_url(self.n.partner_id)

    def test_quiz_reopened_is_a_new_attempt(self):
        homework = self._hw(submission_mode="survey", survey_id=self._survey().id, points_total=4)
        sub = self._sub(homework)
        sub._school_quiz_url(self.p.partner_id)
        first = sub.survey_answer_id
        self._answer_quiz(first, right=2)
        self.assertEqual(sub.grade, 4)
        sub.with_user(self.t1).action_return()
        sub.with_user(self.t1).action_reopen()
        self.assertEqual((sub.state, sub.grade), ("todo", 0))
        sub._school_quiz_url(self.p.partner_id)
        self.assertNotEqual(sub.survey_answer_id, first)
        self.assertEqual(first.state, "done", "the first answer stays in the survey's results")

    # ---------------------------------------------------------------- review, 2026-10-03

    def test_group_and_mode_fixed_once_handed_in(self):
        homework = self._hw(user=self.t1).with_env(self.env)
        with self.assertRaises(AccessError):
            homework.with_user(self.t1).group_id = self.g2  # a group the teacher does not teach
        homework.group_id = self.g2  # the office, nothing handed in: the hand-ins follow
        self.assertEqual(homework.submission_ids.student_id, self.b)
        homework.group_id = self.g1
        self._sub(homework)._school_hand_in([("a.pdf", b"%PDF")], self.p.partner_id)
        for vals in ({"group_id": self.g2.id}, {"submission_mode": "none"}):
            with self.assertRaises(UserError, msg=vals):
                homework.write(vals)
        homework.name = "Rédaction : mon été, version finale"  # anything else still changes

    def test_deleting_the_homework_takes_the_files(self):
        homework = self._hw()
        sub = self._sub(homework)
        sub._school_hand_in([("a.pdf", b"%PDF")], self.p.partner_id)
        files, log = sub.attachment_ids, sub.message_ids
        homework.unlink()
        self.assertFalse(files.exists(), "the family's file is gone with the homework")
        self.assertFalse(log.exists())
        other = self._hw()
        other_files = self._sub(other)
        other_files._school_hand_in([("b.pdf", b"%PDF")], self.p.partner_id)
        kept = other_files.attachment_ids
        self.g1.unlink()
        self.assertFalse(kept.exists(), "deleting the group goes through the ORM too")

    def test_only_own_files_are_linked(self):
        foreign = self.env["ir.attachment"].with_user(self.t2).create(
            {"name": "dossier.pdf", "raw": b"%PDF", "res_model": "bf.school.homework"})
        with self.assertRaises(AccessError):
            self._hw(user=self.t1, material_ids=[(6, 0, foreign.ids)])
        other_record = self.env["ir.attachment"].create(
            {"name": "contrat.pdf", "raw": b"%PDF", "res_model": "res.partner", "res_id": self.a.id})
        sub = self._sub(self._hw())
        with self.assertRaises(AccessError):
            sub.with_user(self.t1).write({"correction_ids": [(6, 0, other_record.ids)]})
        mine = self.env["ir.attachment"].with_user(self.t1).create(
            {"name": "consignes.pdf", "raw": b"%PDF", "res_model": "bf.school.homework"})
        homework = self._hw(user=self.t1, material_ids=[(6, 0, mine.ids)])
        self.assertEqual(mine.res_id, homework.id, "attached to its homework")

    def test_total_not_below_a_mark(self):
        homework = self._hw()
        self._sub(homework).grade = 18
        with self.assertRaises(ValidationError):
            homework.points_total = 10

    def test_nobody_follows_a_hand_in(self):
        homework = self._hw()
        self._sub(homework).unlink()  # a student who joined later: the portal creates it
        self.authenticate("school_wk_p", "school_wk_p")
        self._post_files(homework, self.a, [("a.pdf", b"%PDF", "application/pdf")])
        self.assertEqual(self._sub(homework).state, "submitted")
        self.assertFalse(self._sub(homework).message_partner_ids)

    def test_quiz_survey_locked_to_the_group(self):
        survey = self._survey()
        survey.access_mode = "public"
        homework = self._hw(submission_mode="survey", survey_id=survey.id)
        self.assertEqual((survey.access_mode, survey.users_login_required, survey.is_attempts_limited,
                          survey.attempts_limit), ("token", True, True, 1))
        self.assertIn(self.t1, survey.restrict_user_ids)
        self.assertNotIn(self.t2, survey.restrict_user_ids)
        sub = self._sub(homework)
        sub._school_quiz_url(self.p.partner_id)
        self.t2.groups_id = [(4, self.env.ref("survey.group_survey_user").id)]
        with self.assertRaises(AccessError):
            sub.survey_answer_id.with_user(self.t2).read(["scoring_percentage"])

    def test_quiz_deadline_follows_late_work(self):
        homework = self._hw(submission_mode="survey", survey_id=self._survey().id, accept_late=False)
        sub = self._sub(homework)
        sub._school_quiz_url(self.p.partner_id)
        self.assertEqual(sub.survey_answer_id.deadline, homework._school_due_moment())
        homework.accept_late = True
        self.assertFalse(sub.survey_answer_id.deadline)
        homework.write({"accept_late": False, "date_due": self.today + timedelta(days=9)})
        self.assertEqual(sub.survey_answer_id.deadline, homework._school_due_moment())

    def test_a_practice_run_is_the_hand_in(self):
        homework = self._hw(submission_mode="survey", survey_id=self._survey().id, points_total=4)
        sub = self._sub(homework)
        sub._school_quiz_url(self.p.partner_id)
        official = sub.survey_answer_id
        self.assertTrue(official.invite_token)
        practice = official.survey_id._create_answer(
            partner=self.p.partner_id, invite_token=official.invite_token)
        self._answer_quiz(practice, right=2)
        self.assertEqual((sub.survey_answer_id, sub.state, sub.grade), (practice, "submitted", 4))

    # ---------------------------------------------------------------- emails

    def _mails(self, record, partner):
        return self.env["mail.mail"].sudo().search([
            ("model", "=", record._name), ("res_id", "=", record.id),
            ("recipient_ids", "in", partner.ids)])

    def _digests(self, partner):
        """The daily summaries waiting for this person: tied to no document (see the cron)."""
        return self.env["mail.mail"].sudo().search([
            ("model", "=", False), ("recipient_ids", "in", partner.ids)])

    def _returned(self, grade=17):
        homework = self._hw()
        sub = self._sub(homework)
        sub._school_hand_in([("ete.pdf", b"%PDF")], self.p.partner_id)
        sub.grade = grade
        return sub

    def test_return_email_offered_then_chosen(self):
        for who in (self.p, self.n, self.q):
            who.partner_id.write({"school_work_return_email": True, "email": "%s@example.com" % who.login})
        sub = self._returned()
        sub.with_user(self.t1).action_return()
        self.assertFalse(self._mails(sub, self.p.partner_id), "the school does not offer it")
        self.school.work_offer_return_email = True
        sub.with_user(self.t1).action_reopen()
        sub.with_user(self.t1).action_return()
        mail = self._mails(sub, self.p.partner_id)
        self.assertEqual(len(mail), 1)
        self.assertIn("Alpha Essai", mail.subject)
        self.assertIn("17 / 20", mail.body_html)
        self.assertIn("/my/school/work/%s/%s" % (sub.homework_id.id, self.a.id), mail.body_html)
        self.assertFalse(self._mails(sub, self.n.partner_id), "notices only: never the mark")
        self.assertFalse(self._mails(sub, self.q.partner_id), "another family")

    def test_return_email_not_ticked(self):
        self.school.work_offer_return_email = True
        self.p.partner_id.email = "parent@example.com"
        sub = self._returned()
        sub.with_user(self.t1).action_return()
        self.assertFalse(self._mails(sub, self.p.partner_id), "nothing is sent until ticked")

    def test_return_email_to_the_student_in_their_language(self):
        self.school.work_offer_return_email = True
        self.c.write({"school_work_return_email": True, "email": "charlie@example.com", "lang": "en_US"})
        homework = self._hw()
        sub = self._sub(homework, self.c)
        sub._school_hand_in([("c.pdf", b"%PDF")], self.c)
        sub.with_user(self.t1).action_return()
        mail = self._mails(sub, self.c)
        self.assertEqual(len(mail), 1)
        self.assertIn("work returned", mail.subject)
        if self.env["res.lang"]._lang_get("fr_CA").active:
            self.p.partner_id.write({"school_work_return_email": True, "email": "parent@example.com",
                                     "lang": "fr_CA"})
            sub_a = self._returned()
            sub_a.with_user(self.t1).action_return()
            self.assertIn("travail retourné", self._mails(sub_a, self.p.partner_id).subject)

    def test_portal_choice_only_what_is_offered(self):
        self.authenticate("school_wk_p", "school_wk_p")
        self.assertNotIn("school_work_digest", self.url_open("/my/school/work").text)
        self.school.work_offer_digest = True
        self.assertIn("school_work_digest", self.url_open("/my/school/work").text)
        self.url_open("/my/school/work/notices", data={"csrf_token": self.csrf_token(), "digest": "on",
                                                       "return_email": "on"})
        partner = self.p.partner_id
        self.assertTrue(partner.school_work_digest)
        self.assertFalse(partner.school_work_return_email, "not offered: not set")
        self.authenticate("school_wk_n", "school_wk_n")
        self.assertNotIn("school_work_digest", self.url_open("/my/school/work").text)
        self.url_open("/my/school/work/notices", data={"csrf_token": self.csrf_token(), "digest": "on"})
        self.assertFalse(self.n.partner_id.school_work_digest, "notices only: no work emails")

    def test_daily_summary(self):
        self.school.work_offer_digest = True
        partner = self.p.partner_id
        partner.write({"school_work_digest": True, "email": "parent@example.com", "lang": "en_US"})
        self.q.partner_id.write({"school_work_digest": True, "email": "other@example.com"})
        sub = self._returned()
        sub.with_user(self.t1).action_return()
        self._hw(name="Exposé oral", date_due=self.today + timedelta(days=1))
        self._hw(name="Carte du Québec", date_assigned=self.today - timedelta(days=6),
                 date_due=self.today - timedelta(days=3))
        self._hw(name="Projet lointain", date_due=self.today + timedelta(days=20))
        partner.school_work_digest_sent = fields.Datetime.now() - timedelta(days=1)
        self.env["res.partner"]._cron_school_work_digest()
        mail = self._digests(partner)
        self.assertEqual(len(mail), 1)
        body = mail.body_html
        for expected in ("Alpha Essai", "Rédaction : mon été", "17 / 20", "Exposé oral", "Carte du Québec"):
            self.assertIn(expected, body)
        self.assertNotIn("Projet lointain", body)
        self.assertNotIn("Bravo", body)
        self.assertTrue(partner.school_work_digest_sent)
        mail.unlink()
        self.env["res.partner"]._cron_school_work_digest()
        again = self._digests(partner)
        self.assertNotIn("17 / 20", again.body_html or "", "a return is told once")
        self.school.work_offer_digest = False
        again.unlink()
        self.env["res.partner"]._cron_school_work_digest()
        self.assertFalse(self._digests(partner), "no longer offered: nothing")

    def test_daily_summary_new_course_contents(self):
        if "slide.channel" not in self.env or "school_group_ids" not in self.env["slide.channel"]._fields:
            self.skipTest("bf_school_slides is not installed")
        self.school.work_offer_digest = True
        partner = self.p.partner_id
        partner.write({"school_work_digest": True, "email": "parent@example.com", "lang": "en_US",
                       "school_work_digest_sent": fields.Datetime.now() - timedelta(hours=2)})
        course = self.env["slide.channel"].create({"name": "Sciences 301", "website_published": True,
                                                   "school_group_ids": [(6, 0, self.g1.ids)]})
        slide = self.env["slide.slide"].create({"name": "Les volcans du Québec", "channel_id": course.id,
                                                "slide_category": "article", "html_content": "<p>Pluie</p>",
                                                "is_published": True})
        # eLearning stamps the publication with microseconds and the summary starts on the
        # second: published within that same second, it would be in tomorrow's summary.
        slide.date_published = fields.Datetime.now() - timedelta(minutes=1)
        self.env["res.partner"]._cron_school_work_digest()
        body = self._digests(partner).body_html
        self.assertIn("Les volcans du Québec", body)
        self.assertIn("Sciences 301", body)
        published = slide.date_published
        before = partner._school_work_digest_sections(published - timedelta(hours=1),
                                                       published - timedelta(seconds=1))
        self.assertFalse(any(s["contents"] for _student, s in before), "bounded at its start")

    # ---------------------------------------------------------------- review of the emails, 2026-10-04

    def test_summary_not_on_the_parent_card(self):
        """Every mark of every child: not in the chatter of a contact any employee may open."""
        self.school.work_offer_digest = True
        partner = self.p.partner_id
        partner.write({"school_work_digest": True, "email": "parent@example.com", "lang": "en_US",
                       "school_work_digest_sent": fields.Datetime.now() - timedelta(days=1)})
        self._returned().with_user(self.t1).action_return()
        self.env["res.partner"]._cron_school_work_digest()
        mail = self._digests(partner)
        self.assertEqual(len(mail), 1)
        self.assertIn("17 / 20", mail.body_html)
        staff = new_test_user(self.env, login="school_wk_staff",
                              groups="base.group_user,base.group_partner_manager")
        Message = self.env["mail.message"].with_user(staff)
        self.assertFalse(Message.search([("model", "=", "res.partner"), ("res_id", "=", partner.id),
                                         ("body", "ilike", "17 / 20")]))
        self.assertFalse(Message.search([("id", "=", mail.mail_message_id.id)]))

    def test_link_without_notices_gets_no_work_email(self):
        """The school set this parent to not receive notices: no marks by email either."""
        self.school.write({"work_offer_return_email": True, "work_offer_digest": True})
        partner = self.p.partner_id
        partner.write({"school_work_return_email": True, "email": "parent@example.com"})
        link =self.env["bf.school.guardian.link"].search(
            [("student_id", "=", self.a.id), ("guardian_id", "=", partner.id)])
        link.receives_notices = False
        self.assertEqual(partner._school_work_offers(), (False, False))
        sub = self._returned()
        sub.with_user(self.t1).action_return()
        self.assertFalse(self._mails(sub, partner))

    def test_student_address_shared_with_another_card(self):
        """A student's card often carries a parent's address: the student's choice must not send
        the marks there."""
        self.school.work_offer_return_email = True
        self.c.write({"school_work_return_email": True, "email": "famille@example.com"})
        self.env["res.partner"].create({"name": "Adulte Essai", "email": "famille@example.com"})
        sub = self._sub(self._hw(), self.c)
        sub._school_hand_in([("c.pdf", b"%PDF")], self.c)
        sub.with_user(self.t1).action_return()
        self.assertFalse(self._mails(sub, self.c), "the address is someone else's too")
        self.c.email = "charlie@example.com"
        sub.with_user(self.t1).action_reopen()
        sub.with_user(self.t1).action_return()
        self.assertEqual(len(self._mails(sub, self.c)), 1)

    def test_choices_written_by_administrators_only(self):
        staff = new_test_user(self.env, login="school_wk_staff2",
                              groups="base.group_user,base.group_partner_manager")
        with self.assertRaises(AccessError):
            self.p.partner_id.with_user(staff).write({"school_work_return_email": True})
        with self.assertRaises(AccessError):
            self.p.partner_id.with_user(staff).write({"school_work_digest_sent": False})

    def test_adult_without_portal_account(self):
        """No portal account: they cannot untick, so nothing goes to them."""
        self.school.work_offer_return_email = True
        delta = self.env["res.partner"].create({"name": "Delta Essai", "email": "delta@example.com",
                                                "school_work_return_email": True})
        self.env["bf.school.guardian.link"].create({"student_id": self.a.id, "guardian_id": delta.id})
        sub = self._returned()
        sub.with_user(self.t1).action_return()
        self.assertFalse(self._mails(sub, delta))

    def test_summary_bounded_at_its_start(self):
        """A work returned while the summaries go out is in tomorrow's, not in both."""
        self.school.work_offer_digest = True
        partner = self.p.partner_id
        sub = self._returned()
        sub.with_user(self.t1).action_return()
        since = sub.returned_on - timedelta(hours=1)
        before = partner._school_work_digest_sections(since, sub.returned_on - timedelta(seconds=1))
        self.assertFalse(any(s["returned"] for _student, s in before))
        after = partner._school_work_digest_sections(since, sub.returned_on)
        self.assertTrue(any(s["returned"] for _student, s in after))

    def test_summary_days_are_the_school_days(self):
        """At 2 a.m. UTC it is still yesterday at the school: due today, not late."""
        self.school.work_offer_digest = True
        self.school.company_id.partner_id.tz = "America/Toronto"
        day = self.today + timedelta(days=3)
        homework = self._hw(name="Exposé oral", date_due=day - timedelta(days=1))
        until = datetime(day.year, day.month, day.day, 2, 0)
        sections = self.p.partner_id._school_work_digest_sections(until - timedelta(days=1), until)
        found = dict(sections).get(self.a)
        self.assertTrue(found)
        self.assertIn(homework, found["due"])
        self.assertNotIn(homework, found["late"])

    def test_ticking_the_summary_again_starts_from_the_last_day(self):
        self.school.work_offer_digest = True
        partner = self.p.partner_id
        partner.write({"school_work_digest": False,
                       "school_work_digest_sent": fields.Datetime.now() - timedelta(days=30)})
        self.authenticate("school_wk_p", "school_wk_p")
        self.url_open("/my/school/work/notices", data={"csrf_token": self.csrf_token(), "digest": "on"})
        self.assertTrue(partner.school_work_digest)
        self.assertFalse(partner.school_work_digest_sent)

    def test_reopened_at_once_the_mark_does_not_leave(self):
        self.school.work_offer_return_email = True
        self.p.partner_id.write({"school_work_return_email": True, "email": "parent@example.com"})
        sub = self._returned()
        sub.with_user(self.t1).action_return()
        notice = self._mails(sub, self.p.partner_id)
        self.assertTrue(notice)
        notice.state = "exception"  # failed: an administrator could still resend it
        chatter = self.env["mail.mail"].sudo().create({
            "model": sub._name, "res_id": sub.id, "is_notification": True, "state": "outgoing",
            "subject": "Un mot de l'enseignante", "body_html": "<p>Bravo</p>",
            "recipient_ids": [(6, 0, self.p.partner_id.ids)]})
        sub.with_user(self.t1).action_reopen()
        self.assertFalse(notice.exists(), "taken back before it could leave")
        self.assertTrue(chatter.exists(), "a teacher's message on the hand-in is not the return email")

    def test_no_return_email_once_the_student_left(self):
        self.school.work_offer_return_email = True
        self.p.partner_id.write({"school_work_return_email": True, "email": "parent@example.com"})
        sub = self._returned()
        self.a.student_enrollment_ids.filtered(lambda e: e.group_id == self.g1).state = "left"
        sub.with_user(self.t1).action_return()
        self.assertFalse(self._mails(sub, self.p.partner_id), "its link would end on a 404")

    def test_summary_contents_of_offering_schools_only(self):
        if "slide.channel" not in self.env or "school_group_ids" not in self.env["slide.channel"]._fields:
            self.skipTest("bf_school_slides is not installed")
        self.school.work_offer_digest = True
        other = self.env["bf.school"].create({"name": "Autre école des Essais"})
        year = self.env["bf.school.year"].create({"name": "2026-2027", "school_id": other.id,
                                                  "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        year.action_set_current()
        g3 = self.env["bf.school.group"].create({"name": "Musique", "school_id": other.id, "year_id": year.id})
        self.env["bf.school.enrollment"].create({"student_id": self.a.id, "group_id": g3.id})
        course = self.env["slide.channel"].create({"name": "Musique 101", "website_published": True,
                                                   "school_group_ids": [(6, 0, g3.ids)]})
        self.env["slide.slide"].create({"name": "Les gammes", "channel_id": course.id, "slide_category": "article",
                                        "html_content": "<p>Do ré mi</p>", "is_published": True})
        self._hw(name="Exposé oral", date_due=self.today + timedelta(days=1))
        now = fields.Datetime.now() + timedelta(minutes=1)
        sections = dict(self.p.partner_id._school_work_digest_sections(now - timedelta(days=1), now))
        self.assertTrue(sections.get(self.a), "the offering school still has something to say")
        self.assertFalse(sections[self.a]["contents"], "the other school does not offer the summary")

    def test_summary_from_the_school_company(self):
        company = self.env["res.company"].create({"name": "École B des Essais",
                                                  "email": "secretariat@ecole-b.example.com"})
        self.school.company_id = company
        self.school.work_offer_digest = True
        partner = self.p.partner_id
        partner.write({"school_work_digest": True, "email": "parent@example.com", "lang": "en_US",
                       "school_work_digest_sent": fields.Datetime.now() - timedelta(days=1)})
        self._hw(name="Exposé oral", date_due=self.today + timedelta(days=1))
        self.env["res.partner"]._cron_school_work_digest()
        mail = self._digests(partner)
        self.assertEqual(len(mail), 1)
        self.assertIn("secretariat@ecole-b.example.com", mail.email_from)

    def test_one_summary_per_company(self):
        """Children at schools of two companies: two summaries, each from its own company."""
        self.school.company_id.email = "ecole-a@example.com"
        self.school.work_offer_digest = True
        company = self.env["res.company"].create({"name": "École B des Essais", "email": "ecole-b@example.com"})
        other = self.env["bf.school"].create({"name": "Autre école des Essais", "company_id": company.id,
                                              "work_offer_digest": True})
        year = self.env["bf.school.year"].create({"name": "2026-2027", "school_id": other.id,
                                                  "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        year.action_set_current()
        g3 = self.env["bf.school.group"].create({"name": "B-301", "school_id": other.id, "year_id": year.id})
        delta = self.env["res.partner"].create({"name": "Delta Essai", "is_student": True})
        self.env["bf.school.enrollment"].create({"student_id": delta.id, "group_id": g3.id})
        self.env["bf.school.guardian.link"].create({"student_id": delta.id, "guardian_id": self.p.partner_id.id})
        partner = self.p.partner_id
        partner.write({"school_work_digest": True, "email": "parent@example.com", "lang": "en_US",
                       "school_work_digest_sent": fields.Datetime.now() - timedelta(days=1)})
        self._hw(name="Exposé oral", date_due=self.today + timedelta(days=1))
        self._hw(group=g3, name="Carte du Nord", date_due=self.today + timedelta(days=1))
        self.env["res.partner"]._cron_school_work_digest()
        mails = self._digests(partner)
        self.assertEqual(len(mails), 2)
        a = mails.filtered(lambda m: "ecole-a@example.com" in m.email_from)
        b = mails.filtered(lambda m: "ecole-b@example.com" in m.email_from)
        self.assertTrue(a and b)
        self.assertIn("Exposé oral", a.body_html)
        self.assertNotIn("Carte du Nord", a.body_html)
        self.assertIn("Carte du Nord", b.body_html)
        self.assertNotIn("Alpha Essai", b.body_html)

