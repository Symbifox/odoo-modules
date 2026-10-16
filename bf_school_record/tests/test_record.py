import base64
import csv
import hashlib
import io
import json
import logging
import time
import zipfile
from contextlib import contextmanager
from datetime import date, timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, new_test_user, tagged
from odoo.tools.misc import hash_sign

from odoo.addons.bf_school_record.models.record import DOWNLOAD_SCOPE

PDF = base64.b64encode(b"%PDF-1.4 fake")


class RecordCase(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.school = env["bf.school"].create({"name": "École des Essais"})
        cls.year = env["bf.school.year"].create({
            "name": "2026-2027", "school_id": cls.school.id,
            "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        cls.year.action_set_current()
        cls.group = env["bf.school.group"].create({
            "name": "301", "school_id": cls.school.id, "year_id": cls.year.id})
        Partner = env["res.partner"]
        cls.mother = Partner.create({"name": "Maman Essai", "street": "12, rue des Érables", "city": "Sherbrooke",
                                     "zip": "J1H 1A1", "email": "maman@example.invalid"})
        cls.father = Partner.create({"name": "Papa Essai", "city": "Magog"})
        cls.grandmother = Partner.create({"name": "Mamie Essai"})
        cls.alpha = Partner.create({"name": "Alpha Essai", "is_student": True, "student_birthdate": date(2016, 1, 5),
                                    "student_permanent_code": "ESSA16010512"})
        Link = env["bf.school.guardian.link"]
        Link.create({"student_id": cls.alpha.id, "guardian_id": cls.mother.id, "relationship": "mother"})
        Link.create({"student_id": cls.alpha.id, "guardian_id": cls.father.id, "relationship": "father",
                     "lives_with": False})
        Link.create({"student_id": cls.alpha.id, "guardian_id": cls.grandmother.id, "relationship": "grandparent"})
        env["bf.school.enrollment"].create({"student_id": cls.alpha.id, "group_id": cls.group.id})
        cls.office = new_test_user(env, login="rec_office", groups="bf_school_core.group_school_manager")
        cls.teacher = new_test_user(env, login="rec_teacher", groups="bf_school_core.group_school_user")
        cls.record = env["bf.school.record"].search([("student_id", "=", cls.alpha.id)])

    def _doc(self, code, state="received", **extra):
        vals = {"record_id": self.record.id, "type_id": self.env.ref("bf_school_record.type_%s" % code).id,
                "state": state, "file": PDF, "file_name": "%s.pdf" % code}
        vals.update(extra)
        return self.env["bf.school.record.document"].with_user(self.office).create(vals)

    def _zip(self, transfer):
        return zipfile.ZipFile(io.BytesIO(base64.b64decode(transfer.sudo().package)))


@tagged("post_install", "-at_install")
class TestFile(RecordCase):

    def test_enrolling_a_student_opens_their_file(self):
        self.assertTrue(self.record)
        self.assertEqual((self.record.school_id, self.record.state), (self.school, "open"))
        again = self.env["bf.school.group"].create({
            "name": "302", "school_id": self.school.id, "year_id": self.year.id})
        self.env["bf.school.enrollment"].create({"student_id": self.alpha.id, "group_id": again.id})
        self.assertEqual(self.env["bf.school.record"].search_count([("student_id", "=", self.alpha.id)]), 1,
                         "one file per student and school")

    def test_the_eight_pieces_of_the_regulation_ship_as_data(self):
        types = self.env["bf.school.record.type"].search([("legal_ref", "like", "E-9.1, r. 3, s. 7")])
        self.assertEqual(len(types), 8)
        self.assertEqual(
            sorted(types.filtered(lambda t: t.requirement == "always").mapped("code")),
            ["application", "birth_certificate", "contract", "enrolment_form", "results"])
        self.assertEqual(
            sorted(types.filtered(lambda t: t.requirement == "when_applicable").mapped("code")),
            ["attendance_proof", "departure_notice", "english_eligibility"])

    def test_a_new_file_misses_every_required_piece(self):
        self.assertFalse(self.record.is_complete)
        self.assertEqual(self.record.missing_count, 5)

    def test_the_file_is_complete_with_the_five_required_pieces(self):
        for code in ("application", "birth_certificate", "contract", "enrolment_form", "results"):
            self._doc(code)
        self.assertTrue(self.record.is_complete)
        self.assertEqual(self.record.missing_count, 0)

    def test_a_piece_flagged_missing_counts_as_missing_even_when_it_only_applies_sometimes(self):
        for code in ("application", "birth_certificate", "contract", "enrolment_form", "results"):
            self._doc(code)
        self._doc("english_eligibility", state="missing", file=False, file_name=False)
        self.assertEqual(self.record.missing_type_ids.mapped("code"), ["english_eligibility"])
        self._doc("english_eligibility")
        self.assertTrue(self.record.is_complete)

    def test_a_required_piece_cannot_be_marked_not_applicable(self):
        with self.assertRaises(ValidationError):
            self._doc("birth_certificate", state="not_applicable", note="no", file=False)

    def test_not_applicable_needs_a_reason(self):
        with self.assertRaises(ValidationError):
            self._doc("english_eligibility", state="not_applicable", file=False)
        doc = self._doc("english_eligibility", state="not_applicable", file=False, note="French-language student")
        self.assertEqual(doc.state, "not_applicable")

    def test_a_received_piece_has_a_file_or_says_where_it_is(self):
        with self.assertRaises(ValidationError):
            self._doc("birth_certificate", file=False, file_name=False)
        doc = self._doc("birth_certificate", file=False, file_name=False, note="Paper, drawer 3")
        self.assertEqual(doc.state, "received")

    def test_the_file_is_never_deleted(self):
        with self.assertRaises(UserError):
            self.record.with_user(self.office).unlink()

    def test_a_departure_keeps_the_file_and_asks_for_the_notice(self):
        self.record.with_user(self.office).action_leave()
        # "Today" is the day of the user who clicked: the test's own clock may be on the other side of midnight.
        self.assertEqual((self.record.state, self.record.departure_date),
                         ("left", fields.Date.context_today(self.record.with_user(self.office))))
        notice = self.record.document_ids.filtered(lambda d: d.type_id.code == "departure_notice")
        self.assertEqual(notice.state, "missing")
        self.record.with_user(self.office).action_reopen()
        self.assertEqual((self.record.state, self.record.departure_date), ("open", False))


@tagged("post_install", "-at_install")
class TestRegister(RecordCase):

    def test_the_register_holds_the_five_entries(self):
        self.assertEqual(self.record.student_id.name, "Alpha Essai")
        self.assertEqual(self.record.birthdate, date(2016, 1, 5))
        self.assertEqual(self.record.teaching_language, "fr")
        self.assertEqual(sorted(self.record.authority_holders.split(", ")), ["Maman Essai", "Papa Essai"],
                         "a grandparent holds no parental authority")
        self.assertEqual(self.record.residence_address, "12, rue des Érables, Sherbrooke, J1H 1A1",
                         "the address of the holder the student lives with")

    def test_the_students_own_address_wins(self):
        self.alpha.write({"street": "3, chemin Neuf", "city": "Lennoxville"})
        self.record.invalidate_recordset()
        self.assertEqual(self.record.residence_address, "3, chemin Neuf, Lennoxville")

    def test_the_register_prints_as_a_pdf(self):
        html, _kind = self.env["ir.actions.report"]._render_qweb_html(
            "bf_school_record.report_register", self.record.ids)
        text = html.decode()
        self.assertIn("Alpha Essai", text)
        self.assertIn("Sherbrooke", text)


@tagged("post_install", "-at_install")
class TestPortability(RecordCase):

    def test_the_copy_is_structured_logged_and_never_kept(self):
        self._doc("birth_certificate")
        action = self.record.with_user(self.office).action_export_portable()
        self.assertTrue(action["url"].startswith("/school/record/%s/portable.json?t=" % self.record.id))
        self.authenticate("rec_teacher", "rec_teacher")
        self.assertEqual(self.url_open(action["url"]).status_code, 404)
        self.authenticate("rec_office", "rec_office")
        response = self.url_open(action["url"])
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertFalse(self.env["ir.attachment"].search([("res_model", "=", "bf.school.record")]), "nothing is kept")
        self.assertEqual(data["format"], "symbifox-school-record/1")
        self.assertEqual(data["identity"]["name"], "Alpha Essai")
        self.assertEqual(data["identity"]["permanent_code"], "ESSA16010512")
        self.assertEqual([g["name"] for g in data["guardians"] if g["parental_authority"]],
                         ["Maman Essai", "Papa Essai"])
        self.assertEqual([p["type"] for p in data["pieces"]], ["Birth certificate"])
        self.assertNotIn("gender", json.dumps(data).lower())
        self.assertIn("export_portable", self.record.log_ids.mapped("action"))


@tagged("post_install", "-at_install")
class TestTransfer(RecordCase):

    def _transfer(self, **vals):
        values = {"record_id": self.record.id, "destination_name": "École de Destination"}
        values.update(vals)
        return self.env["bf.school.record.transfer"].with_user(self.office).create(values)

    def test_the_target_date_comes_from_the_school(self):
        transfer = self._transfer(requested_on=date(2026, 10, 1))
        self.assertEqual(transfer.due_on, date(2026, 10, 6))
        self.school.record_transfer_days = 10
        self.assertEqual(transfer.due_on, date(2026, 10, 11))

    def test_the_package_carries_the_data_and_a_manifest_that_checks(self):
        transfer = self._transfer()
        transfer.with_user(self.office).action_prepare()
        self.assertEqual(transfer.state, "ready")
        archive = self._zip(transfer)
        self.assertEqual(sorted(archive.namelist()), ["manifest.json", "transfer.json"])
        manifest = json.loads(archive.read("manifest.json"))
        for name, digest in manifest["files"].items():
            self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)
        data = json.loads(archive.read("transfer.json"))
        self.assertEqual(data["identity"]["name"], "Alpha Essai")
        self.assertEqual(len(data["guardians"]), 3)
        self.assertEqual(transfer.package_sha256, hashlib.sha256(base64.b64decode(transfer.sudo().package)).hexdigest())

    def test_what_is_sent_is_what_was_ticked(self):
        self._doc("birth_certificate")
        bare = self._transfer(include_guardians=False, include_results=False)
        bare.with_user(self.office).action_prepare()
        self.assertNotIn("guardians", json.loads(self._zip(bare).read("transfer.json")))
        self.assertEqual(sorted(self._zip(bare).namelist()), ["manifest.json", "transfer.json"],
                         "the pieces stay home unless asked for")
        full = self._transfer(include_documents=True)
        full.with_user(self.office).action_prepare()
        names = self._zip(full).namelist()
        self.assertTrue(any(n.startswith("pieces/") for n in names))

    def test_sending_needs_a_package_and_a_way(self):
        transfer = self._transfer()
        with self.assertRaises(UserError):
            transfer.with_user(self.office).action_mark_sent()
        transfer.sent_how = "Secure transfer link"  # a way, but no package yet
        with self.assertRaises(UserError):
            transfer.with_user(self.office).action_mark_sent()
        self.assertEqual(transfer.state, "draft")
        transfer.sent_how = False
        transfer.with_user(self.office).action_prepare()
        with self.assertRaises(UserError):
            transfer.with_user(self.office).action_mark_sent()
        transfer.sent_how = "Secure transfer link"
        transfer.with_user(self.office).action_mark_sent()
        self.assertEqual((transfer.state, transfer.sent_by_id), ("sent", self.office))
        self.assertTrue(transfer.sent_on)
        self.assertIn("transfer_sent", self.record.log_ids.mapped("action"))

    def test_a_transfer_that_went_is_kept_as_it_went(self):
        transfer = self._transfer(sent_how="By hand")
        transfer.with_user(self.office).action_prepare()
        transfer.with_user(self.office).action_mark_sent()
        with self.assertRaises(UserError):
            transfer.with_user(self.office).write({"destination_name": "Elsewhere"})
        with self.assertRaises(UserError):
            transfer.with_user(self.office).unlink()

    def test_state_and_package_do_not_move_by_rpc(self):
        transfer = self._transfer()
        with self.assertRaises(AccessError):
            transfer.with_user(self.office).write({"state": "sent"})
        with self.assertRaises(AccessError):
            transfer.with_user(self.office).write({"package": PDF})
        with self.assertRaises(AccessError):
            self._transfer(state="sent")
        with self.assertRaises(AccessError):
            self._transfer(package_sha256="forged")

    def test_a_prepared_transfer_is_not_deleted(self):
        transfer = self._transfer()
        transfer.with_user(self.office).action_prepare()
        with self.assertRaises(UserError):
            transfer.with_user(self.office).unlink()
        self._transfer().with_user(self.office).unlink()


@tagged("post_install", "-at_install")
class TestClosure(RecordCase):

    def test_the_closure_export_holds_the_register_and_every_file_and_is_never_kept(self):
        self._doc("birth_certificate")
        action = self.school.with_user(self.office).action_export_closure()
        self.assertTrue(action["url"].startswith("/school/record/closure/%s?t=" % self.school.id))
        self.authenticate("rec_teacher", "rec_teacher")
        self.assertEqual(self.url_open(action["url"]).status_code, 404, "a teacher reads the school, not its files")
        self.authenticate("rec_office", "rec_office")
        response = self.url_open(action["url"])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.env["ir.attachment"].search([("res_model", "=", "bf.school")]),
                         "the zip is built when it is downloaded and left nowhere a teacher can read")
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = archive.namelist()
        self.assertIn("register.csv", names)
        self.assertIn("manifest.json", names)
        self.assertTrue(any(n.endswith("/record.json") for n in names))
        self.assertTrue(any("/pieces/" in n for n in names))
        manifest = json.loads(archive.read("manifest.json"))
        for name, digest in manifest["files"].items():
            self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest, name)
        self.assertIn("Alpha Essai", archive.read("register.csv").decode())
        self.assertIn("closure_export", self.record.log_ids.mapped("action"))

    def test_only_the_office_exports_and_opens_files(self):
        with self.assertRaises(AccessError):
            self.school.with_user(self.teacher).action_export_closure()
        with self.assertRaises(AccessError):
            self.school.with_user(self.teacher).action_create_missing_records()


