from datetime import date

from odoo.exceptions import AccessError
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSchoolPortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.school = env["bf.school"].create({"name": "École des Essais"})
        year = env["bf.school.year"].create({
            "name": "2026-2027", "school_id": cls.school.id,
            "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        year.action_set_current()
        cls.teacher = new_test_user(env, login="school_prof", name="Enseignante Essai",
                                    groups="bf_school_core.group_school_user")
        cls.group = env["bf.school.group"].create({
            "name": "301", "school_id": cls.school.id, "year_id": year.id,
            "teacher_ids": [(6, 0, cls.teacher.ids)]})
        Partner = env["res.partner"]
        cls.child_a = Partner.create({"name": "Enfant Alpha", "is_student": True})
        cls.child_b = Partner.create({"name": "Enfant Bravo", "is_student": True})
        for child in cls.child_a | cls.child_b:
            env["bf.school.enrollment"].create({"student_id": child.id, "group_id": cls.group.id})
        cls.parent_a = new_test_user(env, login="school_parent_a", name="Parent Alpha",
                                     email="parent.a@example.invalid", groups="base.group_portal")
        cls.parent_b = new_test_user(env, login="school_parent_b", name="Parent Bravo",
                                     email="parent.b@example.invalid", groups="base.group_portal")
        Link = env["bf.school.guardian.link"]
        Link.create({"student_id": cls.child_a.id, "guardian_id": cls.parent_a.partner_id.id,
                     "is_payer": True})
        Link.create({"student_id": cls.child_b.id, "guardian_id": cls.parent_b.partner_id.id,
                     "can_pickup": False})

    def test_parent_sees_own_child_only(self):
        self.authenticate("school_parent_a", "school_parent_a")
        page = self.url_open("/my/school").text
        self.assertIn("Enfant Alpha", page)
        self.assertNotIn("Enfant Bravo", page)
        self.assertIn("Enseignante Essai", page)
        self.assertIn("301", page)

    def test_roles_shown_are_this_adults(self):
        self.authenticate("school_parent_b", "school_parent_b")
        page = self.url_open("/my/school").text
        self.assertIn("Enfant Bravo", page)
        self.assertNotIn("May pick up", page)
        self.assertNotIn(">Pays<", page)

    def test_home_counter(self):
        links = self.parent_a.partner_id._school_portal_links()
        self.assertEqual(links.student_id, self.child_a)

    def test_adult_without_child(self):
        new_test_user(self.env, login="school_nobody", groups="base.group_portal")
        self.authenticate("school_nobody", "school_nobody")
        page = self.url_open("/my/school").text
        self.assertIn("has not linked any child", page)

    def test_portal_has_no_rpc_access(self):
        with self.assertRaises(AccessError):
            self.env["bf.school.enrollment"].with_user(self.parent_a).search([])
        with self.assertRaises(AccessError):
            self.env["bf.school.group"].with_user(self.parent_a).search([])


@tagged("post_install", "-at_install")
class TestInvite(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.manager = new_test_user(env, login="school_admin",
                                    groups="bf_school_core.group_school_manager")
        cls.staff = new_test_user(env, login="school_staff", groups="bf_school_core.group_school_user")
        Partner = env["res.partner"]
        cls.child = Partner.create({"name": "Enfant Charlie", "is_student": True})
        cls.with_mail = Partner.create({"name": "Adulte Courriel", "email": "adulte.c@example.invalid"})
        cls.no_mail = Partner.create({"name": "Adulte Sans Courriel"})
        cls.quiet = Partner.create({"name": "Adulte Discret", "email": "adulte.d@example.invalid"})
        Link = env["bf.school.guardian.link"]
        Link.create({"student_id": cls.child.id, "guardian_id": cls.with_mail.id})
        Link.create({"student_id": cls.child.id, "guardian_id": cls.no_mail.id})
        Link.create({"student_id": cls.child.id, "guardian_id": cls.quiet.id,
                     "receives_notices": False})

    def test_invite_counts_and_skips(self):
        action = self.child.with_user(self.manager).action_school_invite_guardians()
        message = action["params"]["message"]
        self.assertIn("1 invited", message)
        self.assertIn("1 without a usable email", message)
        self.assertIn("Adulte Sans Courriel", message)
        self.assertTrue(self.with_mail.user_ids and self.with_mail.user_ids[0]._is_portal())
        self.assertFalse(self.quiet.user_ids, "an adult who receives no notices is not invited")

    def test_invite_twice_is_harmless(self):
        self.child.with_user(self.manager).action_school_invite_guardians()
        action = self.child.with_user(self.manager).action_school_invite_guardians()
        self.assertIn("0 invited, 1 already had access", action["params"]["message"])

    def test_staff_cannot_invite(self):
        with self.assertRaises(AccessError):
            self.child.with_user(self.staff).action_school_invite_guardians()
