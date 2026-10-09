import re
from datetime import date, datetime, timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSchoolHealth(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.school = env["bf.school"].create({"name": "École des Essais"})
        year = env["bf.school.year"].create({"name": "2026-2027", "school_id": cls.school.id,
                                             "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        year.action_set_current()
        cls.staff = new_test_user(env, login="school_he_staff", groups="bf_school_core.group_school_user")
        cls.nurse = new_test_user(env, login="school_he_nurse", groups="bf_school_health.group_school_health")
        cls.office = new_test_user(env, login="school_he_office", groups="bf_school_core.group_school_manager")
        Partner = env["res.partner"]
        cls.a = Partner.create({"name": "Alpha Essai", "is_student": True, "health_alert_level": "severe",
                                "health_alert": "Allergie grave aux arachides, auto-injecteur dans le sac"})
        cls.b = Partner.create({"name": "Bravo Essai", "is_student": True})
        cls.p = new_test_user(env, login="school_he_p", groups="base.group_portal", email="p@essai.test")
        cls.q = new_test_user(env, login="school_he_q", groups="base.group_portal", email="q@essai.test")
        cls.r = new_test_user(env, login="school_he_r", groups="base.group_portal", email="r@essai.test")
        Link = env["bf.school.guardian.link"]
        Link.create({"student_id": cls.a.id, "guardian_id": cls.p.partner_id.id})
        # r receives the notices for Alpha but does not sign.
        Link.create({"student_id": cls.a.id, "guardian_id": cls.r.partner_id.id, "can_sign": False,
                     "has_parental_authority": False})
        Link.create({"student_id": cls.b.id, "guardian_id": cls.q.partner_id.id})
        env["bf.school.health.record"].create({"student_id": cls.a.id, "allergies": "Arachides : anaphylaxie"})
        cls.today = fields.Date.context_today(env["res.partner"])

    def _med(self, **vals):
        return self.env["bf.school.medication"].create(dict({
            "student_id": self.a.id, "name": "Méthylphénidate 10 mg", "prescriber": "Dre Essai",
            "dosage": "1 comprimé à midi", "route": "oral",
            "date_from": self.today - timedelta(days=1), "date_to": self.today + timedelta(days=90)}, **vals))

    def _signed(self, **vals):
        med = self._med(**vals)
        med.action_ask_family()
        med._school_sign(self.p.partner_id, True, ip="127.0.0.1")
        return med

    # Two levels
    def test_staff_sees_alert_not_record(self):
        student = self.a.with_user(self.staff)
        self.assertIn("arachides", student.health_alert)
        self.assertNotIn("health_record_ids", student.fields_get(), "the record field is hidden from staff")
        self.assertIn("health_record_ids", self.a.with_user(self.nurse).fields_get())
        with self.assertRaises(AccessError):
            student.health_record_ids  # noqa: B018
        with self.assertRaises(AccessError):
            self.env["bf.school.health.record"].with_user(self.staff).search([])
        with self.assertRaises(AccessError):
            self.env["bf.school.medication"].with_user(self.staff).search([])

    def test_administration_is_not_the_health_group(self):
        self.assertFalse(self.office.has_group("bf_school_health.group_school_health"))
        with self.assertRaises(AccessError):
            self.env["bf.school.health.record"].with_user(self.office).search([])

    def test_nurse_reads_the_record(self):
        records = self.env["bf.school.health.record"].with_user(self.nurse).search([("student_id", "=", self.a.id)])
        self.assertEqual(records.allergies, "Arachides : anaphylaxie")

    # Authorisation
    def test_as_needed_needs_conditions(self):
        with self.assertRaises(ValidationError):
            self._med(as_needed=True, as_needed_conditions="  ")
        with self.assertRaises(ValidationError):
            self._med(date_to=self.today - timedelta(days=5))

    def test_text_frozen_once_sent(self):
        med = self._med()
        med.action_ask_family()
        self.assertEqual(med.text_hash, med._fingerprint())
        with self.assertRaises(UserError):
            med.write({"dosage": "2 comprimés"})
        med.write({"storage": "Secrétariat, armoire verrouillée"})  # not what was signed

    def test_only_a_signer_of_this_student_signs(self):
        med = self._med()
        med.action_ask_family()
        for outsider in (self.q.partner_id, self.r.partner_id):
            with self.assertRaises(AccessError):
                med._school_sign(outsider, True)
        self.assertEqual(med.state, "asked")
        med._school_sign(self.p.partner_id, False)
        self.assertEqual(med.state, "refused")
        with self.assertRaises(UserError):
            med._school_sign(self.p.partner_id, True)

    def test_request_mail_to_signers_only(self):
        med = self._med()
        med.action_ask_family()
        mails = self.env["mail.mail"].search([("model", "=", "bf.school.medication"), ("res_id", "=", med.id)])
        self.assertEqual(mails.recipient_ids, self.p.partner_id)

    def test_record_reads_as_the_student(self):
        # The breadcrumb read « bf.school.health.record,1 » and the list showed the ID
        # column only (demo École, 2026-10-02).
        record = self.env["bf.school.health.record"].search([("student_id", "=", self.a.id)])
        self.assertEqual(record.display_name, "Alpha Essai")
        arch = self.env["bf.school.health.record"].get_views([(False, "list")])["views"]["list"]["arch"]
        self.assertIn('name="student_id"', arch)
        self.assertIn('name="allergies"', arch)

    # Register of doses
    def _give(self, user, **vals):
        return self.env["bf.school.medication.administration"].with_user(user).create(
            dict({"student_id": self.a.id, "dose": "1 comprimé"}, **vals))

    def test_no_dose_without_valid_authorisation(self):
        med = self._med()
        with self.assertRaises(UserError):
            self._give(self.nurse, medication_id=med.id)  # not signed
        med.action_ask_family()
        med._school_sign(self.p.partner_id, True)
        self.assertEqual(self._give(self.nurse, medication_id=med.id).given_by_id, self.nurse)
        with self.assertRaises(UserError):
            self._give(self.nurse, medication_id=med.id, given_on=fields.Datetime.now() + timedelta(days=120))
        with self.assertRaises(UserError):
            self._give(self.nurse)  # no authorisation at all
        with self.assertRaises(UserError):
            self._give(self.nurse, student_id=self.b.id, medication_id=med.id)  # another student's
        med.action_revoke()
        with self.assertRaises(UserError):
            self._give(self.nurse, medication_id=med.id)

    def test_staff_gives_only_emergency_epinephrine(self):
        med = self._signed()
        with self.assertRaises(AccessError) as caught:
            self._give(self.staff, medication_id=med.id)
        self.assertIn("authorised by the school", str(caught.exception))
        dose = self._give(self.staff, emergency_epinephrine=True, dose="1 auto-injecteur",
                          given_by_id=self.nurse.id)
        self.assertEqual(dose.given_by_id, self.staff, "the register names who recorded it")
        mails = self.env["mail.mail"].sudo().search([("model", "=", dose._name), ("res_id", "=", dose.id)])
        self.assertEqual(mails.recipient_ids, self.p.partner_id | self.r.partner_id)
        self.assertEqual(mails.mapped("state"), ["sent"] * len(mails), "sent at once, not queued")
        # The staff member sees what they recorded, nothing else.
        nurse_dose = self._give(self.nurse, medication_id=med.id)
        seen = self.env["bf.school.medication.administration"].with_user(self.staff).search([])
        self.assertIn(dose, seen)
        self.assertNotIn(nurse_dose, seen)

    def test_epinephrine_mail_in_french(self):
        self.p.partner_id.lang = "fr_CA"
        if not self.env["res.lang"]._lang_get("fr_CA"):
            return
        dose = self._give(self.staff, emergency_epinephrine=True, dose="1 auto-injecteur")
        mail = self.env["mail.mail"].sudo().search([("model", "=", dose._name), ("res_id", "=", dose.id),
                                                    ("recipient_ids", "in", self.p.partner_id.ids)])
        self.assertIn("épinéphrine", mail.body_html)
        self.assertIn("URGENT", mail.subject)

    # Portal
    def test_portal_page_and_answer(self):
        med = self._med()
        med.action_ask_family()
        other = self._med(student_id=self.b.id, name="Autre famille")
        other.action_ask_family()
        self.authenticate("school_he_p", "school_he_p")
        page = self.url_open("/my/school/health").text
        self.assertIn("Méthylphénidate", page)
        self.assertIn("arachides", page)
        self.assertNotIn("Autre famille", page)
        self.assertNotIn("anaphylaxie", page, "the detailed record never reaches the portal")
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        response = self.url_open("/my/school/health/%s/answer" % other.id,
                                 data={"decision": "accept", "csrf_token": token})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(other.state, "asked")
        self.url_open("/my/school/health/%s/answer" % med.id, data={"decision": "accept", "csrf_token": token})
        self.assertEqual(med.state, "active")
        self.assertEqual(med.signed_by_id, self.p.partner_id)
        self.assertTrue(med.signed_ip)

    def test_emergency_form_proposes_the_dose_in_the_user_language(self):
        Dose = self.env["bf.school.medication.administration"].with_context(default_emergency_epinephrine=True)
        self.assertEqual(Dose.default_get(["dose", "emergency_epinephrine"])["dose"], "1 auto-injector")
        if self.env["res.lang"]._lang_get("fr_CA").active:
            self.assertEqual(Dose.with_context(lang="fr_CA").default_get(["dose", "emergency_epinephrine"])["dose"],
                             "1 auto-injecteur")

    # Adversarial review (2026-09-27)
    def test_state_and_signature_are_not_written_by_hand(self):
        med = self._med()
        nurse_med = med.with_user(self.nurse)
        for vals in ({"state": "active"}, {"signed_by_id": self.p.partner_id.id},
                     {"signed_on": fields.Datetime.now()}, {"text_hash": "x"}):
            with self.assertRaises(UserError):
                nurse_med.write(vals)
        signed = self._signed()
        with self.assertRaises(UserError):
            signed.with_user(self.nurse).write({"state": "draft"})
        self.assertEqual(signed.state, "active")

    def test_a_refusal_after_a_yes_stops_the_medication(self):
        other = new_test_user(self.env, login="school_he_p2", groups="base.group_portal", email="p2@essai.test")
        self.env["bf.school.guardian.link"].create({"student_id": self.a.id, "guardian_id": other.partner_id.id})
        med = self._signed()
        self.assertEqual(med.state, "active")
        med._school_sign(other.partner_id, False, ip="127.0.0.1")
        self.assertEqual(med.state, "refused")
        self.assertEqual(med.signed_by_id, other.partner_id)
        self.assertFalse(med._is_valid_on(self.today))

    def test_a_dose_is_not_rewritten(self):
        med = self._signed()
        dose = self.env["bf.school.medication.administration"].with_user(self.nurse).create(
            {"student_id": self.a.id, "medication_id": med.id, "dose": "1 comprimé"})
        for vals in ({"dose": "2 comprimés"}, {"given_on": fields.Datetime.now() - timedelta(days=3)},
                     {"given_by_id": self.staff.id}, {"emergency_epinephrine": True}):
            with self.assertRaises(UserError):
                dose.write(vals)
        dose.write({"note": "Pris avec de l'eau"})

    def test_epinephrine_cannot_borrow_another_students_medication(self):
        med = self._signed()
        with self.assertRaises(UserError):
            self.env["bf.school.medication.administration"].with_user(self.staff).create({
                "student_id": self.b.id, "medication_id": med.id, "dose": "1 auto-injecteur",
                "emergency_epinephrine": True})

    def test_portal_health_is_for_parental_authority(self):
        self._signed()
        self.authenticate("school_he_r", "school_he_r")
        page = self.url_open("/my/school/health").text
        self.assertNotIn("Méthylphénidate", page, "r receives notices but holds no parental authority")

    def test_dose_has_a_name(self):
        self.env["res.lang"]._activate_lang("en_US")  # inactive on a base installed in fr_CA
        dose = self._give(self.staff, emergency_epinephrine=True, given_on=datetime(2026, 10, 1, 15, 47))
        self.assertEqual(dose.with_context(lang="en_US").display_name, "Alpha Essai · Epinephrine · 1 October, 11:47")

    def test_dose_name_follows_the_language(self):
        # The name holds a translation: read in French first, it stayed French in English
        # (cached without the language, website + fr_CA bench, 2026-10-03).
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["res.lang"]._activate_lang("en_US")
        self.env["ir.module.module"]._load_module_terms(["bf_school_health"], ["fr_CA"])
        dose = self._give(self.staff, emergency_epinephrine=True, given_on=datetime(2026, 10, 1, 15, 47))
        self.assertEqual(dose.with_context(lang="fr_CA").display_name, "Alpha Essai · Épinéphrine · 1 octobre, 11:47")
        self.assertEqual(dose.with_context(lang="en_US").display_name, "Alpha Essai · Epinephrine · 1 October, 11:47")

    def test_emergency_dose_never_names_an_authorisation(self):
        # The staff member reads their own dose, not the authorisations: the name (breadcrumb,
        # email header) must not reveal one (adversarial review, 2026-10-03).
        self.env["res.lang"]._activate_lang("en_US")
        epi = self._signed(name="EpiPen 0,3 mg", route="epinephrine", dosage="1 auto-injecteur")
        dose = self._give(self.staff, emergency_epinephrine=True, medication_id=epi.id)
        name = dose.with_user(self.staff).with_context(lang="en_US").display_name
        self.assertIn("Epinephrine", name)
        self.assertNotIn("EpiPen", name)

    def test_former_nurse_reads_their_old_doses(self):
        # Removed from the health group, a staff member still reads the doses they gave (own
        # doses rule): the name must not open the authorisations (adversarial review, 2026-10-03).
        self.env["res.lang"]._activate_lang("en_US")
        nurse = new_test_user(self.env, login="school_he_former",
                              groups="bf_school_core.group_school_user,bf_school_health.group_school_health")
        med = self._signed()
        dose = self._give(nurse, medication_id=med.id)
        nurse.groups_id = [(3, self.env.ref("bf_school_health.group_school_health").id)]
        self.env.invalidate_all()
        name = dose.with_user(nurse).with_context(lang="en_US").display_name
        self.assertIn("Medication", name)
        self.assertNotIn(med.name, name)

    def test_emergency_box_does_not_carry_another_medication(self):
        med = self._signed()  # oral methylphenidate
        with self.assertRaises(UserError):
            self._give(self.staff, emergency_epinephrine=True, medication_id=med.id)

    # Creation guards (2026-10-08)
    def test_nurse_creates_no_authorisation_already_signed(self):
        # 🔴 write() refused it, create() took an authorisation born "active" with a parent as
        # signatory: administrable without any signature.
        Med = self.env["bf.school.medication"].with_user(self.nurse)
        vals = {"student_id": self.a.id, "name": "Méthylphénidate 10 mg", "prescriber": "Dre Essai",
                "dosage": "1 comprimé à midi", "route": "oral",
                "date_from": self.today - timedelta(days=1), "date_to": self.today + timedelta(days=90)}
        for forged in ({"state": "active"}, {"signed_by_id": self.p.partner_id.id}, {"access_token": "connu"},
                       {"text_hash": "0" * 64}):
            with self.assertRaises(UserError):
                Med.create(dict(vals, **forged))
        self.assertEqual(Med.create(dict(vals, state="draft")).state, "draft")  # the form sends it
        med = Med.with_context(default_state="active", default_signed_by_id=self.p.partner_id.id).create(
            dict(vals))
        self.assertEqual((med.state, med.signed_by_id.id), ("draft", False))
        self.env["ir.default"].set("bf.school.medication", "state", "active", user_id=self.nurse.id)
        med = Med.create(dict(vals))
        self.assertFalse(med._is_valid_on(self.today))
        med.action_ask_family()
        self.assertEqual(med.state, "asked")
