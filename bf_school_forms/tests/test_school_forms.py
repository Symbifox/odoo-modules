import re
from datetime import date, timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class SchoolFormsCase(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.school = env["bf.school"].create({"name": "École des Essais"})
        year = env["bf.school.year"].create({
            "name": "2026-2027", "school_id": cls.school.id,
            "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        year.action_set_current()
        cls.g301 = env["bf.school.group"].create({"name": "301", "school_id": cls.school.id, "year_id": year.id})
        cls.g302 = env["bf.school.group"].create({"name": "302", "school_id": cls.school.id, "year_id": year.id})
        Partner = env["res.partner"]
        cls.alpha = Partner.create({"name": "Alpha Essai", "is_student": True})
        cls.orphan = Partner.create({"name": "Sans Signataire Essai", "is_student": True})
        cls.bravo = Partner.create({"name": "Bravo Essai", "is_student": True})
        Enrollment = env["bf.school.enrollment"]
        for child, group in ((cls.alpha, cls.g301), (cls.orphan, cls.g301), (cls.bravo, cls.g302)):
            Enrollment.create({"student_id": child.id, "group_id": group.id})
        cls.mom = new_test_user(env, login="school_frm_mom", name="Maman Essai",
                                email="frm.mom@example.invalid", groups="base.group_portal")
        cls.dad = env["res.partner"].create({"name": "Papa Essai", "email": "frm.dad@example.invalid"})
        cls.other = new_test_user(env, login="school_frm_other", name="Autre Parent",
                                  email="frm.other@example.invalid", groups="base.group_portal")
        cls.granny = Partner.create({"name": "Mamie Essai", "email": "frm.granny@example.invalid"})
        Link = env["bf.school.guardian.link"]
        Link.create({"student_id": cls.alpha.id, "guardian_id": cls.mom.partner_id.id})
        Link.create({"student_id": cls.alpha.id, "guardian_id": cls.dad.id})
        Link.create({"student_id": cls.alpha.id, "guardian_id": cls.granny.id,
                     "has_parental_authority": False, "can_sign": False})
        Link.create({"student_id": cls.orphan.id, "guardian_id": cls.granny.id,
                     "has_parental_authority": False, "can_sign": False})
        Link.create({"student_id": cls.bravo.id, "guardian_id": cls.other.partner_id.id})
        cls.office = new_test_user(env, login="school_frm_office", name="Secrétariat",
                                   email="frm.office@example.invalid",
                                   groups="bf_school_core.group_school_manager")
        cls.teacher = new_test_user(env, login="school_frm_teacher",
                                    groups="bf_school_core.group_school_user")

    def _form(self, **vals):
        form = self.env["bf.school.form"].with_user(self.office).create(dict({
            "name": "Sortie au musée", "type_id": self.env.ref("bf_school_forms.form_type_trip").id,
            "school_id": self.school.id, "group_ids": [(6, 0, self.g301.ids)],
            "body_html": "<p>Départ 9 h, retour 15 h.</p>", "event_date": date.today() + timedelta(days=20),
            "deadline": date.today() + timedelta(days=10)}, **vals))
        return form

    def _sent(self, **vals):
        form = self._form(**vals)
        form.action_send()
        return form

    def _response(self, form, student):
        return form.response_ids.filtered(lambda r: r.student_id == student)

    def _answer(self, form, student, partner):
        return self._response(form, student).answer_ids.filtered(lambda a: a.partner_id == partner)

    def _csrf(self, url):
        page = self.url_open(url).text
        match = re.search(r'name="csrf_token" value="([^"]+)"', page) or re.search(
            r'csrf_token["\']?\s*:\s*["\']([^"\']+)', page)
        self.assertTrue(match, "no CSRF token on %s" % url)
        return match.group(1)


@tagged("post_install", "-at_install")
class TestFormLogic(SchoolFormsCase):

    def test_send_one_response_per_student_one_answer_per_signer(self):
        form = self._sent()
        self.assertEqual(form.response_ids.student_id, self.alpha | self.orphan)
        self.assertEqual(self._response(form, self.alpha).answer_ids.partner_id,
                         self.mom.partner_id | self.dad, "the grandmother does not sign")
        self.assertEqual(self._response(form, self.orphan).state, "no_signer")
        self.assertEqual(len(form.body_hash), 64)

    def test_one_guardian_is_enough(self):
        form = self._sent()
        self._answer(form, self.alpha, self.dad)._school_decide("accepted", "link")
        self.assertEqual(self._response(form, self.alpha).state, "accepted")

    def test_all_signers_when_required(self):
        form = self._sent(requires_all_signers=True)
        self._answer(form, self.alpha, self.dad)._school_decide("accepted", "link")
        self.assertEqual(self._response(form, self.alpha).state, "pending")
        self._answer(form, self.alpha, self.mom.partner_id)._school_decide("accepted", "link")
        self.assertEqual(self._response(form, self.alpha).state, "accepted")

    def test_overnight_type_requires_everyone(self):
        form = self._form(type_id=self.env.ref("bf_school_forms.form_type_overnight").id)
        self.assertTrue(form.requires_all_signers)

    def test_refusal_wins(self):
        form = self._sent()
        self._answer(form, self.alpha, self.dad)._school_decide("accepted", "link")
        self._answer(form, self.alpha, self.mom.partner_id)._school_decide("refused", "link")
        self.assertEqual(self._response(form, self.alpha).state, "refused")

    def test_answer_is_final(self):
        form = self._sent()
        answer = self._answer(form, self.alpha, self.dad)
        answer._school_decide("accepted", "link")
        with self.assertRaises(UserError):
            answer._school_decide("refused", "link")

    def test_closed_form_expires_pending(self):
        form = self._sent()
        form.action_close()
        self.assertEqual(self._response(form, self.alpha).state, "expired")
        with self.assertRaises(UserError):
            self._answer(form, self.alpha, self.dad)._school_decide("accepted", "link")

    def test_cron_closes_past_deadline(self):
        form = self._sent()
        form.sudo().write({"deadline": date.today() - timedelta(days=1)})
        self.env["bf.school.form"]._cron_close_past_deadline()
        self.assertEqual(form.state, "closed")

    def test_sent_text_is_frozen(self):
        form = self._sent()
        with self.assertRaises(UserError):
            form.write({"body_html": "<p>Autre chose.</p>"})
        form.write({"deadline": date.today() + timedelta(days=12)})

    def test_evidence_keeps_the_text_fingerprint(self):
        form = self._sent()
        answer = self._answer(form, self.alpha, self.dad)
        answer._school_decide("accepted", "link", ip="203.0.113.7", user_agent="Essai")
        self.assertEqual(answer.body_hash, form.body_hash)
        self.assertEqual(answer.ip_address, "203.0.113.7")
        self.assertTrue(answer.answered_on)

    def test_one_email_per_signer_naming_the_child(self):
        self._sent()
        mails = self.env["mail.mail"].sudo().search([("recipient_ids", "in", self.dad.ids)])
        self.assertEqual(len(mails), 1)
        self.assertEqual(mails.subject, "Alpha: authorisation, Sortie au musée")
        self.assertIn("/school/form/", mails.body_html)
        self.assertFalse(self.env["mail.mail"].sudo().search([("recipient_ids", "in", self.granny.ids)]))

    def test_staff_read_but_cannot_send_or_see_evidence(self):
        form = self._form()
        with self.assertRaises(AccessError):
            form.with_user(self.teacher).action_send()
        form.action_send()
        self.assertEqual(form.with_user(self.teacher).count_pending, 1)
        with self.assertRaises(AccessError):
            self.env["bf.school.form.answer"].with_user(self.teacher).search([])


@tagged("post_install", "-at_install")
class TestFormPortal(SchoolFormsCase):

    def test_link_without_account(self):
        form = self._sent()
        answer = self._answer(form, self.alpha, self.dad)
        url = answer._url()
        self.assertIn("Sortie au musée", self.url_open(url).text)
        self.url_open(url + "/answer", data={"decision": "accepted", "csrf_token": self._csrf(url)})
        self.assertEqual(answer.decision, "accepted")
        self.assertEqual(answer.channel, "link")
        self.assertFalse(answer.answered_by_user_id)

    def test_wrong_token_is_404(self):
        form = self._sent()
        answer = self._answer(form, self.alpha, self.dad)
        self.assertEqual(self.url_open("/school/form/%s/%s" % (answer.id, "x" * 43)).status_code, 404)

    def test_portal_list_and_answer(self):
        form = self._sent()
        self.authenticate("school_frm_mom", "school_frm_mom")
        self.assertIn("Sortie au musée", self.url_open("/my/school/forms").text)
        answer = self._answer(form, self.alpha, self.mom.partner_id)
        url = "/my/school/forms/%s" % answer.id
        self.url_open(url + "/answer", data={"decision": "refused", "csrf_token": self._csrf(url)})
        self.assertEqual(answer.decision, "refused")
        self.assertEqual(answer.answered_by_user_id, self.mom)
        self.assertEqual(self._response(form, self.alpha).state, "refused")

    def test_other_parent_gets_404(self):
        form = self._sent()
        answer = self._answer(form, self.alpha, self.mom.partner_id)
        self.authenticate("school_frm_other", "school_frm_other")
        self.assertNotIn("Sortie au musée", self.url_open("/my/school/forms").text)
        self.assertEqual(self.url_open("/my/school/forms/%s" % answer.id).status_code, 404)
        response = self.url_open("/my/school/forms/%s/answer" % answer.id,
                                 data={"decision": "accepted",
                                       "csrf_token": self._csrf("/my/school/forms")})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(answer.decision, "pending")


@tagged("post_install", "-at_install")
class TestFormFrench(SchoolFormsCase):

    def test_email_renders_in_french(self):
        """🔴 The French body is a separate translation: a wrong one crashes only in French."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.dad.lang = "fr_CA"
        self._sent()
        mail = self.env["mail.mail"].sudo().search([("recipient_ids", "in", self.dad.ids)])
        self.assertEqual(mail.subject, "Alpha : autorisation, Sortie au musée")
        self.assertIn("Répondre", mail.body_html)
        self.assertIn("Départ 9 h", mail.body_html)

    # Adversarial review (2026-09-27)
    def test_a_guardian_who_no_longer_signs_cannot_answer(self):
        form = self._sent()
        answer = self._answer(form, self.alpha, self.dad)
        link = self.alpha.student_guardian_link_ids.filtered(lambda l: l.guardian_id == self.dad)
        link.write({"can_sign": False, "has_parental_authority": False})
        with self.assertRaises(UserError):
            answer._school_decide("refused", "link")
        self.assertEqual(answer.decision, "pending")
        self.assertEqual(self.url_open("/school/form/%s/%s" % (answer.id, answer.access_token)).status_code, 404)