@tagged("post_install", "-at_install")
class TestAccess(RecordCase):

    def test_teachers_have_no_right_on_the_files(self):
        Record = self.env["bf.school.record"].with_user(self.teacher)
        with self.assertRaises(AccessError):
            Record.search([])
        with self.assertRaises(AccessError):
            self.env["bf.school.record.document"].with_user(self.teacher).search([])
        with self.assertRaises(AccessError):
            self.env["bf.school.record.log"].with_user(self.teacher).search([])

    def test_the_log_is_written_by_the_system_and_never_edited(self):
        self._doc("birth_certificate")
        log = self.record.log_ids[:1]
        self.assertTrue(log)
        with self.assertRaises(AccessError):
            self.env["bf.school.record.log"].with_user(self.office).create(
                {"record_id": self.record.id, "user_id": self.office.id, "action": "created"})
        with self.assertRaises(UserError):
            log.sudo().write({"note": "forged"})
        with self.assertRaises(UserError):
            log.sudo().unlink()

    def test_a_download_is_logged_and_for_the_office_only(self):
        doc = self._doc("birth_certificate")
        url = doc.with_user(self.office).action_download()["url"]
        self.authenticate("rec_teacher", "rec_teacher")
        self.assertEqual(self.url_open(url).status_code, 404)
        self.authenticate("rec_office", "rec_office")
        response = self.url_open(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"%PDF-1.4 fake")
        self.assertIn("nosniff", response.headers.get("X-Content-Type-Options", ""))
        self.assertEqual(self.record.log_ids.filtered(lambda l: l.action == "piece_downloaded").mapped("note"),
                         ["Birth certificate"])

    def test_the_transfer_package_downloads_for_the_office_only(self):
        transfer = self.env["bf.school.record.transfer"].with_user(self.office).create(
            {"record_id": self.record.id, "destination_name": "Destination"})
        transfer.with_user(self.office).action_prepare()
        url = transfer.with_user(self.office).action_download()["url"]
        self.authenticate("rec_teacher", "rec_teacher")
        self.assertEqual(self.url_open(url).status_code, 404)
        self.authenticate("rec_office", "rec_office")
        self.assertEqual(self.url_open(url).status_code, 200)
        self.assertIn("transfer_downloaded", self.record.log_ids.mapped("action"))

    def test_a_piece_with_a_name_outside_latin_1_is_served(self):
        doc = self._doc("birth_certificate", file_name="Nguyễn – acte.pdf")
        self.authenticate("rec_office", "rec_office")
        response = self.url_open(doc.with_user(self.office).action_download()["url"])
        self.assertEqual(response.status_code, 200)
        self.assertIn("filename*=UTF-8''", response.headers["Content-Disposition"])

    def test_a_pieces_file_is_not_readable_by_a_non_manager_even_by_field(self):
        doc = self._doc("birth_certificate")
        with self.assertRaises(AccessError):
            doc.with_user(self.teacher).read(["file"])


