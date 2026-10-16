import base64
import hashlib
import json
import os
import shutil
import xml.etree.ElementTree as ET
from datetime import date
from unittest.mock import patch

from cryptography.fernet import Fernet

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.bf_school_charlemagne.charlemagne import gpg
from odoo.addons.bf_school_charlemagne.models.config import CharlemagneConfig


class CharlemagneCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.school = env["bf.school"].create({"name": "École des Essais", "code": "689001"})
        cls.year = env["bf.school.year"].create({
            "name": "2026-2027", "school_id": cls.school.id,
            "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        cls.year.action_set_current()
        cls.year.action_create_terms()
        cls.t1, cls.t2, cls.t3 = cls.year.term_ids.sorted("sequence")
        cls.office = new_test_user(env, login="cha_office", groups="bf_school_core.group_school_manager")
        cls.teacher = new_test_user(env, login="cha_teacher", groups="bf_school_core.group_school_user")
        cls.admin = new_test_user(env, login="cha_admin", groups="bf_school_core.group_school_manager,base.group_system")
        cls.level = env.ref("bf_school_core.level_s3")
        cls.group = env["bf.school.group"].create({
            "name": "301", "school_id": cls.school.id, "year_id": cls.year.id,
            "level_ids": [(6, 0, cls.level.ids)], "teacher_ids": [(6, 0, cls.teacher.ids)], "ministry_group_number": 1})
        Partner = env["res.partner"]
        cls.alpha = Partner.create({"name": "Alpha Essai", "is_student": True, "student_birthdate": date(2012, 1, 5),
                                    "student_permanent_code": "ESSA12010512", "zip": "j1h 1a1"})
        cls.bravo = Partner.create({"name": "Bravo Essai", "is_student": True, "student_birthdate": date(2012, 3, 5),
                                    "student_permanent_code": "ESSB12030512", "zip": "H2X 3Y4"})
        for student in (cls.alpha, cls.bravo):
            env["bf.school.enrollment"].create({"student_id": student.id, "group_id": cls.group.id})
        cls.config = env["bf.school.charlemagne.config"].with_user(cls.office).create({
            "school_id": cls.school.id, "responsible_name": "Gaëlle Côté", "order_code_secondary": "S",
            "order_code_elementary": "P", "teaching_language_en": "AN"})
        cls.history = env["bf.school.course"].create({
            "group_id": cls.group.id, "subject_id": env.ref("bf_school_gradebook.subject_sec_his").id,
            "ministry_course_code": "087304", "ministerial_exam": True, "teacher_ids": [(6, 0, cls.teacher.ids)]})
        cls.pe_course = env["bf.school.course"].create({
            "group_id": cls.group.id, "subject_id": env.ref("bf_school_gradebook.subject_sec_eps").id,
            "ministry_course_code": "043302", "teacher_ids": [(6, 0, cls.teacher.ids)]})

    # --- helpers -------------------------------------------------------------------------------

    def _fill_profile(self, student=None, **extra):
        student = student or self.alpha
        Profile = self.env["bf.school.charlemagne.profile"].with_user(self.office)
        Profile._ensure_profiles(self.year)
        profile = Profile.search([("student_id", "=", student.id), ("year_id", "=", self.year.id)])
        vals = {"names_confirmed": True, "last_name": student.name.split()[-1], "first_name": student.name.split()[0],
                "mother_tongue_code": "001", "home_language_code": "001", "birthplace_mother_code": "001",
                "birthplace_father_code": "001", "free_schooling": "N"}
        vals.update(extra)
        profile.write(dict(vals, names_confirmed=vals["names_confirmed"]))
        profile.write({"names_confirmed": vals["names_confirmed"]})
        return profile

    def _ready(self):
        """Both students' data complete: a transmission passes the checks only when every line does."""
        self._fill_profile(self.alpha)
        self._fill_profile(self.bravo)

    def _batch(self, **extra):
        values = {"config_id": self.config.id, "year_id": self.year.id}
        values.update(extra)
        return self.env["bf.school.charlemagne.batch"].with_user(self.office).create(values)

    def _grade_final(self, student, score, course=None):
        course = course or self.history
        for term in (self.t1, self.t2, self.t3):
            assessment = self.env["bf.school.assessment"].search([("course_id", "=", course.id), ("term_id", "=", term.id)])[:1]
            if not assessment:
                assessment = self.env["bf.school.assessment"].create(
                    {"name": "E%s" % term.sequence, "course_id": course.id, "term_id": term.id})
            assessment.result_ids.filtered(lambda r: r.student_id == student).write({"score": float(score)})

    def _close_third_term(self):
        self.t3.with_user(self.office).action_close()

    def _ledger(self, key):
        return self.env["bf.school.charlemagne.ledger"].sudo().search([("key", "=", key), ("mode", "=", "simulation")])

    def _run(self, batch, accept=True):
        """collect, build, seal, send: in simulation the answer comes back at once."""
        batch.action_collect()
        batch.action_build()
        batch.action_seal()
        batch.action_send()
        return batch

    def _tx(self, batch, kind=None, code=None):
        return batch.transaction_ids.filtered(lambda t: (not kind or t.kind == kind) and (not code or t.code == code))

    def _xml(self, batch):
        return ET.fromstring(base64.b64decode(batch.sudo().xml))


@tagged("post_install", "-at_install")
class TestProfile(CharlemagneCase):

    def test_a_profile_is_made_for_each_enrolled_student_with_names_to_confirm(self):
        profiles = self.env["bf.school.charlemagne.profile"].with_user(self.office)._ensure_profiles(self.year)
        self.assertEqual(profiles.student_id, self.alpha | self.bravo)
        alpha = profiles.filtered(lambda p: p.student_id == self.alpha)
        self.assertEqual((alpha.first_name, alpha.last_name, alpha.names_confirmed), ("Alpha", "Essai", False))
        self.assertEqual(alpha.postal_code, "J1H1A1", "the postal code without its space, in capitals")
        self.assertFalse(self.env["bf.school.charlemagne.profile"]._ensure_profiles(self.year), "once")

    def test_next_year_starts_from_this_years_codes(self):
        self._fill_profile(mother_tongue_code="002")
        year2 = self.env["bf.school.year"].create({
            "name": "2027-2028", "school_id": self.school.id, "date_start": date(2027, 8, 27), "date_end": date(2028, 6, 23)})
        group2 = self.env["bf.school.group"].create({"name": "401", "school_id": self.school.id, "year_id": year2.id})
        self.env["bf.school.enrollment"].create({"student_id": self.alpha.id, "group_id": group2.id})
        profile = self.env["bf.school.charlemagne.profile"]._ensure_profiles(year2).filtered(lambda p: p.student_id == self.alpha)
        self.assertEqual((profile.mother_tongue_code, profile.names_confirmed, profile.last_name), ("002", True, "Essai"))

    def test_changing_a_name_unconfirms_it(self):
        profile = self._fill_profile()
        self.assertTrue(profile.names_confirmed)
        profile.with_user(self.office).write({"last_name": "Essay"})
        self.assertFalse(profile.names_confirmed)

    def test_the_declaration_carries_the_guides_elements(self):
        values = self._fill_profile(class_code="3")._dj_values()
        self.assertEqual(values["CD_PERMN"], "ESSA12010512")
        self.assertEqual((values["NOM"], values["PRENM"]), ("Essai", "Alpha"))
        self.assertEqual(values["AN_SCOLR_PPS"], "20262027")
        self.assertEqual((values["CYCLE_ENSEI"], values["AN_ENSEI"]), ("2", "1"), "secondary 3: second cycle, first year")
        self.assertEqual(values["ORDRE_ENSEI"], "S")
        self.assertEqual(values["LANG_ENSEI"], "FR")
        self.assertEqual(values["CD_POSTL_RESDN"], "J1H1A1")
        self.assertEqual(values["DT_DEBUT_FREQN"], str(self.alpha.student_enrollment_ids[:1].date_start))
        self.assertEqual(values["CHFFREJS_DECLR_JEUNE_SPECF"], {"IND_ELEVE_PENSN": "N", "GROUP_ELEVE": "1"})
        self.assertNotIn("CD_PROGR", values, "an empty element is absent, the guide's way to say no value")

    def test_a_declaration_does_not_leave_with_unconfirmed_names(self):
        profile = self._fill_profile(names_confirmed=False)
        with self.assertRaises(ValidationError):
            profile._dj_values()

    def test_only_the_office_enters_the_data(self):
        with self.assertRaises(AccessError):
            self.env["bf.school.charlemagne.profile"].with_user(self.teacher).search([])

    def test_the_ministry_group_number_is_between_1_and_99_and_unique_in_the_year(self):
        with self.assertRaises(ValidationError):
            self.group.ministry_group_number = 100
        other = self.env["bf.school.group"].create({"name": "302", "school_id": self.school.id, "year_id": self.year.id})
        with self.assertRaises(ValidationError):
            other.ministry_group_number = 1
        other.ministry_group_number = 2


@tagged("post_install", "-at_install")
class TestDeclarations(CharlemagneCase):

    def test_a_first_declaration_is_a_creation(self):
        self._ready()
        batch = self._batch()
        batch.action_collect()
        creations = self._tx(batch, "DJ", "CRE")
        self.assertEqual(creations.student_id, self.alpha | self.bravo)
        self.assertEqual(creations.filtered(lambda t: t.student_id == self.alpha).ident,
                         "ESSA12010512-DJCRE-%s" % self.year.id)

    def test_a_student_the_collection_cannot_declare_is_shown_with_the_reason(self):
        self._fill_profile(self.alpha)
        batch = self._batch()
        batch.action_collect()
        stuck = self._tx(batch, "DJ").filtered(lambda t: t.student_id == self.bravo)
        self.assertEqual(stuck.state, "invalid")
        self.assertIn("Confirm the names", stuck.message_ids.text)

    def test_a_file_with_one_transaction_that_fails_is_not_sent_partially(self):
        self._fill_profile(self.alpha)
        self._fill_profile(self.bravo, mother_tongue_code=False)
        batch = self._batch()
        batch.action_collect()
        answer = batch.action_build()
        self.assertEqual(answer["tag"], "display_notification")
        self.assertEqual((batch.state, batch.invalid_count), ("draft", 1))
        message = self._tx(batch, "DJ").filtered(lambda t: t.student_id == self.bravo).message_ids
        self.assertIn("CD_LANG_MATRN_DETL", " ".join(message.mapped("text")))
        self.assertFalse(batch.sudo().xml)
        with self.assertRaises(UserError):
            batch.action_seal()

    def test_the_file_the_ministry_receives(self):
        self._ready()
        batch = self._batch()
        batch.action_collect()
        batch.action_build()
        self.assertEqual((batch.state, batch.invalid_count), ("built", 0))
        blob = base64.b64decode(batch.sudo().xml)
        self.assertTrue(blob.startswith(b'<?xml version="1.0" encoding="ISO-8859-1"?>'))
        self.assertIn("Gaëlle Côté".encode("latin-1"), blob)
        root = ET.fromstring(blob)
        demand = root.find("demande")
        self.assertEqual((root.findtext("codeOrganismeTransmetteur"), demand.findtext("typeTransaction"),
                          demand.findtext("nbTransactionsSystemeTransmises")), ("689001", "DJ", "2"))
        declaration = demand.find("transactionSysteme/parametreTransaction/CHFFREDR_DECLR_DJ_CRE")
        self.assertEqual([child.tag for child in declaration][:5], ["CD_PERMN", "NOM", "PRENM", "AN_SCOLR_PPS", "CD_ORGNS"])
        self.assertEqual(batch.xml_sha256, hashlib.sha256(blob).hexdigest())

    def test_the_whole_circuit_in_simulation_and_the_ledger(self):
        self._ready()
        batch = self._run(self._batch())
        self.assertEqual(batch.state, "answered")
        self.assertEqual({t.state for t in batch.transaction_ids}, {"accepted"})
        self.assertEqual(batch.command_number, "%07d" % batch.id)
        key = "DJ|%s|%s" % (self.alpha.id, self.year.id)
        self.assertEqual(self._ledger(key).state, "accepted")
        self.assertEqual(self.env["bf.school.charlemagne.profile"].search([("student_id", "=", self.alpha.id)]).ledger_state,
                         "declared")

    def test_the_plain_file_is_dropped_once_sealed(self):
        self._ready()
        batch = self._batch()
        batch.action_collect()
        batch.action_build()
        digest = batch.xml_sha256
        batch.action_seal()
        self.assertFalse(batch.sudo().xml)
        self.assertEqual(batch.xml_sha256, digest, "what was sent stays identified")
        self.assertFalse(batch.sudo().sealed, "a rehearsal keeps no encrypted copy of the students")
        self.assertTrue(batch.sealed_sha256)

    def test_nothing_new_nothing_to_send(self):
        self._ready()
        self._run(self._batch())
        again = self._batch()
        again.action_collect()
        self.assertFalse(again.transaction_ids)
        with self.assertRaises(UserError):
            again.action_build()

    def test_a_change_after_acceptance_is_a_modification(self):
        self._ready()
        self._run(self._batch())
        profile = self.env["bf.school.charlemagne.profile"].search([("student_id", "=", self.alpha.id)])
        self.assertEqual(profile.ledger_state, "declared")
        profile.with_user(self.office).write({"postal_code": "J1X2Y3"})
        self.assertEqual(profile.ledger_state, "changed")
        batch = self._batch()
        batch.action_collect()
        modification = self._tx(batch, "DJ", "MOD")
        self.assertEqual(modification.student_id, self.alpha)
        self.assertEqual(modification._values()["CD_POSTL_RESDN"], "J1X2Y3")
        self.assertFalse(self._tx(batch, "DJ", "CRE"))

    def test_a_new_start_date_names_the_old_one_the_way_the_guide_asks(self):
        self._ready()
        self._run(self._batch())
        old = self.alpha.student_enrollment_ids[:1].date_start
        self.env["bf.school.charlemagne.profile"].search([("student_id", "=", self.alpha.id)]).with_user(
            self.office).write({"start_date": date(2026, 9, 8)})
        batch = self._batch()
        batch.action_collect()
        values = self._tx(batch, "DJ", "MOD")._values()
        self.assertEqual((values["DT_DEBUT_FREQN"], values["ANCIE_DT_DEBUT_FREQN"]), ("2026-09-08", str(old)))

    def test_a_rejection_leaves_the_ledger_alone_so_the_next_try_is_a_creation(self):
        self._ready()
        batch = self._batch()
        batch.action_collect()
        batch.action_build()
        batch.action_seal()
        batch.action_send()
        tx = self._tx(batch, "DJ", "CRE").filtered(lambda t: t.student_id == self.alpha)
        # What the Ministry says instead of an acceptance:
        tx.sudo().state = "rejected"
        self.env["bf.school.charlemagne.ledger"].sudo().search([("key", "=", tx.key)]).unlink()
        again = self._batch()
        again.action_collect()
        self.assertEqual(self._tx(again, "DJ", "CRE").student_id, self.alpha)

    def test_a_cancelled_declaration_is_asked_for_once(self):
        self._ready()
        self._run(self._batch())
        profile = self.env["bf.school.charlemagne.profile"].search([("student_id", "=", self.alpha.id)])
        profile.with_user(self.office).write({"withdraw": True})
        batch = self._batch()
        batch.action_collect()
        cancel = self._tx(batch, "DJ", "SUP")
        self.assertEqual(set(cancel._values()), {"CD_PERMN", "NOM", "PRENM", "AN_SCOLR_PPS", "CD_ORGNS", "TYPE_DECLR",
                                                  "DT_DEBUT_FREQN"})
        batch.action_build()
        batch.action_seal()
        batch.action_send()
        self.assertEqual(self._ledger(cancel.key).state, "withdrawn")
        again = self._batch()
        again.action_collect()
        self.assertFalse(again.transaction_ids)


@tagged("post_install", "-at_install")
class TestResultsAndExams(CharlemagneCase):

    def _results_batch(self):
        self._close_third_term()
        return self._batch(include_declarations=False, include_exams=False)

    def test_the_local_result_is_the_schools_final_result_with_its_rank(self):
        self._grade_final(self.alpha, 77, self.pe_course)
        self._grade_final(self.bravo, 65, self.pe_course)
        batch = self._results_batch()
        batch.action_collect()
        tx = self._tx(batch, "RJ", "CRE").filtered(lambda t: t.student_id == self.alpha)
        self.assertEqual(tx._values(), {
            "CD_PERMN": "ESSA12010512", "CD_ORGNS": "689001", "CD_COURS": "043302", "AN_OBTEN_RESLT": "2027",
            "SESN_OBTEN_RESLT": "06", "NO_GROUP_ELEVE": "01", "NOTE_ECOLE_BRUT": "77", "RANG_CINQM": "1"})
        other = self._tx(batch, "RJ", "CRE").filtered(lambda t: t.student_id == self.bravo)
        self.assertEqual(other._values()["RANG_CINQM"], "2")
        batch.action_build()
        element = self._xml(batch).find(".//CHSIRERL_RESLT_LOCAL_RJ_CRE")
        self.assertEqual((element.findtext("NOTE_ECOLE_BRUT"), element.findtext("RANG_CINQM")), ("77", "1"))

    def test_a_course_with_a_ministerial_exam_has_no_local_result(self):
        self._grade_final(self.alpha, 77, self.history)
        batch = self._results_batch()
        batch.action_collect()
        self.assertFalse(self._tx(batch, "RJ"), "the Ministry refuses a local result for a course with an exam (CHS-340701)")

    def test_no_local_result_before_the_third_term_is_closed(self):
        self._grade_final(self.alpha, 77, self.pe_course)
        batch = self._batch(include_declarations=False, include_exams=False)
        batch.action_collect()
        self.assertFalse(batch.transaction_ids)
        self.assertIn("third term is not closed", batch.collect_log)

    def test_a_local_result_changed_after_acceptance_is_a_modification(self):
        self._grade_final(self.alpha, 77, self.pe_course)
        self._run(self._results_batch())
        self._grade_final(self.alpha, 80, self.pe_course)
        batch = self._batch(include_declarations=False, include_exams=False)
        batch.action_collect()
        self.assertEqual(self._tx(batch, "RJ", "MOD")._values()["NOTE_ECOLE_BRUT"], "80")
        self.assertFalse(self._tx(batch, "RJ", "CRE"))

    def test_what_cannot_be_collected_is_said(self):
        self._grade_final(self.alpha, 77, self.pe_course)
        self.group.ministry_group_number = 0
        batch = self._results_batch()
        batch.action_collect()
        self.assertFalse(batch.transaction_ids)
        self.assertIn("no Ministry number", batch.collect_log)

    def test_a_course_without_third_term_result_is_left_out_and_said(self):
        assessment = self.env["bf.school.assessment"].create(
            {"name": "E1", "course_id": self.pe_course.id, "term_id": self.t1.id})
        assessment.result_ids.filtered(lambda r: r.student_id == self.alpha).write({"score": 70.0})
        batch = self._results_batch()
        batch.action_collect()
        self.assertFalse(batch.transaction_ids)
        self.assertIn("third term", batch.collect_log)

    def test_a_student_without_a_permanent_code_is_shown_not_dropped(self):
        self.bravo.student_permanent_code = False
        self._grade_final(self.bravo, 70, self.pe_course)
        batch = self._results_batch()
        batch.action_collect()
        stuck = self._tx(batch, "RJ")
        self.assertEqual((stuck.student_id, stuck.state), (self.bravo, "invalid"))

    def test_a_student_who_left_is_not_sent_nor_ranked(self):
        self._grade_final(self.alpha, 90, self.pe_course)
        self._grade_final(self.bravo, 70, self.pe_course)
        self.env["bf.school.enrollment"].search([("student_id", "=", self.alpha.id)]).action_leave()
        batch = self._results_batch()
        batch.action_collect()
        sent = self._tx(batch, "RJ")
        self.assertEqual(sent.student_id, self.bravo)
        self.assertEqual(sent._values()["RANG_CINQM"], "1")

    def _exam(self, student, **vals):
        values = {"course_id": self.history.id, "student_id": student.id, "exam_year": 2027, "exam_month": 6}
        values.update(vals)
        return self.env["bf.school.exam.result"].create(values)

    def test_a_registration_is_made_once(self):
        self._exam(self.alpha)
        first = self._batch(include_declarations=False, include_results=False)
        first.action_collect()
        registration = self._tx(first, "IJ", "CRE")
        self.assertEqual(registration._values()["AN_EPREU_DNR"], "2027")
        self.assertEqual(registration._values()["SESN_EPREU_DNR"], "06")
        first.action_build()
        first.action_seal()
        first.action_send()
        second = self._batch(include_declarations=False, include_results=False)
        second.action_collect()
        self.assertFalse(second.transaction_ids)

    def test_the_exam_result_carries_the_schools_result_and_the_rank_together(self):
        self._grade_final(self.alpha, 84, self.history)
        self._grade_final(self.bravo, 70, self.history)
        self._exam(self.alpha, state="taken", objective_score=30)
        self._exam(self.bravo, state="taken")
        first = self._run(self._batch(include_declarations=False, include_results=False))
        uj = self._tx(first, "UJ", "CRE").filtered(lambda t: t.student_id == self.alpha)
        self.assertEqual({k: v for k, v in uj._values().items() if k.startswith(("NOTE", "RANG"))},
                         {"NOTE_ECOLE_BRUT": "84", "RANG_CINQM": "1", "NOTE_CALC_PART_OBJEC": "30"},
                         "the note école is the school's final result, with its rank (CHS-340010, 340011)")
        only = self._tx(first, "UJ", "CRE").filtered(lambda t: t.student_id == self.bravo)
        self.assertEqual(only._values()["RANG_CINQM"], "2")

    def test_exam_results_are_created_then_added_to_then_modified(self):
        self._grade_final(self.alpha, 70, self.history)
        exam = self._exam(self.alpha, state="taken")
        first = self._run(self._batch(include_declarations=False, include_results=False))
        self.assertEqual(self._tx(first, "UJ", "CRE")._values()["NOTE_ECOLE_BRUT"], "70")
        exam.write({"objective_score": 30})
        self._grade_final(self.alpha, 72, self.history)
        second = self._batch(include_declarations=False, include_results=False)
        second.action_collect()
        creation, modification = self._tx(second, "UJ", "CRE"), self._tx(second, "UJ", "MOD")
        self.assertEqual(creation._values()["NOTE_CALC_PART_OBJEC"], "30")
        self.assertNotIn("NOTE_ECOLE_BRUT", creation._values(), "a creation only for what is new")
        self.assertEqual((modification._values()["NOTE_ECOLE_BRUT"], modification._values()["RANG_CINQM"]), ("72", "1"),
                         "a modified note école goes with its rank")
        second.action_build()
        second.action_seal()
        second.action_send()
        merged = json.loads(self._ledger(creation.key).values)
        self.assertEqual((merged["NOTE_ECOLE_BRUT"], merged["NOTE_CALC_PART_OBJEC"], merged["RANG_CINQM"]), ("72", "30", "1"))

    def test_an_absent_student_is_sent_as_absent(self):
        self._exam(self.alpha, state="absent")
        batch = self._batch(include_declarations=False, include_results=False)
        batch.action_collect()
        self.assertEqual(self._tx(batch, "UJ", "CRE")._values()["NOTE_MINST_BRUT"], "ABS")

    def test_a_request_for_a_file(self):
        batch = self._batch(include_declarations=False, include_results=False, include_exams=False)
        batch.action_add_file_request("OCC", {"AN_EPREU": "2027", "SESN_EPREU": "06"})
        batch.action_build()
        demand = self._xml(batch).find("demande")
        self.assertEqual((demand.get("codeTransactionDemandeService"), demand.findtext("typeTransaction")), ("FIC", "FI"))
        self.assertEqual(demand.find(".//TRANSACTION_FI_OCC/AN_EPREU").text, "2027")
        wizard = self.env["bf.school.charlemagne.request"].with_user(self.office).create({
            "batch_id": batch.id, "code": "EPR", "parameters": "AN_EPREU=2027\nSESN_EPREU=06"})
        with self.assertRaises(UserError):
            wizard.action_add()  # the file was built: no more requests

    def test_the_wizard_takes_tag_value_lines(self):
        batch = self._batch(include_declarations=False, include_results=False, include_exams=False)
        wizard = self.env["bf.school.charlemagne.request"].with_user(self.office).create({
            "batch_id": batch.id, "code": "EPR", "parameters": "AN_EPREU=2027\nSESN_EPREU=06"})
        wizard.action_add()
        tx = self._tx(batch, "FI", "EPR")
        self.assertEqual(tx._values(), {"AN_EPREU": "2027", "SESN_EPREU": "06", "CD_ORGNS": "689001"})
        bad = self.env["bf.school.charlemagne.request"].with_user(self.office).create({
            "batch_id": batch.id, "code": "EPR", "parameters": "no equals sign"})
        with self.assertRaises(UserError):
            bad.action_add()


@tagged("post_install", "-at_install")
class TestAnswers(CharlemagneCase):

    def _sent(self):
        self._ready()
        batch = self._batch()
        batch.action_collect()
        batch.action_build()
        batch.action_seal()
        batch.sudo().state = "sealed"
        batch.sudo().write({"state": "sent"})
        return batch

    def _reports(self, batch, answers):
        """The Ministry's two XML reports: `answers` maps a student to the messages on that student's transaction."""
        command = ("<ECH01020299><commande noCommande='4569' nomFichier='%s.CHA'><codeOrganismeTransmetteur>689001"
                   "</codeOrganismeTransmetteur><nbDemandesTransmises>1</nbDemandesTransmises><nbDemandesAcceptees>1"
                   "</nbDemandesAcceptees><nbDemandesRefusees>0</nbDemandesRefusees><nbDemandesSuspends>0"
                   "</nbDemandesSuspends><listeMessageCommande><messageCommande><noMessage>ECH-1</noMessage>"
                   "<consequence>A</consequence><message>Heard</message></messageCommande></listeMessageCommande>"
                   "</commande></ECH01020299>" % batch.file_base).encode()
        items = ""
        for tx in batch.transaction_ids:
            messages = answers.get(tx.student_id, [])
            body = "".join(
                "<messageTransactionSysteme><noMessageTransaction>%s</noMessageTransaction><consequenceTransaction>%s"
                "</consequenceTransaction><descriptionMessageTransactionSysteme>%s</descriptionMessageTransactionSysteme>"
                "</messageTransactionSysteme>" % m for m in messages)
            items += ("<transactionSysteme><codeTransactionSysteme>%s</codeTransactionSysteme>"
                      "<identificationTransaction>%s</identificationTransaction><listeMessageTransactionSysteme>%s"
                      "</listeMessageTransactionSysteme></transactionSysteme>" % (tx.code, tx.ident, body))
        demand = ("<ECH01020298 noCommande='4569'><listeTransaction>%s</listeTransaction></ECH01020298>" % items).encode()
        return command, demand

    def test_the_answers_set_each_transaction_and_the_ledger(self):
        batch = self._sent()
        command, demand = self._reports(batch, {
            self.alpha: [], self.bravo: [("CHA-610204", "R", "The record is not synchronised")]})
        batch._ingest("CHACO0004569%s.XML" % batch.file_base, command)
        batch._ingest("CHADE0004569%s.XML" % batch.file_base, demand)
        by_student = {t.student_id: t for t in batch.transaction_ids}
        self.assertEqual((by_student[self.alpha].state, by_student[self.bravo].state), ("accepted", "rejected"))
        self.assertEqual(by_student[self.bravo].message_ids.text, "The record is not synchronised")
        self.assertEqual(by_student[self.bravo].message_ids.source, "ministry")
        self.assertTrue(self._ledger(by_student[self.alpha].key))
        self.assertFalse(self._ledger(by_student[self.bravo].key), "a rejected transaction is not remembered as accepted")
        self.assertEqual((batch.state, batch.command_number), ("answered", "4569"))
        self.assertEqual(batch.message_ids.text, "Heard")
        self.assertEqual(batch.rejected_count, 1)

    def test_accepted_with_a_message_is_remembered_and_flagged(self):
        batch = self._sent()
        command, demand = self._reports(batch, {self.alpha: [("CHA-1", "A", "Noted")], self.bravo: []})
        batch._ingest("CHADE0004569%s.XML" % batch.file_base, demand)
        by_student = {t.student_id: t for t in batch.transaction_ids}
        self.assertEqual(by_student[self.alpha].state, "accepted_message")
        self.assertTrue(self._ledger(by_student[self.alpha].key))

    def test_a_held_back_transaction_is_not_remembered(self):
        batch = self._sent()
        command, demand = self._reports(batch, {self.alpha: [("CHA-2", "S", "Pending a decision")], self.bravo: []})
        batch._ingest("CHADE0004569%s.XML" % batch.file_base, demand)
        held = batch.transaction_ids.filtered(lambda t: t.student_id == self.alpha)
        self.assertEqual(held.state, "suspended")
        self.assertFalse(self._ledger(held.key))

    def test_a_file_that_is_not_a_report_is_kept_as_it_came(self):
        batch = self._sent()
        attachment = batch._ingest("CHAFIOCC_0004569_0000001_%s.ZIP" % batch.file_base, b"PK-zip-bytes")
        self.assertEqual(attachment.raw, b"PK-zip-bytes")
        self.assertEqual(batch.state, "sent")

    def test_an_unknown_transaction_in_a_report_is_ignored(self):
        batch = self._sent()
        demand = (b"<ECH01020298><listeTransaction><transactionSysteme><codeTransactionSysteme>CRE</codeTransactionSysteme>"
                  b"<identificationTransaction>NOT-OURS</identificationTransaction></transactionSysteme></listeTransaction>"
                  b"</ECH01020298>")
        batch._ingest("CHADE0004569%s.XML" % batch.file_base, demand)
        self.assertEqual({t.state for t in batch.transaction_ids}, {"ready"})


@tagged("post_install", "-at_install")
class TestSecurity(CharlemagneCase):

    def test_teachers_see_nothing_of_charlemagne(self):
        for model in ("bf.school.charlemagne.config", "bf.school.charlemagne.batch", "bf.school.charlemagne.profile",
                      "bf.school.charlemagne.ledger", "bf.school.charlemagne.transaction", "bf.school.charlemagne.message"):
            with self.assertRaises(AccessError):
                self.env[model].with_user(self.teacher).search([])

    def test_the_office_does_not_read_the_servers_secrets(self):
        self.config.sudo().write({"sftp_user": "ssh-user", "sftp_host": "echanges.example.invalid"})
        with self.assertRaises(AccessError):
            self.config.with_user(self.office).read(["sftp_user"])
        with self.assertRaises(AccessError):
            self.config.with_user(self.office).read(["gpg_secret_enc"])
        self.assertEqual(self.config.with_user(self.admin).sftp_user, "ssh-user")

    def test_the_state_and_files_of_a_transmission_move_with_the_buttons(self):
        batch = self._batch()
        for vals in ({"state": "sent"}, {"sealed_sha256": "x"}, {"xml": base64.b64encode(b"<x/>")}, {"sent_on": "2026-10-11"}):
            with self.assertRaises(AccessError):
                batch.with_user(self.office).write(vals)
        with self.assertRaises(AccessError):
            self._batch(state="sent")

    def test_transactions_ledger_and_messages_are_the_systems(self):
        batch = self._batch()
        for model, vals in (("bf.school.charlemagne.transaction", {"batch_id": batch.id, "kind": "DJ", "code": "CRE"}),
                            ("bf.school.charlemagne.ledger", {"school_id": self.school.id, "key": "x", "kind": "DJ"}),
                            ("bf.school.charlemagne.message", {"batch_id": batch.id, "source": "local"})):
            with self.assertRaises(AccessError):
                self.env[model].with_user(self.office).create(vals)

    def test_a_transmission_that_was_sent_is_kept(self):
        self._ready()
        batch = self._run(self._batch())
        batch.sudo().mode = "production"  # a rehearsal is not kept: it holds nothing the Ministry received
        with self.assertRaises(UserError):
            batch.with_user(self.office).unlink()
        with self.assertRaises(UserError):
            batch.with_user(self.office).action_back_to_draft()
        with self.assertRaises(UserError):
            batch.with_user(self.office).write({"year_id": self.year.id})

    def test_a_new_server_does_not_inherit_the_old_password(self):
        with patch.dict(os.environ, {"BF_POLICY_ESCROW_KEY": Fernet.generate_key().decode()}):
            self.config.with_user(self.admin).write({"sftp_host": "a.example.invalid", "sftp_password": "secret"})
            self.assertTrue(self.config.password_set)
            self.config.with_user(self.admin).write({"sftp_host": "b.example.invalid"})
            self.assertFalse(self.config.password_set, "the password was for the other host")
            self.config.with_user(self.admin).write({"sftp_host": "c.example.invalid", "sftp_password": "new"})
            self.assertTrue(self.config.password_set)

    def test_no_secret_is_stored_without_an_encryption_key(self):
        with patch.dict(os.environ, {"BF_POLICY_ESCROW_KEY": ""}), patch(
                "odoo.addons.bf_policy.models.escrow._raw_key", return_value=""):
            with self.assertRaises(UserError):
                self.config.with_user(self.admin).write({"sftp_password": "secret"})
        self.assertFalse(self.config.password_set)

    def test_one_set_of_settings_per_school(self):
        with self.assertRaises(Exception), self.cr.savepoint():
            self.env["bf.school.charlemagne.config"].with_user(self.office).create({"school_id": self.school.id})


class FakeServer:
    """The Ministry's SFTP server, in memory: what the school puts and what the Ministry leaves."""

    def __init__(self):
        self.files = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def put(self, local, remote):
        with open(local, "rb") as handle:
            self.files[remote] = handle.read()

    def listdir(self, directory):
        prefix = directory.strip("/") + "/"
        return sorted(path[len(prefix):] for path in self.files if path.startswith(prefix))

    def get(self, remote, local):
        with open(local, "wb") as handle:
            handle.write(self.files[remote])
        return len(self.files[remote])

    def remove(self, remote):
        del self.files[remote]


@tagged("post_install", "-at_install")
class TestProduction(CharlemagneCase):
    """The same circuit with real OpenPGP keys and a fake SFTP server."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with gpg.Keyring() as school, gpg.Keyring() as ministry, gpg.Keyring() as impostor:
            cls.school_fpr = gpg.generate_test_key(school, "School <school@example.invalid>", "school-pass")
            cls.ministry_fpr = gpg.generate_test_key(ministry, "Ministry <mel@example.invalid>", "ministry-pass")
            cls.impostor_fpr = gpg.generate_test_key(impostor, "Impostor <bad@example.invalid>", "impostor-pass")
            cls.school_secret = gpg.export_key(school, cls.school_fpr, secret=True)
            cls.school_public = gpg.export_key(school, cls.school_fpr)
            cls.ministry_secret = gpg.export_key(ministry, cls.ministry_fpr, secret=True)
            cls.ministry_public = gpg.export_key(ministry, cls.ministry_fpr)
            cls.impostor_secret = gpg.export_key(impostor, cls.impostor_fpr, secret=True)

    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, {"BF_POLICY_ESCROW_KEY": Fernet.generate_key().decode()})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server = FakeServer()
        patcher = patch.object(CharlemagneConfig, "_sftp", lambda config: self.server)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.config.with_user(self.admin).write({
            "mode": "production", "sftp_host": "echanges.example.invalid", "sftp_user": "689001",
            "sftp_known_host": "echanges.example.invalid ssh-ed25519 AAAA", "sftp_password": "pw",
            "gpg_secret_key": self.school_secret, "gpg_passphrase": "school-pass",
            "gpg_ministry_key": self.ministry_public})
        self._ready()

    def _ministry_reads(self, name):
        with gpg.Keyring("ministry-pass") as ring:
            ring.import_key(self.ministry_secret)
            ring.import_key(self.school_public)
            return ring.decrypt_and_verify(self.server.files[name], signer=self.school_fpr)

    def _ministry_answers(self, batch, secret=None, signer=None, passphrase="ministry-pass", reject=()):
        """The two reports, signed by the Ministry and encrypted for the school, left in the return directory."""
        tx_xml = "".join(
            "<transactionSysteme><codeTransactionSysteme>%s</codeTransactionSysteme><identificationTransaction>%s"
            "</identificationTransaction>%s</transactionSysteme>" % (
                tx.code, tx.ident, ("<listeMessageTransactionSysteme><messageTransactionSysteme><noMessageTransaction>"
                                    "CHA-600802</noMessageTransaction><consequenceTransaction>R</consequenceTransaction>"
                                    "<descriptionMessageTransactionSysteme>Refused</descriptionMessageTransactionSysteme>"
                                    "</messageTransactionSysteme></listeMessageTransactionSysteme>")
                if tx.student_id in reject else "")
            for tx in batch.transaction_ids)
        files = {
            "CHACO0004569%s.XML" % batch.file_base: (
                "<ECH01020299><commande noCommande='4569'><nbDemandesTransmises>1</nbDemandesTransmises></commande>"
                "</ECH01020299>").encode(),
            "CHADE0004569%s.XML" % batch.file_base: (
                "<ECH01020298 noCommande='4569'><listeTransaction>%s</listeTransaction></ECH01020298>" % tx_xml).encode()}
        with gpg.Keyring(passphrase) as ring:
            ring.import_key(secret or self.ministry_secret)
            ring.import_key(self.school_public)
            for name, blob in files.items():
                self.server.files["LISTE/" + name] = ring.sign_and_encrypt(blob, self.school_fpr, signer or self.ministry_fpr)

    def _sealed_and_sent(self):
        batch = self._batch()
        batch.action_collect()
        batch.action_build()
        batch.action_seal()
        batch.action_send()
        return batch

    def test_what_a_rehearsal_accepted_is_not_taken_for_what_the_ministry_accepted(self):
        self.config.sudo().mode = "simulation"
        self._run(self._batch())  # the rehearsal fills the simulation's own ledger
        self.assertTrue(self.env["bf.school.charlemagne.ledger"].sudo().search([("mode", "=", "simulation")]))
        self.config.sudo().mode = "production"
        batch = self._batch()
        batch.action_collect()
        declarations = batch.transaction_ids.filtered(lambda t: t.kind == "DJ")
        self.assertEqual(sorted(declarations.mapped("code")), ["CRE", "CRE"], "nothing was accepted in production yet")

    def test_the_keys_are_checked_before_the_first_transmission(self):
        answer = self.config.with_user(self.office).action_check_keys()
        self.assertEqual(answer["params"]["type"], "success")
        self.assertEqual((self.config.school_key_fingerprint, self.config.ministry_key_fingerprint),
                         (self.school_fpr, self.ministry_fpr))

    def test_wrong_keys_are_found_by_the_check_not_by_the_first_transmission(self):
        self.config.with_user(self.admin).write({"gpg_passphrase": "wrong"})
        with self.assertRaises(UserError):
            self.config.with_user(self.office).action_check_keys()

    def test_production_asks_for_what_it_needs(self):
        self.config.sudo().write({"gpg_secret_enc": False})
        batch = self._batch()
        batch.action_collect()
        batch.action_build()
        with self.assertRaises(UserError):
            batch.action_seal()

    def test_the_file_on_the_server_is_what_only_the_ministry_can_read_and_it_is_signed_by_the_school(self):
        batch = self._sealed_and_sent()
        remote = "INTRANT/%s.CHA" % batch.file_base
        self.assertIn(remote, self.server.files)
        self.assertFalse(any(path.endswith(".part") for path in self.server.files))
        self.assertNotIn(b"ESSA12010512", self.server.files[remote])
        plain, ok, _by = self._ministry_reads(remote)
        self.assertTrue(ok)
        self.assertEqual(hashlib.sha256(plain).hexdigest(), batch.xml_sha256)
        root = ET.fromstring(plain)
        self.assertEqual(root.find("demande/transactionSysteme/parametreTransaction/CHFFREDR_DECLR_DJ_CRE/CD_PERMN").text,
                         "ESSA12010512")
        self.assertEqual((batch.state, batch.sealed_sha256), ("sent", hashlib.sha256(self.server.files[remote]).hexdigest()))

    def test_the_answers_are_fetched_checked_read_and_removed_from_the_server_once_saved(self):
        batch = self._sealed_and_sent()
        self._ministry_answers(batch, reject=self.bravo)
        self.assertEqual(batch.action_fetch_answers(), 2)
        by_student = {t.student_id: t.state for t in batch.transaction_ids}
        self.assertEqual(by_student, {self.alpha: "accepted", self.bravo: "rejected"})
        self.assertEqual(batch.state, "answered")
        self.assertEqual(len(self.server.listdir("LISTE")), 2, "still there: the server lets go only of what is saved")
        self.assertEqual(batch.action_fetch_answers(), 0)
        self.assertEqual(self.server.listdir("LISTE"), [], "a later pass removes what an earlier one read and saved")

    def test_an_answer_not_signed_by_the_ministry_is_not_read_and_not_read_again(self):
        batch = self._sealed_and_sent()
        self._ministry_answers(batch, secret=self.impostor_secret, signer=self.impostor_fpr, passphrase="impostor-pass")
        self.assertEqual(batch.action_fetch_answers(), 0)
        self.assertEqual(batch.state, "sent")
        self.assertEqual(len(self.server.listdir("LISTE")), 2, "kept on the server: nothing was read")
        self.assertEqual(batch.answer_ids.mapped("kind"), ["error", "error"])
        self.assertIn("not signed by the Ministry", batch.answer_ids[0].error)
        self.assertEqual(batch.action_fetch_answers(), 0)
        self.assertEqual(len(batch.answer_ids), 2, "not tried again")

    def test_the_files_not_read_can_be_read_again_by_the_office(self):
        batch = self._sealed_and_sent()
        self._ministry_answers(batch, secret=self.impostor_secret, signer=self.impostor_fpr, passphrase="impostor-pass")
        self.assertEqual(batch.action_fetch_answers(), 0)
        self.assertEqual(batch.error_count, 2)
        self._ministry_answers(batch)  # the Ministry's own files, in their place
        self.assertEqual(batch.action_fetch_answers(), 0, "left alone by the passes: it needs someone to say so")
        with self.assertRaises(AccessError):
            batch.with_user(self.teacher).action_retry_answers()
        self.assertEqual(batch.with_user(self.office).action_retry_answers(), 2)
        self.assertEqual((batch.state, batch.error_count), ("answered", 0))
        self.assertEqual({t.state for t in batch.transaction_ids}, {"accepted"})
        with self.assertRaises(UserError):
            batch.with_user(self.office).action_retry_answers()  # answered: nothing is waiting

    def test_a_file_that_cannot_be_read_does_not_lose_the_others(self):
        batch = self._sealed_and_sent()
        self._ministry_answers(batch)
        with gpg.Keyring("ministry-pass") as ring:
            ring.import_key(self.ministry_secret)
            ring.import_key(self.school_public)
            self.server.files["LISTE/CHACO0004569%s.XML" % batch.file_base] = ring.sign_and_encrypt(
                b"<<< not xml", self.school_fpr, self.ministry_fpr)
        self.assertEqual(batch.action_fetch_answers(), 1)
        self.assertEqual(sorted(batch.answer_ids.mapped("kind")), ["error", "request"])
        self.assertEqual(batch.state, "answered")
        self.assertEqual({t.state for t in batch.transaction_ids}, {"accepted"})

    def test_the_cron_fetches_for_the_transmissions_waiting_for_an_answer(self):
        batch = self._sealed_and_sent()
        self._ministry_answers(batch)
        self.env["bf.school.charlemagne.batch"]._cron_fetch_answers()
        self.assertEqual(batch.state, "answered")

    def test_the_answers_of_another_transmission_are_left_alone(self):
        batch = self._sealed_and_sent()
        self.server.files["LISTE/CHACO0000001SOMETHING_ELSE.XML"] = b"x"
        self.server.files["LISTE/CHACO0000001%s5.XML" % batch.file_base] = b"x"  # a longer name that contains ours
        before = dict(self.server.files)
        self.assertEqual(batch.action_fetch_answers(), 0)
        self.assertEqual(batch.action_fetch_answers(), 0)
        self.assertEqual(self.server.files, before, "nothing of another transmission is read or removed")
        self.assertFalse(batch.answer_ids)

    def test_a_server_that_refuses_is_said(self):
        from odoo.addons.bf_school_charlemagne.charlemagne import sftp as sftp_lib

        def refuse(*_args):
            raise sftp_lib.SftpError("Permission denied")

        batch = self._batch()
        batch.action_collect()
        batch.action_build()
        batch.action_seal()
        with patch.object(FakeServer, "put", refuse), self.assertRaises(UserError):
            batch.action_send()
        self.assertEqual(batch.state, "sealed")


@tagged("post_install", "-at_install")
class TestEdgeCases(CharlemagneCase):
    """Edge cases and guards of the transmissions, each as it was found."""

    def test_a_rehearsal_does_not_poison_the_production_ledger(self):
        self._ready()
        self._run(self._batch())
        key = "DJ|%s|%s" % (self.alpha.id, self.year.id)
        self.assertTrue(self._ledger(key))
        self.assertFalse(self.env["bf.school.charlemagne.ledger"].sudo().search(
            [("key", "=", key), ("mode", "=", "production")]))
        pending = self._batch()
        self.config.sudo().mode = "production"
        with self.assertRaises(UserError):
            pending.action_collect()
        profile = self.env["bf.school.charlemagne.profile"].search([("student_id", "=", self.alpha.id)])
        profile.invalidate_recordset()
        self.assertEqual(profile.ledger_state, "new", "in production nothing was accepted yet")

    def test_a_simulation_leaves_no_copy_of_the_students_and_can_be_deleted(self):
        self._ready()
        batch = self._run(self._batch())
        self.assertFalse(batch.sudo().sealed)
        self.assertFalse(batch.sudo().xml)
        self.assertTrue(batch.sealed_sha256)
        batch.with_user(self.office).unlink()

    def test_a_transmission_with_several_requests_is_answered_when_each_has_its_report(self):
        self._ready()
        self._exam_alpha = self.env["bf.school.exam.result"].create({
            "course_id": self.history.id, "student_id": self.alpha.id, "exam_year": 2027, "exam_month": 6})
        batch = self._run(self._batch(include_results=False))
        self.assertEqual(batch.request_count, 2)
        self.assertEqual(len(batch.answer_ids.filtered(lambda a: a.kind == "request")), 2)
        self.assertEqual(batch.state, "answered")
        self.assertEqual({t.state for t in batch.transaction_ids}, {"accepted"})
        self.assertTrue(self._ledger("IJ|%s|%s|2027|6" % (self.alpha.id, self.history.id)))

    def _sent_two(self):
        self._ready()
        batch = self._batch(include_results=False, include_exams=False)
        batch.action_collect()
        batch.action_build()
        batch.action_seal()
        batch.sudo().write({"state": "sent"})
        return batch

    def _demand(self, batch, listed, clean, with_message=0, refused=0):
        items = "".join(
            "<transactionSysteme><codeTransactionSysteme>%s</codeTransactionSysteme><identificationTransaction>%s"
            "</identificationTransaction>%s</transactionSysteme>" % (
                tx.code, tx.ident, ("<listeMessageTransactionSysteme><messageTransactionSysteme><noMessageTransaction>"
                                    "CHA-1</noMessageTransaction><consequenceTransaction>R</consequenceTransaction>"
                                    "<descriptionMessageTransactionSysteme>No</descriptionMessageTransactionSysteme>"
                                    "</messageTransactionSysteme></listeMessageTransactionSysteme>"))
            for tx in listed)
        return ("<ECH01020298 noCommande='1'><Demande><noDemande>1</noDemande><typeTransaction>DJ</typeTransaction>"
                "<listeCompteurTransaction><compteurTransaction><nbTransactionsTransmises>%s</nbTransactionsTransmises>"
                "<nbTransactionsAccepteesSansMessage>%s</nbTransactionsAccepteesSansMessage>"
                "<nbTransactionsAccepteesAvecMessage>%s</nbTransactionsAccepteesAvecMessage>"
                "<nbTransactionsRefusees>%s</nbTransactionsRefusees></compteurTransaction></listeCompteurTransaction>"
                "</Demande><listeTransaction>%s</listeTransaction></ECH01020298>" % (
                    len(batch.transaction_ids), clean, with_message, refused, items)).encode()

    def test_a_report_that_lists_only_what_has_a_message_accepts_the_rest_by_its_counters(self):
        batch = self._sent_two()
        rejected = batch.transaction_ids.filtered(lambda t: t.student_id == self.bravo)
        batch._ingest("CHADE0000001%s.XML" % batch.file_base, self._demand(batch, rejected, clean=1, refused=1))
        by_student = {t.student_id: t.state for t in batch.transaction_ids}
        self.assertEqual(by_student, {self.alpha: "accepted", self.bravo: "rejected"})
        self.assertTrue(self._ledger(batch.transaction_ids.filtered(lambda t: t.student_id == self.alpha).key))

    def test_counters_that_do_not_add_up_leave_the_transaction_as_it_is_and_say_so(self):
        batch = self._sent_two()
        batch._ingest("CHADE0000001%s.XML" % batch.file_base, self._demand(batch, batch.transaction_ids[:0], clean=1))
        self.assertEqual({t.state for t in batch.transaction_ids}, {"ready"})
        self.assertIn("does not say what became of 2", batch.message_ids.text)
        self.assertFalse(self.env["bf.school.charlemagne.ledger"].sudo().search([("mode", "=", "simulation")]))

    def test_one_file_name_that_contains_another_belongs_to_the_longer(self):
        first = self._batch()
        first.sudo().file_base = "SYMBIFOX_689001_20261011_1e"
        self.assertEqual(first._answer_kind("CHACO0000012SYMBIFOX_689001_20261011_1e.XML"), "command")
        self.assertIsNone(first._answer_kind("CHACO0000012SYMBIFOX_689001_20261011_15e.XML"))
        self.assertIsNone(first._answer_kind("CHADE0000001SYMBIFOX_689001_20261011_11e.XML"))
        self.assertEqual(first._answer_kind("CHADE0000001SYMBIFOX_689001_20261011_1e.XML"), "request")
        self.assertEqual(first._answer_kind("CHAFIEPR_0000001_0000001_SYMBIFOX_689001_20261011_1e.ZIP"), "file")
        self.assertEqual(first._answer_kind("CHAFIEPR_0000001_0000001_SYMBIFOX_689001_20261011_1e2.ZIP"), "file")
        self.assertIsNone(first._answer_kind("CHAFIEPR_0000001_0000001_SYMBIFOX_689001_20261011_15e.ZIP"))

    def test_a_student_who_left_has_an_end_of_attendance_in_the_declaration(self):
        self._ready()
        self.env["bf.school.enrollment"].search([("student_id", "=", self.alpha.id)]).action_leave()
        profile = self.env["bf.school.charlemagne.profile"].search([("student_id", "=", self.alpha.id)])
        self.assertEqual(profile._dj_values()["DT_FIN_FREQN"], str(fields.Date.context_today(profile)))

    def test_the_office_cannot_create_settings_that_choose_where_the_secrets_go(self):
        other = self.env["bf.school"].create({"name": "Autre école", "code": "689002"})
        with self.assertRaises(AccessError):
            self.env["bf.school.charlemagne.config"].with_user(self.office).create({
                "school_id": other.id, "mode": "production", "sftp_host": "evil.example.invalid",
                "gpg_ministry_key": "-----BEGIN PGP PUBLIC KEY BLOCK-----"})
        with self.assertRaises(AccessError):
            self.config.with_user(self.office).school_id = other
        with self.assertRaises(AccessError):
            self.config.with_user(self.office).write({"keys_checked_on": "2026-10-11 00:00:00"})
        self.env["bf.school.charlemagne.config"].with_user(self.office).create({"school_id": other.id})

    def test_a_secret_is_stored_exactly_as_given(self):
        with patch.dict(os.environ, {"BF_POLICY_ESCROW_KEY": Fernet.generate_key().decode()}):
            self.config.with_user(self.admin).write({"sftp_password": " pass phrase ", "gpg_passphrase": "end "})
            secrets = self.config._secrets()
            self.assertEqual((secrets["password"], secrets["gpg_passphrase"]), (" pass phrase ", "end "))

    def test_a_host_or_account_cannot_be_taken_for_an_ssh_option(self):
        for value in ("-oProxyCommand=x", "a b", "a;b"):
            with self.assertRaises(ValidationError):
                self.config.with_user(self.admin).sftp_host = value
        with self.assertRaises(ValidationError):
            self.config.with_user(self.admin).sftp_user = "-oProxyCommand=x"

    def test_the_local_checks_say_themselves_in_the_message_of_the_transaction(self):
        self._fill_profile(self.alpha, mother_tongue_code=False)
        self._fill_profile(self.bravo)
        batch = self._batch()
        batch.action_collect()
        batch.action_build()
        message = self._tx(batch, "DJ").filtered(lambda t: t.student_id == self.alpha).message_ids
        self.assertEqual(message.text, "The element CD_LANG_MATRN_DETL is required.")

    def test_the_daily_cron_survives_a_transmission_that_cannot_be_fetched(self):
        self._ready()
        batch = self._run(self._batch())
        batch.sudo().write({"state": "sent"})
        batch.sudo().mode = "production"
        self.env["bf.school.charlemagne.batch"]._cron_fetch_answers()  # not configured for production: said, not raised
        self.assertEqual(batch.state, "sent")

    def test_a_rehearsal_has_no_answers_to_fetch(self):
        self._ready()
        batch = self._run(self._batch())
        with self.assertRaisesRegex(UserError, "answer comes back"):
            batch.action_fetch_answers()

    def test_a_new_transmission_starts_from_the_only_settings_and_the_current_year(self):
        # A database that already holds another school's settings (the bench's seed) has no "only" settings.
        self.env["bf.school.charlemagne.config"].sudo().search([("id", "!=", self.config.id)]).unlink()
        values = self.env["bf.school.charlemagne.batch"].with_user(self.office).default_get(["config_id", "year_id"])
        self.assertEqual(values.get("config_id"), self.config.id)
        self.assertEqual(values.get("year_id"), self.year.id)

    def test_the_form_can_save_a_new_transmission_but_a_forged_counter_is_refused(self):
        Batch = self.env["bf.school.charlemagne.batch"].with_user(self.office)
        # What the web client sends: the screen's defaults, the counters at 0 included.
        made = Batch.create({"config_id": self.config.id, "year_id": self.year.id, "state": "draft", "request_count": 0,
                             "xml_sha256": False, "include_declarations": True})
        self.assertEqual((made.state, made.request_count, made.mode), ("draft", 0, "simulation"))
        for forged in ({"request_count": 5}, {"mode": "production"}, {"xml_sha256": "abc"}, {"state": "sent"}):
            with self.assertRaises(AccessError, msg=forged):
                Batch.create(dict({"config_id": self.config.id, "year_id": self.year.id}, **forged))


@tagged("post_install", "-at_install")
class TestIsolationAndLimits(CharlemagneCase):
    """Company isolation and limits of the transmissions, each as it was found."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.other_company = env["res.company"].create({"name": "Autre école inc."})
        cls.stranger_office = new_test_user(
            env, login="cha2_stranger", groups="bf_school_core.group_school_manager",
            company_id=cls.other_company.id, company_ids=[(6, 0, cls.other_company.ids)])

    # --- helpers ---------------------------------------------------------------------------------

    def _exam(self, student, **vals):
        values = {"course_id": self.history.id, "student_id": student.id, "exam_year": 2027, "exam_month": 6}
        values.update(vals)
        return self.env["bf.school.exam.result"].create(values)

    def _exams_batch(self):
        return self._batch(include_declarations=False, include_results=False)

    def _accepted_exam(self, **vals):
        """Alpha's exam sitting with a school result and an objective score, sent and accepted."""
        self._grade_final(self.alpha, 84, self.history)
        exam = self._exam(self.alpha, state="taken", objective_score=30, **vals)
        self._run(self._exams_batch())
        return exam

    def _key(self, kind, month=6):
        return "%s|%s|%s|2027|%s" % (kind, self.alpha.id, self.history.id, month)

    def _sent(self):
        """A transmission of the declarations, signed and sent: its answer is not in."""
        self._ready()
        batch = self._batch(include_results=False, include_exams=False)
        batch.action_collect()
        batch.action_build()
        batch.action_seal()
        batch.sudo().write({"state": "sent"})
        return batch

    def _command(self, batch, transmitted=1, accepted=1, refused=0, consequence=None):
        message = ("<listeMessageCommande><messageCommande><noMessage>ECH-800015</noMessage><consequence>%s</consequence>"
                   "<message>The file is not well formed</message></messageCommande></listeMessageCommande>" % consequence
                   if consequence else "")
        return ("<ECH01020299><commande noCommande='4569' nomFichier='%s.CHA'><codeOrganismeTransmetteur>689001"
                "</codeOrganismeTransmetteur><nbDemandesTransmises>%s</nbDemandesTransmises><nbDemandesAcceptees>%s"
                "</nbDemandesAcceptees><nbDemandesRefusees>%s</nbDemandesRefusees><nbDemandesSuspends>0"
                "</nbDemandesSuspends>%s</commande></ECH01020299>" % (
                    batch.file_base, transmitted, accepted, refused, message)).encode()

    def _course_with_components(self, weights):
        """French, with its three competencies, graded for Alpha in the three terms."""
        course = self.env["bf.school.course"].create({
            "group_id": self.group.id, "subject_id": self.env.ref("bf_school_gradebook.subject_sec_fra").id,
            "ministry_course_code": "132406", "teacher_ids": [(6, 0, self.teacher.ids)]})
        for component, weight in zip(course.component_ids.sorted("sequence"), weights):
            component.weight = weight
            for term in (self.t1, self.t2, self.t3):
                assessment = self.env["bf.school.assessment"].create({
                    "name": "%s %s" % (component.name, term.sequence), "course_id": course.id, "term_id": term.id,
                    "component_id": component.id})
                assessment.result_ids.filtered(lambda r: r.student_id == self.alpha).write({"score": 75.0})
        return course

    def _xml_of(self, batch, path):
        return self._xml(batch).findall(path)

    # --- B7 : the August session ----------------------------------------------------------------

    def test_the_august_session_carries_neither_the_school_result_nor_the_rank(self):
        self._grade_final(self.alpha, 84, self.history)
        self._exam(self.alpha, state="taken", objective_score=30, exam_month=8)
        self._exam(self.bravo, exam_month=8)
        batch = self._exams_batch()
        batch.action_collect()
        values = self._tx(batch, "UJ", "CRE")._values()
        self.assertEqual((values["SESN_EPREU_DNR"], values["NOTE_CALC_PART_OBJEC"]), ("08", "30"))
        self.assertNotIn("NOTE_ECOLE_BRUT", values, "CHS-340838: absent for the August session")
        self.assertNotIn("RANG_CINQM", values, "CHS-340010: no rank without the school result")
        self.assertEqual(len(self._tx(batch, "IJ", "CRE")), 2, "the registrations are made all the same")
        self.assertEqual(len(self._tx(batch, "UJ")), 1, "Bravo has nothing to send for an exam not taken yet")

    def test_the_other_sessions_keep_the_school_result_and_the_rank(self):
        self._grade_final(self.alpha, 84, self.history)
        self._grade_final(self.bravo, 70, self.history)
        self._exam(self.alpha, state="taken", objective_score=30, exam_month=6)
        self._exam(self.bravo, state="taken", objective_score=30, exam_month=1)
        batch = self._exams_batch()
        batch.action_collect()
        self.assertEqual(len(self._tx(batch, "UJ", "CRE")), 2)
        for tx in self._tx(batch, "UJ", "CRE"):
            self.assertIn("NOTE_ECOLE_BRUT", tx._values(), tx.key)
            self.assertIn("RANG_CINQM", tx._values(), tx.key)

    # --- B8 : a sitting that moves, a registration that goes, an element that goes -------------

    def test_a_sitting_that_moves_takes_the_results_off_then_cancels_the_registration(self):
        exam = self._accepted_exam()
        old = self._ledger(self._key("UJ"))
        self.assertEqual(set(json.loads(old.values)), {"NOTE_ECOLE_BRUT", "RANG_CINQM", "NOTE_CALC_PART_OBJEC"})
        exam.write({"exam_month": 1})
        second = self._exams_batch()
        second.action_collect()
        self.assertEqual(self._tx(second, "IJ", "CRE")._values()["SESN_EPREU_DNR"], "01")
        self.assertFalse(self._tx(second, "IJ", "SUP"), "not while results stand: the Ministry refuses (CHA-600814)")
        off = self._tx(second, "UJ", "MOD")
        self.assertEqual(off.key, self._key("UJ"))
        self.assertEqual({k: v for k, v in off._values().items() if k.startswith(("NOTE", "RANG"))},
                         {"NOTE_ECOLE_BRUT": "", "RANG_CINQM": "", "NOTE_CALC_PART_OBJEC": ""})
        self.assertEqual(off._values()["SESN_EPREU_DNR"], "06", "the key of the registration the Ministry holds")
        self.assertIn(self.alpha.name, second.collect_log)
        second.action_build()
        element = self._xml(second).find(".//CHSIRERE_RESLT_EPREU_UJ_MOD")
        for tag in ("NOTE_ECOLE_BRUT", "RANG_CINQM", "NOTE_CALC_PART_OBJEC"):
            self.assertIsNotNone(element.find(tag), tag)
            self.assertFalse(element.findtext(tag), "an empty tag takes the element off (guide 6.29)")
        second.action_seal()
        second.action_send()
        self.assertEqual(json.loads(self._ledger(self._key("UJ")).values), {})
        third = self._exams_batch()
        third.action_collect()
        cancel = self._tx(third, "IJ", "SUP")
        self.assertEqual(cancel._values(), {
            "CD_PERMN": "ESSA12010512", "CD_ORGNS": "689001", "CD_COURS": "087304", "AN_EPREU_DNR": "2027",
            "SESN_EPREU_DNR": "06", "NO_GROUP_ELEVE": "01"})
        self.assertEqual(third.transaction_ids, cancel, "nothing else: the new sitting was sent with the second")
        third.action_build()
        third.action_seal()
        third.action_send()
        self.assertEqual(self._ledger(self._key("IJ")).state, "withdrawn")
        self.assertEqual(self._ledger(self._key("IJ", 1)).state, "accepted")
        fourth = self._exams_batch()
        fourth.action_collect()
        self.assertFalse(fourth.transaction_ids)

    def test_a_registration_that_goes_is_cancelled_before_the_new_one_is_made(self):
        exam = self._exam(self.alpha)
        self._run(self._exams_batch())
        exam.write({"exam_month": 1})
        second = self._exams_batch()
        second.action_collect()
        second.action_build()
        codes = [t.get("codeTransactionSysteme") for t in self._xml_of(
            second, ".//demande[typeTransaction='IJ']/transactionSysteme")]
        self.assertEqual(codes, ["SUP", "CRE"], "the cancellation of the sitting that is left comes first")
        cancel = self._tx(second, "IJ", "SUP")
        self.assertEqual(cancel._values()["SESN_EPREU_DNR"], "06")
        self.assertNotEqual(cancel.ident, self._tx(second, "IJ", "CRE").ident)

    def test_a_registration_removed_is_cancelled_and_can_be_made_again(self):
        exam = self._exam(self.alpha)
        self._run(self._exams_batch())
        exam.unlink()
        second = self._run(self._exams_batch())
        self.assertEqual(self._tx(second, "IJ", "SUP").student_id, self.alpha)
        self.assertEqual(len(second.transaction_ids), 1)
        self.assertEqual(self._ledger(self._key("IJ")).state, "withdrawn")
        self._exam(self.alpha)
        third = self._exams_batch()
        third.action_collect()
        self.assertEqual(self._tx(third, "IJ", "CRE").student_id, self.alpha,
                         "what was cancelled at the Ministry is created again")

    def test_a_registration_of_another_year_is_left_alone(self):
        self._exam(self.alpha)
        self._run(self._exams_batch())
        year2 = self.env["bf.school.year"].create({
            "name": "2027-2028", "school_id": self.school.id, "date_start": date(2027, 8, 27), "date_end": date(2028, 6, 23)})
        later = self._batch(year_id=year2.id, include_declarations=False, include_results=False)
        later.action_collect()
        self.assertFalse(later.transaction_ids, "the registrations of the courses of another year are not this year's")

    def test_an_element_the_school_no_longer_has_is_taken_off_with_an_empty_tag(self):
        exam = self._accepted_exam()
        exam.write({"objective_score": 0})
        batch = self._exams_batch()
        batch.action_collect()
        self.assertFalse(self._tx(batch, "UJ", "CRE"))
        modification = self._tx(batch, "UJ", "MOD")
        self.assertEqual(modification._values()["NOTE_CALC_PART_OBJEC"], "")
        self.assertNotIn("NOTE_ECOLE_BRUT", modification._values(), "what still stands is not touched")
        batch.action_build()
        element = self._xml(batch).find(".//CHSIRERE_RESLT_EPREU_UJ_MOD")
        self.assertIsNotNone(element.find("NOTE_CALC_PART_OBJEC"))
        self.assertFalse(element.findtext("NOTE_CALC_PART_OBJEC"))
        self.assertIsNone(element.find("NOTE_ECOLE_BRUT"))
        batch.action_seal()
        batch.action_send()
        self.assertEqual(json.loads(self._ledger(self._key("UJ")).values), {"NOTE_ECOLE_BRUT": "84", "RANG_CINQM": "1"})
        again = self._exams_batch()
        again.action_collect()
        self.assertFalse(again.transaction_ids)

    def test_an_exam_not_taken_after_all_takes_its_scores_and_its_absence_is_created(self):
        exam = self._accepted_exam()
        exam.write({"state": "absent"})
        batch = self._exams_batch()
        batch.action_collect()
        self.assertEqual(self._tx(batch, "UJ", "CRE")._values()["NOTE_MINST_BRUT"], "ABS")
        self.assertEqual(self._tx(batch, "UJ", "MOD")._values()["NOTE_CALC_PART_OBJEC"], "")

    def test_a_student_who_left_keeps_the_rank_the_ministry_holds(self):
        self._grade_final(self.alpha, 90, self.history)
        self._grade_final(self.bravo, 70, self.history)
        self._exam(self.alpha, state="taken", objective_score=30)
        self._exam(self.bravo, state="taken", objective_score=30)
        self._run(self._exams_batch())
        self.env["bf.school.enrollment"].search([("student_id", "=", self.alpha.id)]).action_leave()
        batch = self._exams_batch()
        batch.action_collect()
        self.assertFalse(self._tx(batch, "UJ", "MOD").filtered(lambda t: t.student_id == self.alpha),
                         "the rank alone is not taken off: it is the result of the group as it was")

    def test_a_result_the_school_cannot_confirm_is_not_taken_off(self):
        course = self._course_with_components((30, 30, 40))
        course.write({"ministerial_exam": True})
        self.env["bf.school.exam.result"].create({
            "course_id": course.id, "student_id": self.alpha.id, "exam_year": 2027, "exam_month": 6, "state": "taken"})
        self._run(self._exams_batch())
        key = "UJ|%s|%s|2027|6" % (self.alpha.id, course.id)
        self.assertIn("NOTE_ECOLE_BRUT", json.loads(self._ledger(key).values))
        first = course.component_ids.sorted("sequence")[0]
        course.component_ids.sorted("sequence")[2].weight = 30
        for assessment in self.env["bf.school.assessment"].search([("component_id", "=", first.id)]):
            assessment.result_ids.filtered(lambda r: r.student_id == self.alpha).write({"score": 55.0})
        batch = self._exams_batch()
        batch.action_collect()
        self.assertFalse(self._tx(batch, "UJ"), "weights nobody confirmed: the school result is neither changed nor "
                         "taken off")
        self.assertIn("do not total 100", batch.collect_log)

    # --- B9 : a command the Ministry refuses ---------------------------------------------------

    def test_a_command_refused_by_its_counters_ends_the_transmission_and_leaves_the_ledger_alone(self):
        batch = self._sent()
        batch._ingest("CHACO0004569%s.XML" % batch.file_base, self._command(batch, transmitted=1, accepted=0, refused=1))
        self.assertEqual(batch.state, "refused")
        self.assertTrue(batch.answered_on)
        self.assertEqual({t.state for t in batch.transaction_ids}, {"rejected"})
        self.assertEqual(batch.rejected_count, 2)
        self.assertFalse(self.env["bf.school.charlemagne.ledger"].sudo().search([("mode", "=", "simulation")]))
        again = self._batch(include_results=False, include_exams=False)
        again.action_collect()
        self.assertEqual(self._tx(again, "DJ", "CRE").student_id, self.alpha | self.bravo,
                         "nothing was accepted: the same transactions are made again")
        posted = self.env["mail.message"].search([("model", "=", batch._name), ("res_id", "=", batch.id)])
        self.assertIn("refused the whole transmission", " ".join(posted.mapped("body")))

    def test_a_message_that_refuses_the_command_is_enough(self):
        batch = self._sent()
        batch._ingest("CHACO0004569%s.XML" % batch.file_base,
                      self._command(batch, transmitted=0, accepted=0, refused=0, consequence="R"))
        self.assertEqual((batch.state, {t.state for t in batch.transaction_ids}), ("refused", {"rejected"}))
        self.assertEqual(batch.message_ids.number, "ECH-800015")

    def test_a_refusal_that_comes_after_the_answer_changes_nothing(self):
        batch = self._sent()
        batch.sudo().write({"state": "answered"})
        batch._ingest("CHACO0004569%s.XML" % batch.file_base, self._command(batch, transmitted=1, accepted=0, refused=1))
        self.assertEqual((batch.state, {t.state for t in batch.transaction_ids}), ("answered", {"ready"}))

    def test_a_command_accepted_with_a_message_is_not_refused(self):
        batch = self._sent()
        batch._ingest("CHACO0004569%s.XML" % batch.file_base, self._command(batch, consequence="A"))
        self.assertEqual((batch.state, {t.state for t in batch.transaction_ids}), ("sent", {"ready"}))

    def test_one_request_refused_among_others_does_not_refuse_the_command(self):
        batch = self._sent()
        batch._ingest("CHACO0004569%s.XML" % batch.file_base, self._command(batch, transmitted=2, accepted=1, refused=1))
        self.assertEqual(batch.state, "sent")

    # --- B10 : the weights of the components --------------------------------------------------

    def test_a_course_whose_weights_do_not_total_100_has_no_local_result(self):
        course = self._course_with_components((30, 30, 30))
        self.assertFalse(course.weights_ok)
        self.t3.with_user(self.office).action_close()
        batch = self._batch(include_declarations=False, include_exams=False)
        batch.action_collect()
        self.assertFalse(self._tx(batch, "RJ"), "the report card refuses the same course")
        self.assertIn("do not total 100", batch.collect_log)
        self.assertIn(course.display_name, batch.collect_log)
        course.component_ids.sorted("sequence")[2].weight = 40
        batch.action_collect()
        self.assertEqual(self._tx(batch, "RJ", "CRE")._values()["NOTE_ECOLE_BRUT"], "75")

    # --- B13 : the key of a local result ----------------------------------------------------------

    def test_the_modification_of_a_local_result_names_the_record_the_ministry_holds(self):
        self._grade_final(self.alpha, 77, self.pe_course)
        self.t3.with_user(self.office).action_close()
        self._run(self._batch(include_declarations=False, include_exams=False))
        self._grade_final(self.alpha, 80, self.pe_course)
        self.group.ministry_group_number = 7
        self.config.sudo().local_result_month = 8
        batch = self._batch(include_declarations=False, include_exams=False)
        batch.action_collect()
        values = self._tx(batch, "RJ", "MOD")._values()
        self.assertEqual((values["NO_GROUP_ELEVE"], values["SESN_OBTEN_RESLT"], values["NOTE_ECOLE_BRUT"]),
                         ("01", "06", "80"), "the key is the accepted one (guide 6.24), the grade is the new one")
        self.assertIn("cannot be modified", batch.collect_log)
        batch.action_build()
        batch.action_seal()
        batch.action_send()
        again = self._batch(include_declarations=False, include_exams=False)
        again.action_collect()
        self.assertFalse(again.transaction_ids, "the same grade is not sent again for a key that cannot move")
        self.assertIn("cannot be modified", again.collect_log, "but the school is still told")

    # --- B14 : a transmission waiting for its answer ------------------------------------------

    def test_a_collection_says_when_an_earlier_transmission_waits_for_its_answer(self):
        first = self._sent()
        second = self._batch()
        second.action_collect()
        self.assertIn(first.name, second.collect_log)
        first.sudo().write({"state": "answered"})
        third = self._batch()
        third.action_collect()
        self.assertFalse(third.collect_log and first.name in third.collect_log)

    # --- B15 : a report that is no answer ---------------------------------------------------------

    def test_a_report_without_a_request_or_a_known_transaction_is_not_the_answer(self):
        batch = self._sent()
        batch._ingest("CHADE0000001%s.XML" % batch.file_base, b"<ECH01020298 noCommande='1'></ECH01020298>")
        self.assertEqual(batch.state, "sent")
        self.assertEqual({t.state for t in batch.transaction_ids}, {"ready"})
        self.assertEqual(batch.answer_ids.mapped("kind"), ["error"])
        self.assertEqual(batch.error_count, 1)
        self.assertEqual(batch.message_ids.number, "LOCAL-021")
        self.assertIn("was not read", batch.message_ids.text)
        self.assertTrue(self.env["ir.attachment"].sudo().search([("res_model", "=", batch._name), ("res_id", "=", batch.id)]),
                        "the file is kept as it came")

    def test_a_command_report_without_a_command_is_not_read(self):
        batch = self._sent()
        batch._ingest("CHACO0004569%s.XML" % batch.file_base, b"<ECH01020299></ECH01020299>")
        self.assertEqual((batch.state, batch.answer_ids.kind, batch.message_ids.number), ("sent", "error", "LOCAL-021"))

    # --- B16 : the start of an attendance declaration -----------------------------------------------

    def test_an_attendance_declaration_needs_its_start_date_and_a_financing_one_does_not(self):
        profile = self._fill_profile(self.alpha, declaration_type="FRE")
        enrollment = self.env["bf.school.enrollment"].search([("student_id", "=", self.alpha.id)])
        enrollment.date_start = False
        with self.assertRaisesRegex(ValidationError, "attendance declaration needs"):
            profile._dj_values()
        self._fill_profile(self.bravo)
        batch = self._batch(include_results=False, include_exams=False)
        batch.action_collect()
        stuck = self._tx(batch, "DJ").filtered(lambda t: t.student_id == self.alpha)
        self.assertEqual(stuck.state, "invalid")
        self.assertIn("attendance declaration needs", stuck.message_ids.text)
        profile.start_date = date(2026, 9, 8)
        self.assertEqual(profile._dj_values()["DT_DEBUT_FREQN"], "2026-09-08")
        profile.declaration_type = "FIN"
        profile.start_date = False
        self.assertNotIn("DT_DEBUT_FREQN", profile._dj_values())

    # --- C14 : the host ------------------------------------------------------------------------------

    def test_a_host_holds_no_at_sign_because_ssh_cuts_at_the_last_one(self):
        with self.assertRaises(ValidationError):
            self.config.with_user(self.admin).sftp_host = "ABC123@evil.example"
        self.config.with_user(self.admin).sftp_user = "ABC123"
        self.config.with_user(self.admin).sftp_host = "echanges.example.invalid"

    # --- A6 : the keys of another company's settings -------------------------------------------------

    def test_the_office_of_another_company_does_not_check_the_keys_of_these_settings(self):
        self.config.sudo().read()  # what the superuser read is in the cache the user's environment shares
        stranger = self.config.with_user(self.stranger_office).with_context(allowed_company_ids=self.other_company.ids)
        with self.assertRaises(AccessError):
            stranger.action_check_keys()

    # --- C11 : what a rehearsal keeps --------------------------------------------------------------

    def test_what_a_rehearsal_keeps_is_said_exactly(self):
        self._ready()
        batch = self._run(self._batch())
        # No copy of the file, plain or encrypted: only its fingerprint.
        self.assertFalse(batch.sudo().xml)
        self.assertFalse(batch.sudo().sealed)
        self.assertTrue(batch.sealed_sha256)
        # The transactions and the ledger keep the elements, the identities among them: the rehearsal goes on from them.
        declaration = self._tx(batch, "DJ", "CRE").filtered(lambda t: t.student_id == self.alpha)
        key = declaration.key
        self.assertEqual(declaration._values()["CD_PERMN"], "ESSA12010512")
        self.assertEqual(json.loads(self._ledger(key).values)["PRENM"], "Alpha")
        # Deleting the rehearsal takes away its transactions, its messages, its answers and the reports it kept...
        batch_id = batch.id
        batch.with_user(self.office).unlink()
        Attachment = self.env["ir.attachment"].sudo()
        self.assertFalse(self.env["bf.school.charlemagne.transaction"].sudo().search([("batch_id", "=", batch_id)]))
        self.assertFalse(self.env["bf.school.charlemagne.answer"].sudo().search([("batch_id", "=", batch_id)]))
        self.assertFalse(Attachment.search([("res_model", "=", "bf.school.charlemagne.batch"), ("res_id", "=", batch_id)]))
        # ... and the ledger lines of the rehearsal stay.
        self.assertTrue(self._ledger(key))

