from datetime import date, datetime

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class ConductCase(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.school = env["bf.school"].create({"name": "École des Essais"})
        year = env["bf.school.year"].create({"name": "2026-2027", "school_id": cls.school.id,
                                             "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        year.action_set_current()
        cls.t1 = new_test_user(env, login="school_cnd_t1", groups="bf_school_core.group_school_user")
        cls.t2 = new_test_user(env, login="school_cnd_t2", groups="bf_school_core.group_school_user")
        Group = env["bf.school.group"]
        cls.g1 = Group.create({"name": "301", "school_id": cls.school.id, "year_id": year.id,
                               "teacher_ids": [(6, 0, cls.t1.ids)]})
        cls.g2 = Group.create({"name": "Sec 3", "school_id": cls.school.id, "year_id": year.id,
                               "teacher_ids": [(6, 0, cls.t2.ids)]})
        today = fields.Date.context_today(env["res.partner"])
        Partner = env["res.partner"]
        cls.kid = Partner.create({"name": "Enfant Essai", "is_student": True,
                                  "student_birthdate": today - relativedelta(years=9)})
        cls.teen = Partner.create({"name": "Ado Essai", "is_student": True,
                                   "student_birthdate": today - relativedelta(years=15)})
        env["bf.school.enrollment"].create({"student_id": cls.kid.id, "group_id": cls.g1.id})
        env["bf.school.enrollment"].create({"student_id": cls.teen.id, "group_id": cls.g2.id})
        cls.p = new_test_user(env, login="school_cnd_p", name="Parent P", email="cnd.p@example.invalid",
                              groups="base.group_portal")
        cls.q = new_test_user(env, login="school_cnd_q", name="Parent Q", email="cnd.q@example.invalid",
                              groups="base.group_portal")
        Link = env["bf.school.guardian.link"]
        Link.create({"student_id": cls.kid.id, "guardian_id": cls.p.partner_id.id})
        Link.create({"student_id": cls.teen.id, "guardian_id": cls.q.partner_id.id})
        ref = env.ref
        cls.phone, cls.sexual, cls.bullying = (ref("bf_school_conduct.rule_phone"), ref("bf_school_conduct.rule_sexual"),
                                               ref("bf_school_conduct.rule_bullying"))
        cls.warning, cls.reflection, cls.suspension = (ref("bf_school_conduct.sanction_warning"),
                                                       ref("bf_school_conduct.sanction_reflection"),
                                                       ref("bf_school_conduct.sanction_internal"))

    def _incident(self, student=None, rule=None, **vals):
        return self.env["bf.school.incident"].create(dict({
            "student_id": (student or self.kid).id, "rule_id": (rule or self.phone).id,
            "description": "Cellulaire en classe.", "date": datetime(2026, 10, 5, 14, 0)}, **vals))

    def _mails_to(self, partner):
        return self.env["mail.mail"].sudo().search([("recipient_ids", "in", partner.ids)])


@tagged("post_install", "-at_install")
class TestConduct(ConductCase):

    def test_incident_has_a_name(self):
        # The breadcrumb read « bf.school.incident,1 » (demo École, 2026-10-02).
        incident = self._incident()
        self.assertEqual(incident.display_name, "Enfant Essai · %s" % self.phone.name)

    def test_graduated_suggestion(self):
        first = self._incident()
        self.assertEqual((first.prior_count, first.suggested_sanction_id), (0, self.warning))
        second = self._incident(date=datetime(2026, 10, 6, 9, 0))
        self.assertEqual((second.prior_count, second.suggested_sanction_id), (1, self.reflection))
        other_rule = self._incident(rule=self.bullying, date=datetime(2026, 10, 7, 9, 0))
        self.assertEqual(other_rule.prior_count, 0, "counted per rule")

    def test_suspension_needs_reasons_and_support(self):
        incident = self._incident(sanction_id=self.suspension.id)
        with self.assertRaises(UserError):
            incident.action_inform_parents()
        incident.write({"suspension_reasons": "Violence répétée", "support_measures": "Rencontre avec la TES"})
        incident.action_inform_parents()
        self.assertTrue(incident.parents_informed_on)

    def test_sexual_violence_from_14_needs_consent(self):
        incident = self._incident(student=self.teen, rule=self.sexual, description="Signalement.")
        with self.assertRaises(UserError):
            incident.action_inform_parents()
        self.assertFalse(self._mails_to(self.q.partner_id))
        incident.student_consent = True
        incident.action_inform_parents()
        self.assertEqual(len(self._mails_to(self.q.partner_id)), 1)

    def test_sexual_violence_under_14_informs(self):
        incident = self._incident(rule=self.sexual, description="Signalement.")
        incident.action_inform_parents()
        self.assertEqual(len(self._mails_to(self.p.partner_id)), 1)

    def test_teacher_sees_own_students_only(self):
        incident = self._incident()
        self.assertTrue(self.env["bf.school.incident"].with_user(self.t1).search([("id", "=", incident.id)]))
        self.assertFalse(self.env["bf.school.incident"].with_user(self.t2).search([("id", "=", incident.id)]))
        with self.assertRaises(AccessError):
            self.env["bf.school.incident"].with_user(self.t2).create({
                "student_id": self.kid.id, "rule_id": self.phone.id, "description": "x"})

    def test_annual_report_counts_violence_only(self):
        self._incident(rule=self.bullying)
        self._incident(rule=self.bullying, date=datetime(2026, 11, 2, 9, 0))
        self._incident()
        report = self.env["bf.school.incident"]._annual_report(date(2026, 8, 27), date(2027, 6, 23))
        self.assertEqual(report, {self.bullying.name: 2})

    def test_monthly_reminder(self):
        followup = self.env["bf.school.followup"].create(
            {"student_id": self.kid.id, "case": "failure_risk", "responsible_id": self.t1.id})
        self.env["bf.school.followup"]._cron_monthly_reminder()
        self.assertEqual(followup.activity_ids.user_id, self.t1)
        self.assertIn(self.kid.name, followup.display_name, "a readable name, not bf.school.followup,N")
        self.assertFalse(self.env["mail.mail"].search([("recipient_ids", "in", self.t1.partner_id.ids)]),
                         "no Odoo « assigned to you » notice: the to-do stays in the teacher's activities")
        followup.activity_ids.unlink()
        self.env["bf.school.followup.communication"].create(
            {"followup_id": followup.id, "summary": "Progrès en lecture.", "channel": "phone"})
        self.env["bf.school.followup"]._cron_monthly_reminder()
        self.assertFalse(followup.activity_ids, "news given this month: no reminder")
        self.assertFalse(self._mails_to(self.p.partner_id), "a phone call is noted, not emailed")

    def test_email_news_reaches_the_family(self):
        followup = self.env["bf.school.followup"].create(
            {"student_id": self.kid.id, "case": "conduct", "responsible_id": self.t1.id})
        self.env["bf.school.followup.communication"].create(
            {"followup_id": followup.id, "summary": "Semaine calme.", "channel": "email"})
        mails = self._mails_to(self.p.partner_id)
        self.assertEqual(len(mails), 1)
        self.assertIn("Semaine calme", mails.body_html)


@tagged("post_install", "-at_install")
class TestConductPortal(ConductCase):

    def test_family_sees_only_what_it_was_told(self):
        told = self._incident(description="Cellulaire pendant l'examen.")
        told.action_inform_parents()
        self._incident(description="Note interne jamais communiquée.", date=datetime(2026, 10, 9, 9, 0))
        self.authenticate("school_cnd_p", "school_cnd_p")
        page = self.url_open("/my/school/conduct").text
        self.assertIn("Cellulaire pendant l", page)
        self.assertNotIn("Note interne jamais", page)

    def test_other_family_sees_nothing(self):
        self._incident().action_inform_parents()
        self.authenticate("school_cnd_q", "school_cnd_q")
        self.assertNotIn("Enfant Essai", self.url_open("/my/school/conduct").text)

    def test_portal_has_no_rpc_access(self):
        with self.assertRaises(AccessError):
            self.env["bf.school.incident"].with_user(self.p).search([])


@tagged("post_install", "-at_install")
class TestConductFrench(ConductCase):

    def test_states_agree_with_un_manquement(self):
        # « Ouvert » next to « Fermée » for the same breach (adversarial review, 2026-10-03).
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"]._load_module_terms(["bf_school_conduct"], ["fr_CA"])
        labels = dict(self.env["bf.school.incident"]._fields["state"]._description_selection(
            self.env(context={"lang": "fr_CA"})))
        self.assertEqual((labels["open"], labels["closed"]), ("Ouvert", "Fermé"))

    def test_emails_render_in_french(self):
        """🔴 Each French body is a separate translation: a wrong one crashes only in French."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.p.partner_id.lang = "fr_CA"
        self._incident().action_inform_parents()
        followup = self.env["bf.school.followup"].create(
            {"student_id": self.kid.id, "case": "conduct", "responsible_id": self.t1.id})
        self.env["bf.school.followup.communication"].create(
            {"followup_id": followup.id, "summary": "Semaine calme.", "channel": "email"})
        mails = self._mails_to(self.p.partner_id).sorted("id")
        self.assertEqual(mails.mapped("subject"), ["Enfant Essai : suivi de l'école",
                                                   "Enfant Essai : nouvelles de l'école"])
        self.assertIn("L'école vous informe d'une situation", mails[0].body_html)
        self.assertIn("Voir le suivi", mails[1].body_html)


@tagged("post_install", "-at_install")
class TestConductGuards(ConductCase):
    """Adversarial review (2026-09-27): what the screen hides, RPC could write."""

    def test_teacher_cannot_mark_the_parents_informed(self):
        incident = self._incident(student=self.teen, rule=self.sexual).with_user(self.t2)
        with self.assertRaises(UserError):
            incident.write({"parents_informed_on": fields.Datetime.now()})
        with self.assertRaises(UserError):
            incident.action_inform_parents()
        self.assertFalse(incident.parents_informed_on)

    def test_teacher_cannot_move_a_breach_to_another_student(self):
        incident = self._incident().with_user(self.t1)
        with self.assertRaises(UserError):
            incident.write({"student_id": self.teen.id})

    def test_conduct_is_for_parental_authority(self):
        step = new_test_user(self.env, login="school_cnd_step", name="Beau-parent", email="cnd.step@example.invalid",
                             groups="base.group_portal")
        self.env["bf.school.guardian.link"].create({"student_id": self.kid.id, "guardian_id": step.partner_id.id,
                                                    "has_parental_authority": False, "can_sign": False,
                                                    "receives_notices": True})
        incident = self._incident(description="Cellulaire pendant l'examen.")
        incident.action_inform_parents()
        self.assertTrue(self._mails_to(self.p.partner_id))
        self.assertFalse(self._mails_to(step.partner_id), "notices without parental authority")
        self.authenticate("school_cnd_step", "school_cnd_step")
        self.assertNotIn("Cellulaire pendant", self.url_open("/my/school/conduct").text)
        self.authenticate("school_cnd_p", "school_cnd_p")
        self.assertIn("Cellulaire pendant", self.url_open("/my/school/conduct").text)

    def test_teacher_of_a_past_year_does_not_read_the_breach(self):
        incident = self._incident()
        self.assertTrue(self.env["bf.school.incident"].with_user(self.t1).search([("id", "=", incident.id)]))
        self.kid.student_enrollment_ids.write({"state": "left"})
        self.assertFalse(self.env["bf.school.incident"].with_user(self.t1).search([("id", "=", incident.id)]))

    # Creation guards (2026-10-08)
    def test_teacher_creates_no_breach_with_the_parents_informed(self):
        # 🔴 At creation too: a breach born "parents informed" skipped the button's conditions.
        Incident = self.env["bf.school.incident"].with_user(self.t2)
        vals = {"student_id": self.teen.id, "rule_id": self.sexual.id, "description": "Essai."}
        with self.assertRaises(UserError):
            Incident.create(dict(vals, parents_informed_on=fields.Datetime.now()))
        incident = Incident.with_context(default_parents_informed_on=fields.Datetime.now(),
                                         default_reported_by_id=self.t1.id).create(dict(vals))
        self.assertFalse(incident.parents_informed_on)
        self.assertEqual(incident.reported_by_id, self.t2)
        self.env["ir.default"].set("bf.school.incident", "parents_informed_on", "2026-10-01 10:00:00",
                                   user_id=self.t2.id)
        self.assertFalse(Incident.create(dict(vals)).parents_informed_on)
        with self.assertRaises(UserError):
            incident.write({"reported_by_id": self.t1.id})

    def test_the_monthly_news_register_says_who_wrote_it(self):
        # 🔴 Adversarial review (2026-10-08): the register of Régime pédagogique s. 29.2 credited
        # someone else, and the monthly reminder was silenced by writing the computed date.
        followup = self.env["bf.school.followup"].with_user(self.t1).create(
            {"student_id": self.kid.id, "case": "conduct"})
        Communication = self.env["bf.school.followup.communication"].with_user(self.t1)
        news = Communication.with_context(default_author_id=self.t2.id).create(
            {"followup_id": followup.id, "summary": "Bonne semaine.", "channel": "phone", "author_id": self.t2.id})
        self.assertEqual(news.author_id, self.t1)
        # The office may write a communication (a teacher may not at all): not its author.
        office = new_test_user(self.env, login="school_cnd_office_news", groups="bf_school_core.group_school_manager")
        news.with_user(office).write({"summary": "Bonne semaine, devoirs remis."})
        with self.assertRaises(UserError):
            news.with_user(office).write({"author_id": self.t2.id})
        with self.assertRaises(UserError):
            followup.with_user(office).write({"last_communication": date(2030, 1, 1)})
        self.assertEqual(followup.last_communication, news.date)

    def test_a_copy_does_not_inherit_the_parents_informed(self):
        incident = self._incident()
        incident.action_inform_parents()
        self.assertFalse(incident.copy().parents_informed_on)