@tagged("post_install", "-at_install")
class TestEdgeCases(RecordCase):

    def test_instruction_in_english_needs_the_proof_of_eligibility(self):
        for code in ("application", "birth_certificate", "contract", "enrolment_form", "results"):
            self._doc(code)
        self.assertTrue(self.record.is_complete)
        self.record.with_user(self.office).teaching_language = "en"
        self.assertEqual(self.record.missing_type_ids.mapped("code"), ["english_eligibility"])
        self._doc("english_eligibility", state="not_applicable", file=False, note="Exemption on file elsewhere")
        self.assertTrue(self.record.is_complete)

    def test_the_same_transfer_prepared_twice_gives_the_same_package(self):
        transfer = self.env["bf.school.record.transfer"].with_user(self.office).create(
            {"record_id": self.record.id, "destination_name": "Destination"})
        transfer.with_user(self.office).action_prepare()
        first = transfer.package_sha256
        transfer.with_user(self.office).action_back_to_draft()
        self.assertEqual((transfer.state, transfer.package_sha256), ("draft", False))
        transfer.with_user(self.office).action_prepare()
        self.assertEqual(transfer.package_sha256, first)

    def test_a_prepared_transfer_is_not_edited_only_taken_back(self):
        transfer = self.env["bf.school.record.transfer"].with_user(self.office).create(
            {"record_id": self.record.id, "destination_name": "Destination"})
        transfer.with_user(self.office).action_prepare()
        with self.assertRaises(UserError):
            transfer.with_user(self.office).write({"destination_name": "Elsewhere", "include_documents": True})
        transfer.with_user(self.office).action_back_to_draft()
        with self.assertRaises(UserError):
            transfer.with_user(self.office).action_back_to_draft()


