import re
from datetime import date

from odoo.exceptions import AccessError
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class SchoolMessageCase(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.school = env["bf.school"].create({"name": "École des Essais"})
        year = env["bf.school.year"].create({
            "name": "2026-2027", "school_id": cls.school.id,
            "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        year.action_set_current()
        Group = env["bf.school.group"]
        cls.g301 = Group.create({"name": "301", "school_id": cls.school.id, "year_id": year.id})
        cls.g302 = Group.create({"name": "302", "school_id": cls.school.id, "year_id": year.id})
        Partner = env["res.partner"]
        cls.alpha = Partner.create({"name": "Alpha Essai", "is_student": True})
        cls.charlie = Partner.create({"name": "Charlie Essai", "is_student": True})
        cls.bravo = Partner.create({"name": "Bravo Essai", "is_student": True})
        Enrollment = env["bf.school.enrollment"]
        Enrollment.create({"student_id": cls.alpha.id, "group_id": cls.g301.id})
        Enrollment.create({"student_id": cls.charlie.id, "group_id": cls.g301.id})
        Enrollment.create({"student_id": cls.bravo.id, "group_id": cls.g302.id})
        cls.parent_a = new_test_user(env, login="school_msg_a", name="Parent A",
                                     email="msg.a@example.invalid", groups="base.group_portal")
        cls.parent_b = new_test_user(env, login="school_msg_b", name="Parent B",
                                     email="msg.b@example.invalid", groups="base.group_portal")
        cls.parent_quiet = new_test_user(env, login="school_msg_q", name="Parent Q",
                                         email="msg.q@example.invalid", groups="base.group_portal")
        Link = env["bf.school.guardian.link"]
        for child in cls.alpha | cls.charlie:
            Link.create({"student_id": child.id, "guardian_id": cls.parent_a.partner_id.id})
        Link.create({"student_id": cls.bravo.id, "guardian_id": cls.parent_b.partner_id.id})
        Link.create({"student_id": cls.alpha.id, "guardian_id": cls.parent_quiet.partner_id.id,
                     "receives_notices": False})
        cls.office = new_test_user(env, login="school_msg_office", name="Secrétariat",
                                   email="office@example.invalid",
                                   groups="bf_school_core.group_school_manager")
        cls.staff = new_test_user(env, login="school_msg_staff", groups="base.group_user")

    def _csrf(self, url="/my/school/news"):
        """The session's CSRF token, read from a portal page as a browser would."""
        page = self.url_open(url).text
        match = re.search(r'name="csrf_token" value="([^"]+)"', page) or re.search(
            r'csrf_token["\']?\s*:\s*["\']([^"\']+)', page)
        self.assertTrue(match, "no CSRF token on %s" % url)
        return match.group(1)

    def _post(self, **vals):
        return self.env["bf.babillard.post"].with_user(self.office).create(dict({
            "name": "Sortie au musée", "corps_html": "<p>Mardi prochain.</p>",
            "audience": "school_families", "school_id": self.school.id,
            "state": "publie"}, **vals))

    def _mails_to(self, user):
        return self.env["mail.mail"].sudo().search(
            [("recipient_ids", "in", user.partner_id.ids)])



@tagged("post_install", "-at_install")
class TestSchoolMessage(SchoolMessageCase):

    def test_group_audience(self):
        post = self._post(school_group_ids=[(6, 0, self.g301.ids)])
        self.assertEqual(post._destinataires(), self.parent_a)

    def test_school_audience(self):
        post = self._post()
        self.assertEqual(post._destinataires(), self.parent_a | self.parent_b)

    def test_one_email_per_adult_naming_children(self):
        self._post(school_group_ids=[(6, 0, self.g301.ids)])
        mails = self._mails_to(self.parent_a)
        self.assertEqual(len(mails), 1, "two children, one email")
        self.assertEqual(mails.subject, "Alpha, Charlie: Sortie au musée")
        self.assertIn("/my/school/news/", mails.body_html)
        self.assertFalse(self._mails_to(self.parent_b))
        self.assertFalse(self._mails_to(self.parent_quiet))

    def test_draft_sends_nothing_until_published(self):
        post = self._post(state="brouillon")
        self.assertFalse(self._mails_to(self.parent_a))
        post.action_publier()
        self.assertEqual(len(self._mails_to(self.parent_a)), 1)
        post.action_remettre_en_brouillon()
        post.action_publier()
        self.assertEqual(len(self._mails_to(self.parent_a)), 1, "a notice goes out once")

    def test_staff_do_not_see_family_posts(self):
        post = self._post()
        self.assertFalse(self.env["bf.babillard.post"].with_user(self.staff).search(
            [("id", "=", post.id)]))

    def test_portal_has_no_rpc_access(self):
        with self.assertRaises(AccessError):
            self.env["bf.babillard.post"].with_user(self.parent_a).search([])

    def test_portal_list_and_detail(self):
        post = self._post(school_group_ids=[(6, 0, self.g301.ids)])
        self.authenticate("school_msg_a", "school_msg_a")
        self.assertIn("Sortie au musée", self.url_open("/my/school/news").text)
        detail = self.url_open("/my/school/news/%s" % post.id)
        self.assertEqual(detail.status_code, 200)
        self.assertIn("Mardi prochain", detail.text)
        self.assertIn("Alpha, Charlie", detail.text)

    def test_other_family_gets_404(self):
        post = self._post(school_group_ids=[(6, 0, self.g301.ids)])
        self.authenticate("school_msg_b", "school_msg_b")
        self.assertNotIn("Sortie au musée", self.url_open("/my/school/news").text)
        self.assertEqual(self.url_open("/my/school/news/%s" % post.id).status_code, 404)

    def test_confirm_reading(self):
        post = self._post(lecture_requise=True, school_group_ids=[(6, 0, self.g301.ids)])
        self.authenticate("school_msg_a", "school_msg_a")
        page = self.url_open("/my/school/news/%s" % post.id).text
        self.assertIn("I have read it", page)
        self.assertFalse(post.sudo().lecture_ids)
        self.url_open("/my/school/news/%s/read" % post.id,
                      data={"csrf_token": self._csrf("/my/school/news/%s" % post.id)})
        self.assertEqual(post.sudo().lecture_ids.user_id, self.parent_a)
        self.assertIn("You confirmed reading", self.url_open("/my/school/news/%s" % post.id).text)

    def test_other_family_cannot_confirm(self):
        post = self._post(lecture_requise=True, school_group_ids=[(6, 0, self.g301.ids)])
        self.authenticate("school_msg_b", "school_msg_b")
        response = self.url_open("/my/school/news/%s/read" % post.id,
                                 data={"csrf_token": self._csrf()})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(post.sudo().lecture_ids)

    def test_notice_goes_out_once_even_when_called_again(self):
        post = self._post(school_group_ids=[(6, 0, self.g301.ids)])
        post.sudo()._school_notify_families()
        self.assertEqual(len(self._mails_to(self.parent_a)), 1)

    def test_feed_matches_recipients(self):
        """The portal feed and the recipient list must agree for every adult."""
        posts = (self._post(school_group_ids=[(6, 0, self.g301.ids)])
                 | self._post(school_group_ids=[(6, 0, self.g302.ids)])
                 | self._post())
        Post = self.env["bf.babillard.post"]
        for user in self.parent_a | self.parent_b | self.parent_quiet:
            feed = Post._school_news_for(user)
            for post in posts:
                self.assertEqual(
                    post in feed, user.id in post._school_recipient_map(),
                    "%s / %s" % (user.login, post.school_group_ids.mapped("name")))

    def test_adult_without_portal_account_still_gets_the_email(self):
        uninvited = self.env["res.partner"].create(
            {"name": "Parent Sans Compte", "email": "sans.compte@example.invalid"})
        self.env["bf.school.guardian.link"].create(
            {"student_id": self.bravo.id, "guardian_id": uninvited.id})
        post = self._post(school_group_ids=[(6, 0, self.g302.ids)])
        mails = self.env["mail.mail"].sudo().search([("recipient_ids", "in", uninvited.ids)])
        self.assertEqual(len(mails), 1)
        self.assertNotIn(uninvited.id, [u.partner_id.id for u in post._destinataires()],
                         "no account, no portal access")

    def test_adult_without_email_is_counted(self):
        silent = self.env["res.partner"].create({"name": "Parent Sans Courriel"})
        self.env["bf.school.guardian.link"].create(
            {"student_id": self.bravo.id, "guardian_id": silent.id})
        post = self._post(school_group_ids=[(6, 0, self.g302.ids)])
        notes = post.sudo().message_ids.mapped("body")
        self.assertTrue(any("1 adult(s) who receive notices have no email" in (b or "") for b in notes))


@tagged("post_install", "-at_install")
class TestTeacherScope(SchoolMessageCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.teacher = new_test_user(cls.env, login="school_msg_teacher", name="Enseignant 301",
                                    email="prof301@example.invalid",
                                    groups="bf_school_core.group_school_user")
        cls.g301.teacher_ids = [(6, 0, cls.teacher.ids)]

    def _teacher_post(self, **vals):
        return self.env["bf.babillard.post"].with_user(self.teacher).create(dict({
            "name": "Devoirs de la semaine", "audience": "school_families",
            "school_group_ids": [(6, 0, self.g301.ids)], "state": "publie"}, **vals))

    def test_teacher_writes_to_own_group(self):
        post = self._teacher_post()
        self.assertEqual(post.school_id, self.school, "the school comes from the teacher's groups")
        self.assertEqual(post._destinataires(), self.parent_a)
        self.assertEqual(len(self._mails_to(self.parent_a)), 1)

    def test_teacher_cannot_reach_another_group(self):
        with self.assertRaises(AccessError):
            self._teacher_post(school_group_ids=[(6, 0, (self.g301 | self.g302).ids)])

    def test_teacher_cannot_reach_the_whole_school(self):
        with self.assertRaises(AccessError):
            self._teacher_post(school_group_ids=[(5, 0, 0)])

    def test_teacher_cannot_widen_own_post(self):
        post = self._teacher_post(state="brouillon")
        with self.assertRaises(AccessError):
            post.with_user(self.teacher).write({"audience": "tous"})
        with self.assertRaises(AccessError):
            post.with_user(self.teacher).write({"school_group_ids": [(4, self.g302.id)]})

    def test_teacher_cannot_edit_the_office_post(self):
        post = self._post(school_group_ids=[(6, 0, self.g301.ids)])
        self.assertTrue(post.with_user(self.teacher).name, "the teacher reads it")
        with self.assertRaises(AccessError):
            post.with_user(self.teacher).write({"name": "Autre titre"})

    def test_teacher_does_not_see_other_groups_posts(self):
        post = self._post(school_group_ids=[(6, 0, self.g302.ids)])
        self.assertFalse(self.env["bf.babillard.post"].with_user(self.teacher).search(
            [("id", "=", post.id)]))

    def test_teacher_sees_who_has_not_confirmed(self):
        post = self._teacher_post(lecture_requise=True)
        action = post.with_user(self.teacher).action_voir_manquants()
        self.assertEqual(action["domain"], [("id", "in", self.parent_a.partner_id.ids)])
        with self.assertRaises(AccessError):
            self._post(lecture_requise=True, school_group_ids=[(6, 0, self.g302.ids)]
                       ).with_user(self.teacher).action_voir_manquants()


@tagged("post_install", "-at_install")
class TestFrench(SchoolMessageCase):

    def test_email_renders_in_french(self):
        """🔴 The French body is a separate translation: a wrong one crashes only in French."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.parent_a.partner_id.lang = "fr_CA"
        self._post(school_group_ids=[(6, 0, self.g301.ids)])
        mail = self._mails_to(self.parent_a)
        self.assertEqual(mail.subject, "Alpha, Charlie : Sortie au musée")
        self.assertIn("Ouvrir dans le portail des familles", mail.body_html)
