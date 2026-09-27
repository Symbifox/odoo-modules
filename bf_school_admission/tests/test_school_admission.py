import base64
import re
from datetime import date, timedelta

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class AdmissionCase(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.school = env["bf.school"].create({"name": "École des Essais"})
        cls.year = env["bf.school.year"].create({
            "name": "2026-2027", "school_id": cls.school.id,
            "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        cls.year.action_set_current()
        cls.level = env.ref("bf_school_core.level_s1")
        cls.level2 = env.ref("bf_school_core.level_s2")
        today = date.today()
        cls.campaign = env["bf.school.admission.campaign"].create({
            "name": "Admission 2027-2028", "school_id": cls.school.id, "target_year": "2027-2028",
            "level_ids": [(6, 0, (cls.level | cls.level2).ids)], "fee_amount": 50.0,
            "date_open": today - timedelta(days=5), "date_close": today + timedelta(days=5),
            "state": "open"})
        cls.office = new_test_user(env, login="school_adm_office",
                                   groups="bf_school_core.group_school_manager,account.group_account_invoice")
        cls.teacher = new_test_user(env, login="school_adm_teacher", groups="bf_school_core.group_school_user")

    def _csrf(self, url):
        page = self.url_open(url).text
        match = re.search(r'name="csrf_token" value="([^"]+)"', page) or re.search(
            r'csrf_token["\']?\s*:\s*["\']([^"\']+)', page)
        self.assertTrue(match, "no CSRF token on %s" % url)
        return match.group(1)

    def _apply(self, **extra):
        url = "/school/admission/%s" % self.campaign.id
        data = dict({
            "csrf_token": self._csrf(url),
            "student_firstname": "Nouvel", "student_lastname": "Élève-Essai",
            "student_birthdate": "2014-03-02", "level_id": str(self.level.id),
            "current_school": "École d'ailleurs",
            "guardian1_name": "Parent Un", "guardian1_email": "adm.un@example.invalid",
            "guardian2_name": "Parent Deux", "guardian2_email": "adm.deux@example.invalid",
            "privacy_ack": "1"}, **extra)
        files = {"documents": ("naissance.pdf", b"%PDF-1.4 essai", "application/pdf")}
        return self.url_open(url + "/submit", data=data, files=files)

    def _last(self):
        return self.env["bf.school.admission"].search([("campaign_id", "=", self.campaign.id)],
                                                      order="id desc", limit=1)

    def _pay(self, move):
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=move.ids).create({})._create_payments()


@tagged("post_install", "-at_install")
class TestCampaign(AdmissionCase):

    def test_fee_caps(self):
        with self.assertRaises(ValidationError):
            self.campaign.fee_amount = 50.01
        reenrol = self.env["bf.school.admission.campaign"].create({
            "name": "Réinscription", "kind": "reenrollment", "school_id": self.school.id,
            "target_year": "2027-2028", "fee_amount": 200.0,
            "date_open": date.today(), "date_close": date.today()})
        with self.assertRaises(ValidationError):
            reenrol.fee_amount = 200.01


@tagged("post_install", "-at_install")
class TestPublicForm(AdmissionCase):

    def test_application_creates_family_documents_and_invoice(self):
        response = self._apply()
        app = self._last()
        self.assertEqual(app.state, "awaiting_fee")
        self.assertEqual(set(app.guardian_ids.mapped("email")), {"adm.un@example.invalid", "adm.deux@example.invalid"})
        self.assertEqual(app.payer_id.email, "adm.un@example.invalid")
        self.assertEqual(app.invoice_id.state, "posted")
        self.assertEqual(app.invoice_id.amount_total, 50.0, "no tax on an educational fee")
        self.assertFalse(app.invoice_id.invoice_pdf_report_id, "not in the family's request")
        self.env["bf.school.admission"]._cron_fee_invoice_pdf()
        self.assertTrue(app.invoice_id.invoice_pdf_report_id, "the official PDF, not a proforma")
        self.assertFalse(self.env["mail.mail"].sudo().search([("model", "=", "account.move"), ("res_id", "=", app.invoice_id.id)]),
                         "the invoice itself is not emailed")
        attachment = self.env["ir.attachment"].search([("res_model", "=", app._name), ("res_id", "=", app.id)])
        self.assertEqual(attachment.name, "naissance.pdf")
        self.assertIn(app._status_url(), response.url)

    def test_status_page_needs_the_token(self):
        self._apply()
        app = self._last()
        page = self.url_open(app._status_url()).text
        self.assertIn(app.name, page)
        self.assertIn(app._payment_url().split("?")[0], page, "the page offers to pay")
        self.assertEqual(self.url_open("/school/admission/status/%s/%s" % (app.id, "x" * 43)).status_code, 404)

    def test_exam_time_in_the_school_time_zone(self):
        """🔴 A family without an account has no time zone: Odoo showed UTC, 4 hours off."""
        self.env.company.partner_id.tz = "America/Toronto"
        self._apply()
        app = self._last()
        self.campaign.exam_datetime = "2026-10-17 17:30:00"
        app.sudo().state = "convened"
        page = self.url_open(app._status_url()).text
        self.assertIn("13:30", page)
        self.assertNotIn("17:30", page)

    def test_robot_field_creates_nothing(self):
        before = self.env["bf.school.admission"].search_count([])
        self._apply(website_url="http://spam.example.invalid")
        self.assertEqual(self.env["bf.school.admission"].search_count([]), before)

    def test_level_not_offered_is_refused(self):
        before = self.env["bf.school.admission"].search_count([])
        response = self._apply(level_id=str(self.env.ref("bf_school_core.level_e1").id))
        self.assertIn("Choose a level", response.text)
        self.assertEqual(self.env["bf.school.admission"].search_count([]), before)

    def test_closed_campaign_refuses(self):
        self.campaign.action_close()
        url = "/school/admission/%s" % self.campaign.id
        self.assertIn("not open", self.url_open(url).text)
        before = self.env["bf.school.admission"].search_count([])
        response = self._apply()  # a complete, valid application: only the closing refuses it
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.env["bf.school.admission"].search_count([]), before)

    def test_fee_paid_at_the_office_submits(self):
        self._apply()
        app = self._last()
        self._pay(app.invoice_id)
        self.assertEqual(app.state, "submitted")
        mails = self.env["mail.mail"].sudo().search([("recipient_ids", "in", app.guardian_ids.ids)])
        self.assertEqual(len(mails), 2, "one email per guardian")

    def test_campaign_without_fee_submits_at_once(self):
        self.campaign.fee_amount = 0.0
        self._apply()
        app = self._last()
        self.assertFalse(app.invoice_id)
        self.assertEqual(app.state, "submitted")


@tagged("post_install", "-at_install")
class TestDecision(AdmissionCase):

    def _submitted(self, first, score, level=None):
        app = self.env["bf.school.admission"].create({
            "campaign_id": self.campaign.id, "student_firstname": first, "student_lastname": "Essai",
            "student_birthdate": date(2014, 1, 1), "level_id": (level or self.level).id,
            "guardian_ids": [(0, 0, {"name": "Parent de %s" % first, "email": "%s@example.invalid" % first.lower()})],
            "state": "submitted", "score": score})
        app.payer_id = app.guardian_ids[:1]
        return app

    def test_convene_needs_the_exam_date(self):
        app = self._submitted("Alpha", 0)
        with self.assertRaises(UserError):
            app.action_convene()
        self.campaign.exam_datetime = "2026-10-17 13:00:00"
        app.action_convene()
        self.assertEqual(app.state, "convened")

    def test_rank_per_level_by_score(self):
        a, b, c = (self._submitted("Alpha", 71), self._submitted("Bravo", 88), self._submitted("Charlie", 64))
        other = self._submitted("Delta", 99, level=self.level2)
        (a | b | c | other).action_mark_evaluated()
        self.assertEqual((b.rank, a.rank, c.rank, other.rank), (1, 2, 3, 1))

    def test_decision_is_signed_by_a_person(self):
        app = self._submitted("Alpha", 80)
        app.action_mark_evaluated()
        app.with_user(self.office).action_waitlist()
        self.assertEqual(app.state, "waitlisted")
        self.assertEqual(app.decided_by_id, self.office)
        app.with_user(self.office).action_accept()
        self.assertEqual(app.state, "accepted")

    def test_no_decision_before_submission(self):
        app = self._submitted("Alpha", 80)
        app.state = "awaiting_fee"
        with self.assertRaises(UserError):
            app.action_accept()

    def test_enrol_creates_student_and_links(self):
        app = self._submitted("Alpha", 80)
        app.action_mark_evaluated()
        app.action_accept()
        app.action_enroll()
        student = app.student_id
        self.assertTrue(student.is_student)
        self.assertEqual(student.name, "Alpha Essai")
        self.assertEqual(student.student_guardian_link_ids.guardian_id, app.guardian_ids)
        self.assertTrue(student.student_guardian_link_ids.is_payer)
        self.assertTrue(student.is_minor_child)

    def test_purge_destroys_what_the_family_sent(self):
        kept = self._submitted("Alpha", 90)
        kept.action_mark_evaluated()
        kept.action_accept()
        kept.action_enroll()
        gone = self._submitted("Bravo", 40)
        gone.evaluation_note = "Note interne"
        self.env["ir.attachment"].create({"name": "bulletin.pdf", "datas": base64.b64encode(b"%PDF"),
                                          "res_model": gone._name, "res_id": gone.id})
        with self.assertRaises(UserError):
            self.campaign.action_purge()
        self.campaign.action_close()
        self.campaign.action_purge()
        self.assertTrue(gone.purged)
        self.assertFalse(gone.student_firstname or gone.student_birthdate or gone.evaluation_note)
        self.assertFalse(self.env["ir.attachment"].search([("res_model", "=", gone._name), ("res_id", "=", gone.id)]))
        self.assertEqual(kept.student_firstname, "Alpha", "an enrolled student is not purged")

    def test_teacher_has_no_access(self):
        with self.assertRaises(AccessError):
            self.env["bf.school.admission"].with_user(self.teacher).search([])


@tagged("post_install", "-at_install")
class TestReenrollment(AdmissionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        group = env["bf.school.group"].create({"name": "101", "school_id": cls.school.id, "year_id": cls.year.id})
        cls.child = env["res.partner"].create({"name": "Enfant Réinscrit", "is_student": True})
        env["bf.school.enrollment"].create({"student_id": cls.child.id, "group_id": group.id})
        cls.mom = new_test_user(env, login="school_adm_mom", name="Maman Réinscription",
                                email="adm.mom@example.invalid", groups="base.group_portal")
        cls.step = new_test_user(env, login="school_adm_step", name="Beau-parent",
                                 email="adm.step@example.invalid", groups="base.group_portal")
        cls.other = new_test_user(env, login="school_adm_other", name="Autre Parent",
                                  email="adm.other@example.invalid", groups="base.group_portal")
        Link = env["bf.school.guardian.link"]
        Link.create({"student_id": cls.child.id, "guardian_id": cls.mom.partner_id.id, "is_payer": True})
        Link.create({"student_id": cls.child.id, "guardian_id": cls.step.partner_id.id,
                     "has_parental_authority": False, "can_sign": False})
        cls.reenrol = env["bf.school.admission.campaign"].create({
            "name": "Réinscription 2027-2028", "kind": "reenrollment", "school_id": cls.school.id,
            "target_year": "2027-2028", "fee_amount": 200.0, "level_ids": [(6, 0, cls.level2.ids)],
            "date_open": date.today() - timedelta(days=1), "date_close": date.today() + timedelta(days=30),
            "state": "open"})

    def _url(self):
        return "/my/school/reenroll/%s/%s" % (self.child.id, self.reenrol.id)

    def test_signing_parent_reenrols_and_pays(self):
        self.authenticate("school_adm_mom", "school_adm_mom")
        self.assertIn("Re-enrol for 2027-2028", self.url_open("/my/school").text)
        self.url_open(self._url() + "/submit",
                      data={"csrf_token": self._csrf(self._url()), "level_id": str(self.level2.id)})
        app = self.env["bf.school.admission"].search([("campaign_id", "=", self.reenrol.id)])
        self.assertEqual((app.student_id, app.state, app.level_id), (self.child, "awaiting_fee", self.level2))
        self.assertEqual(app.invoice_id.amount_total, 200.0)
        self.assertEqual(app.payer_id, self.mom.partner_id)
        self.assertFalse(app.invoice_id.invoice_user_id, "the parent on the portal is not the salesperson")
        self.assertTrue(app.invoice_id.invoice_line_ids.name.startswith(("Registration fee", "Droits d'inscription")),
                        app.invoice_id.invoice_line_ids.name)
        self._pay(app.invoice_id)
        self.assertEqual(app.state, "confirmed")
        self.assertIn("Re-enrolment 2027-2028", self.url_open("/my/school").text)

    def test_no_second_application(self):
        self.authenticate("school_adm_mom", "school_adm_mom")
        for _i in range(2):
            self.url_open(self._url() + "/submit", data={"csrf_token": self._csrf("/my/school")})
        self.assertEqual(self.env["bf.school.admission"].search_count([("campaign_id", "=", self.reenrol.id)]), 1)

    def test_adult_who_does_not_sign_cannot_reenrol(self):
        self.authenticate("school_adm_step", "school_adm_step")
        self.assertNotIn("Re-enrol for", self.url_open("/my/school").text)
        self.assertEqual(self.url_open(self._url()).status_code, 404)

    def test_other_family_gets_404(self):
        self.authenticate("school_adm_other", "school_adm_other")
        self.assertEqual(self.url_open(self._url()).status_code, 404)
        self.url_open(self._url() + "/submit", data={"csrf_token": self._csrf("/my/school")})
        self.assertFalse(self.env["bf.school.admission"].search([("campaign_id", "=", self.reenrol.id)]))


@tagged("post_install", "-at_install")
class TestAdmissionFrench(AdmissionCase):

    def test_email_renders_in_french(self):
        """🔴 The French body is a separate translation: a wrong one crashes only in French."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self._apply()
        app = self._last()
        app.guardian_ids.write({"lang": "fr_CA"})
        self._pay(app.invoice_id)
        mail = self.env["mail.mail"].sudo().search([("recipient_ids", "in", app.guardian_ids[:1].ids)])
        self.assertEqual(len(mail), 1)
        self.assertIn("Nous avons reçu la demande", mail.body_html)
        self.assertTrue(mail.subject.startswith("École des Essais : Admission 2027-2028, "))