@tagged("post_install", "-at_install")
class TestIsolationAndLimits(RecordCase):
    """Company isolation and limits of the student's file, each as it was found."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.other_company = env["res.company"].create({"name": "Autre école inc."})
        cls.stranger_office = new_test_user(
            env, login="rec_other_office", groups="bf_school_core.group_school_manager",
            company_id=cls.other_company.id, company_ids=[(6, 0, cls.other_company.ids)])
        cls.admin = new_test_user(env, login="rec_admin", groups="bf_school_core.group_school_manager,base.group_system")

    def _as_stranger(self, model):
        return self.env[model].with_user(self.stranger_office).with_context(allowed_company_ids=self.other_company.ids)

    def _closure(self):
        self.authenticate("rec_office", "rec_office")
        response = self.url_open(self.school.with_user(self.office).action_export_closure()["url"])
        self.assertEqual(response.status_code, 200)
        return zipfile.ZipFile(io.BytesIO(response.content))

    # --- A5: the legal kinds of pieces are the system administration's

    def test_the_office_of_another_company_does_not_change_which_files_are_complete(self):
        kind = self.env.ref("bf_school_record.type_birth_certificate")
        before = self.record.missing_count
        with self.assertRaises(AccessError):
            self._as_stranger("bf.school.record.type").browse(kind.id).write({"requirement": "optional"})
        with self.assertRaises(AccessError):
            self.env["bf.school.record.type"].with_user(self.office).browse(kind.id).write({"requirement": "optional"})
        with self.assertRaises(AccessError):
            self._as_stranger("bf.school.record.type").browse(kind.id).unlink()
        self.assertEqual((kind.requirement, self.record.missing_count), ("always", before))
        self.env["bf.school.record.type"].with_user(self.admin).browse(kind.id).write({"requirement": "always"})

    def test_a_kind_a_school_adds_is_its_own_and_counts_for_its_own_files_only(self):
        mine = self.env["bf.school.record.type"].with_user(self.office).create({
            "name": "Medical file", "code": "medical", "requirement": "always"})
        self.assertEqual(mine.company_id, self.env.company)
        self.assertEqual(self.record.missing_count, 6, "five required by the regulation and the school's own")
        theirs = self._as_stranger("bf.school.record.type").create({
            "name": "Their form", "code": "theirs", "requirement": "always"})
        self.assertEqual(theirs.company_id, self.other_company)
        self.record.invalidate_recordset()
        self.assertEqual(self.record.missing_count, 6, "another company's kind is not asked of this school's files")
        self.assertNotIn(theirs, self.env["bf.school.record.type"].with_user(self.office).search([]))
        self.assertIn(self.env.ref("bf_school_record.type_birth_certificate"),
                      self._as_stranger("bf.school.record.type").search([]), "the legal ones are read by every school")
        with self.assertRaises(AccessError):
            self.env["bf.school.record.type"].with_user(self.office).create({
                "name": "x", "code": "x", "company_id": self.other_company.id})
        with self.assertRaises(AccessError):
            mine.with_user(self.office).write({"company_id": False})

    # --- B6: the student who leaves the file leaves the group

    def test_the_student_leaving_the_file_leaves_the_group(self):
        enrolment = self.env["bf.school.enrollment"].search([("student_id", "=", self.alpha.id)])
        self.assertEqual(enrolment.state, "active")
        self.record.with_user(self.office).action_leave()
        self.assertEqual((enrolment.state, enrolment.date_end), ("left", self.record.departure_date))
        self.assertFalse(self.group._active_students())

    # --- B18: a reopened file asks for no notice; a stale package is not sent

    def test_a_reopened_file_no_longer_asks_for_the_notice_of_departure(self):
        self.record.with_user(self.office).action_leave()
        self.assertIn("departure_notice", self.record.missing_type_ids.mapped("code"))
        self.record.with_user(self.office).action_reopen()
        self.assertNotIn("departure_notice", self.record.missing_type_ids.mapped("code"))
        self.assertFalse(self.record.document_ids.filtered(lambda d: d.type_id.code == "departure_notice"))

    def test_a_package_that_is_no_longer_what_the_file_says_is_not_sent(self):
        transfer = self.env["bf.school.record.transfer"].with_user(self.office).create(
            {"record_id": self.record.id, "destination_name": "Destination", "sent_how": "By hand"})
        transfer.with_user(self.office).action_prepare()
        self.alpha.write({"name": "Alpha Corrigé"})
        with self.assertRaises(UserError):
            transfer.with_user(self.office).action_mark_sent()
        self.assertEqual(transfer.state, "ready")
        transfer.with_user(self.office).action_back_to_draft()
        transfer.with_user(self.office).action_prepare()
        transfer.with_user(self.office).action_mark_sent()
        self.assertEqual(transfer.state, "sent")

    # --- A10

    def test_a_correction_of_how_it_was_sent_keeps_what_it_said(self):
        transfer = self.env["bf.school.record.transfer"].with_user(self.office).create(
            {"record_id": self.record.id, "destination_name": "Destination", "sent_how": "By hand"})
        transfer.with_user(self.office).action_prepare()
        transfer.with_user(self.office).action_mark_sent()
        self.env.cr.precommit.run()  # the sending is one transaction, its correction another
        transfer.with_user(self.office).write({"sent_how": "By secure link"})
        self.env.cr.precommit.run()  # the history of a field is written when the transaction ends
        old = transfer.sudo().message_ids.tracking_value_ids.filtered(lambda t: t.field_id.name == "sent_how")
        self.assertEqual(old.mapped("old_value_char"), ["By hand"])
        self.assertEqual(old.mapped("new_value_char"), ["By secure link"])

    # --- C5: the register carries no formula

    def test_the_register_csv_runs_no_formula(self):
        self.mother.write({"name": "=1+1 Maman Essai"})
        self.alpha.write({"name": '=HYPERLINK("http://x.invalid","a")', "street": "@SUM(1+1)", "city": "Sherbrooke"})
        self.record.invalidate_recordset()
        text = self._closure().read("register.csv").decode()
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(len(rows), 2)
        for row in rows:
            for cell in row:
                self.assertFalse(cell[:1] in ("=", "+", "-", "@", "\t", "\r"), cell)
        self.assertEqual(rows[1][0], "'" + '=HYPERLINK("http://x.invalid","a")', "the value is kept, only made text")
        self.assertTrue(rows[1][1].startswith("'@SUM(1+1)"))

    # --- C12: a name in an archive stays in its folder

    def test_a_piece_name_with_dots_and_slashes_stays_in_the_archive_folder(self):
        self._doc("birth_certificate", file_name="../../../../tmp/pwned.txt")
        self._doc("application", file_name="..\\..\\x.pdf")
        names = self._closure().namelist()
        pieces = [n for n in names if "/pieces/" in n]
        self.assertEqual(len(pieces), 2)
        for name in names:
            self.assertNotIn("..", name)
            self.assertFalse(name.startswith("/"))
        self.assertTrue(any(n.endswith("-pwned.txt") for n in pieces))
        transfer = self.env["bf.school.record.transfer"].with_user(self.office).create(
            {"record_id": self.record.id, "destination_name": "Destination", "include_documents": True})
        transfer.with_user(self.office).action_prepare()
        for name in self._zip(transfer).namelist():
            self.assertNotIn("..", name)
            self.assertFalse(name.startswith("/"))

    # --- A9: an address only opens with the signature of its own button

    def test_a_download_address_opens_only_with_the_signature_of_the_button(self):
        doc = self._doc("birth_certificate")
        before = self.env["bf.school.record.log"].search_count([])
        self.authenticate("rec_office", "rec_office")
        for path in ("/school/record/document/%s" % doc.id, "/school/record/%s/portable.json" % self.record.id,
                     "/school/record/closure/%s" % self.school.id):
            self.assertEqual(self.url_open(path).status_code, 404, path)
            self.assertEqual(self.url_open(path + "?t=forged").status_code, 404, path)
        self.assertEqual(self.env["bf.school.record.log"].search_count([]), before, "a bare address writes no line")
        url = doc.with_user(self.office).action_download()["url"]
        self.assertEqual(self.url_open(url).status_code, 200)
        # a signature is for one file...
        other = self._doc("application")
        token = url.split("?t=")[1]
        expires = int.from_bytes(base64.urlsafe_b64decode(token + "===")[1:9], "little")
        self.assertTrue(0 < expires - time.time() <= 5 * 60 + 5, "it opens for a few minutes, not for ever")
        self.assertEqual(self.url_open("/school/record/document/%s?t=%s" % (other.id, token)).status_code, 404)
        # ... for one person...
        self.authenticate("rec_admin", "rec_admin")
        self.assertEqual(self.url_open(url).status_code, 404)
        # ... and for a few minutes.
        self.authenticate("rec_office", "rec_office")
        old = hash_sign(self.env(su=True), DOWNLOAD_SCOPE, [self.office.id, "document", doc.id],
                        expiration=timedelta(seconds=-5))
        self.assertEqual(self.url_open("/school/record/document/%s?t=%s" % (doc.id, old)).status_code, 404)

    def test_a_signed_address_does_not_open_the_file_to_someone_who_is_not_the_office(self):
        doc = self._doc("birth_certificate")
        token = hash_sign(self.env(su=True), DOWNLOAD_SCOPE, [self.teacher.id, "document", doc.id],
                          expiration=timedelta(minutes=5))
        self.authenticate("rec_teacher", "rec_teacher")
        self.assertEqual(self.url_open("/school/record/document/%s?t=%s" % (doc.id, token)).status_code, 404)

    def test_a_download_is_not_kept_by_a_cache(self):
        doc = self._doc("birth_certificate")
        self.authenticate("rec_office", "rec_office")
        response = self.url_open(doc.with_user(self.office).action_download()["url"])
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    # --- C4: the standard routes of a binary field are logged too

    def test_the_standard_download_routes_write_the_log_too(self):
        doc = self._doc("birth_certificate")
        transfer = self.env["bf.school.record.transfer"].with_user(self.office).create(
            {"record_id": self.record.id, "destination_name": "Destination"})
        transfer.with_user(self.office).action_prepare()
        attachment = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "bf.school.record.document"), ("res_id", "=", doc.id), ("res_field", "=", "file")])
        self.assertTrue(attachment)
        Log = self.env["bf.school.record.log"]
        self.authenticate("rec_teacher", "rec_teacher")
        self.assertEqual(self.url_open("/web/content/bf.school.record.document/%s/file" % doc.id).status_code, 404)
        self.authenticate("rec_office", "rec_office")
        before = Log.search_count([("action", "=", "piece_downloaded")])
        response = self.url_open("/web/content/bf.school.record.document/%s/file?download=true" % doc.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Log.search_count([("action", "=", "piece_downloaded")]), before + 1)
        self.assertEqual(self.url_open("/web/content/%s?download=true" % attachment.id).status_code, 200)
        self.assertEqual(Log.search_count([("action", "=", "piece_downloaded")]), before + 2)
        before = Log.search_count([("action", "=", "transfer_downloaded")])
        self.assertEqual(self.url_open("/web/content/bf.school.record.transfer/%s/package" % transfer.id).status_code, 200)
        self.assertEqual(Log.search_count([("action", "=", "transfer_downloaded")]), before + 1)


@tagged("post_install", "-at_install")
class TestSealedFiles(RecordCase):
    """The sealed file that leaves, the line written without an error, the file not changed by hand."""

    def _prepared_transfer(self):
        transfer = self.env["bf.school.record.transfer"].with_user(self.office).create(
            {"record_id": self.record.id, "destination_name": "Destination"})
        transfer.with_user(self.office).action_prepare()
        return transfer

    def _lines(self, action):
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

    def test_the_standard_routes_write_their_line_with_no_error_and_no_second_run(self):
        # The route is declared read-only: the line goes by a cursor of its own. Written by the request's cursor it
        # was an ERROR "bad query ... read-only transaction", then a WARNING "retrying with a read/write cursor".
        doc = self._doc("birth_certificate")
        transfer = self._prepared_transfer()
        self.authenticate("rec_office", "rec_office")
        before = self._lines("piece_downloaded"), self._lines("transfer_downloaded")
        with self.no_read_only_retry():
            piece = self.url_open("/web/content/bf.school.record.document/%s/file?download=true" % doc.id)
            package = self.url_open("/web/content/bf.school.record.transfer/%s/package" % transfer.id)
        self.assertEqual((piece.status_code, package.status_code), (200, 200))
        self.assertEqual((self._lines("piece_downloaded"), self._lines("transfer_downloaded")),
                         (before[0] + 1, before[1] + 1), "one line each, written once")

    def test_a_package_that_no_longer_matches_its_seal_is_not_given_out(self):
        transfer = self._prepared_transfer()
        transfer.sudo().package = base64.b64encode(b"PK-forged")
        self.authenticate("rec_office", "rec_office")
        before, shown = self._lines("seal_mismatch"), self._lines("transfer_downloaded")
        response = self.url_open(transfer.with_user(self.office).action_download()["url"])
        self.assertEqual(response.status_code, 409)
        self.assertNotIn(b"PK-forged", response.content)
        self.assertEqual(self._lines("seal_mismatch"), before + 1)
        # and by the standard route: nothing is served, the refusal stays in the log
        response = self.url_open("/web/content/bf.school.record.transfer/%s/package" % transfer.id)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self._lines("seal_mismatch"), before + 2, "the line of a refusal is kept")
        self.assertEqual(self._lines("transfer_downloaded"), shown)

    def test_the_file_under_a_piece_or_a_package_is_not_replaced_nor_deleted_by_hand(self):
        doc = self._doc("birth_certificate")
        transfer = self._prepared_transfer()
        Attachment = self.env["ir.attachment"]
        for model, record, field in (("bf.school.record.document", doc, "file"),
                                     ("bf.school.record.transfer", transfer, "package")):
            attachment = Attachment.sudo().search([
                ("res_model", "=", model), ("res_id", "=", record.id), ("res_field", "=", field)])
            self.assertTrue(attachment, model)
            with self.assertRaises(AccessError):
                attachment.with_user(self.office).write({"datas": base64.b64encode(b"forged")})
            with self.assertRaises(AccessError):
                attachment.with_user(self.office).unlink()
            with self.assertRaises(AccessError):
                Attachment.with_user(self.office).create({
                    "name": "second", "res_model": model, "res_id": record.id, "res_field": field,
                    "datas": base64.b64encode(b"forged")})
            self.assertTrue(attachment.exists(), "kept")
        # the way the screen replaces a piece is still open, and logs
        before = self._lines("piece_replaced")
        doc.with_user(self.office).write({"file": base64.b64encode(b"%PDF-1.4 other")})
        self.assertEqual(self._lines("piece_replaced"), before + 1)
        self.assertEqual(base64.b64decode(doc.sudo().file), b"%PDF-1.4 other")
