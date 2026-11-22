import io
import re
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_survey_answer_sheet.models.nextcloud_document_config import StrangerFile


@tagged("post_install", "-at_install")
class TestAnswerSheet(TransactionCase):
    """Nothing here assumes a thing about the database it runs on: the survey,
    its questions and its responses are made by the test."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env["nextcloud.document.config"].create({
            "name": "Nextcloud of the test",
            "nextcloud_base_url": "https://cloud.example.com",
            "nextcloud_user": "service",
            "webdav_path": "/remote.php/dav/files/service",
        })
        cls.survey = cls.env["survey.survey"].create({
            "title": "Diagnostic",
            "access_mode": "public",
            "bf_sheet_enabled": True,
            "bf_sheet_config_id": cls.config.id,
            "bf_sheet_root": "/Programme",
            "bf_sheet_folders": "02 Questionnaire\n09 Shared",
            "bf_sheet_template": "00 Template",
            "bf_sheet_noise_words": "programme nord",
            "bf_sheet_filename": "%(date)s_%(company)s - Results.pdf",
            "question_and_page_ids": [
                (0, 0, {"title": "Section one", "is_page": True, "sequence": 1}),
                (0, 0, {"title": "Company name", "question_type": "char_box",
                        "save_as_nickname": True, "sequence": 2}),
                (0, 0, {"title": "In your words", "question_type": "text_box", "sequence": 3}),
                (0, 0, {"title": "How many sites", "question_type": "numerical_box", "sequence": 4}),
                (0, 0, {"title": "Section two", "is_page": True, "sequence": 5}),
                (0, 0, {"title": "Your size", "question_type": "simple_choice",
                        "comments_allowed": True, "sequence": 6,
                        "suggested_answer_ids": [(0, 0, {"value": "Small"}),
                                                 (0, 0, {"value": "Large"})]}),
                (0, 0, {"title": "Your markets", "question_type": "multiple_choice", "sequence": 7,
                        "suggested_answer_ids": [(0, 0, {"value": "Canada"}),
                                                 (0, 0, {"value": "Europe"})]}),
                (0, 0, {"title": "Never answered", "question_type": "char_box", "sequence": 8}),
            ],
        })
        questions = cls.survey.question_and_page_ids.filtered(lambda q: not q.is_page)
        cls.q_company, cls.q_words, cls.q_sites, cls.q_size, cls.q_markets, cls.q_skipped = questions

    # ------------------------------------------------------------------ #
    def _sheet(self, marker, password):
        """A PDF carrying `marker`, encrypted with `password`, as the module makes them."""
        from pyhanko.pdf_utils import generic
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.pdf_utils.writer import copy_into_new_writer
        from reportlab.pdfgen import canvas

        buf = io.BytesIO()
        page = canvas.Canvas(buf)
        page.drawString(72, 720, "sheet")
        page.showPage()
        page.save()
        buf.seek(0)
        writer = copy_into_new_writer(PdfFileReader(buf))
        writer.set_info(generic.DictionaryObject({
            generic.pdf_name("/Keywords"): generic.pdf_string(marker)}))
        writer.encrypt(owner_pass="owner", user_pass=password)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()

    def _answer(self, **values):
        """A completed response, answered the way the controller answers."""
        user_input = self.env["survey.user_input"].create(dict(
            {"survey_id": self.survey.id}, **values))
        user_input._save_lines(self.q_company, "Kestrel inc.")
        user_input._save_lines(self.q_words, "Two lines\nof prose")
        user_input._save_lines(self.q_sites, "5")
        user_input._save_lines(self.q_size, self.q_size.suggested_answer_ids[1].id,
                               comment="and growing")
        user_input._save_lines(self.q_markets, self.q_markets.suggested_answer_ids.ids)
        user_input._mark_done()
        return user_input

    # ------------------------------------------------------------------ #
    # The trigger
    # ------------------------------------------------------------------ #
    def test_submission_is_the_only_trigger(self):
        answered = self._answer()
        self.assertEqual(answered.bf_sheet_state, "pending")

        test_entry = self._answer(test_entry=True)
        self.assertFalse(test_entry.bf_sheet_state, "a test entry is never filed")

        self.survey.bf_sheet_enabled = False
        plain = self._answer()
        self.assertFalse(plain.bf_sheet_state, "a survey that files nothing flags nothing")

    def test_only_the_settings_group_may_address_the_cloud(self):
        """🔴 `groups=` is enforced on read, write and search - not on create.
        Without the guard, any Surveys user could point a service account that
        sees the whole cloud at a folder of their choosing."""
        user = self.env["res.users"].create({
            "name": "Plain surveys user", "login": "surveys-only@example.test",
            "groups_id": [(6, 0, [self.env.ref("survey.group_survey_user").id,
                                  self.env.ref("base.group_user").id])]})
        with self.assertRaises(AccessError):
            self.env["survey.survey"].with_user(user).create({
                "title": "Mine", "bf_sheet_enabled": True,
                "bf_sheet_config_id": self.config.id, "bf_sheet_root": "/"})
        plain = self.env["survey.survey"].with_user(user).create({"title": "Plain"})
        self.assertTrue(plain, "a survey without those fields is still theirs to make")
        with self.assertRaises(AccessError):
            plain.write({"bf_sheet_root": "/elsewhere"})

    def test_a_survey_that_files_needs_where_to_file(self):
        with self.assertRaises(ValidationError):
            self.survey.bf_sheet_root = False

    # ------------------------------------------------------------------ #
    # Naming
    # ------------------------------------------------------------------ #
    def test_the_company_is_the_one_declared(self):
        answered = self._answer()
        self.assertEqual(answered._bf_sheet_company_name(), "Kestrel inc.",
                         "the nickname question carries the declared name")

    def test_an_invited_response_belongs_to_the_partner_s_company(self):
        """What somebody at the house typed beats what the internet typed."""
        company = self.env["res.partner"].create({"name": "Kestrel Ltd", "is_company": True})
        contact = self.env["res.partner"].create({"name": "A Person", "parent_id": company.id})
        answered = self._answer(partner_id=contact.id)
        self.assertEqual(answered._bf_sheet_company_name(), "Kestrel Ltd")

    def test_a_sheet_always_has_a_name(self):
        bare = self.env["survey.user_input"].create({"survey_id": self.survey.id})
        bare.email = "someone@acme.example"
        self.assertEqual(bare._bf_sheet_company_name(), "acme.example")
        bare.email = False
        self.assertEqual(bare._bf_sheet_company_name(),
                         self.env._("Unknown company"))

    def test_the_file_name_carries_the_completion_day(self):
        """The day is the company's day, not the server's: 22:30 in Montreal is
        still the 19th, where UTC has already turned."""
        self.env.company.partner_id.tz = "America/Montreal"
        answered = self._answer()
        answered.sudo().end_datetime = "2026-11-20 03:30:00"   # UTC
        # "Kestrel inc." loses its final dot: a name ending with one is a
        # name a Windows client cannot open.
        self.assertEqual(answered._bf_sheet_filename(),
                         "2026_11_19_Kestrel inc - Results.pdf")
        self.assertEqual(answered._bf_sheet_basename(),
                         "2026_11_19_Kestrel inc - Results")

    def test_a_name_that_a_windows_client_could_not_open_is_cleaned(self):
        answered = self._answer()
        answered.nickname = 'A/B: "C" <D>'
        self.assertNotRegex(answered._bf_sheet_filename(), r'[\\/:*?"<>|]')

    # ------------------------------------------------------------------ #
    # What the sheet says
    # ------------------------------------------------------------------ #
    def test_the_sheet_follows_the_survey_section_by_section(self):
        sections = self._answer()._bf_sheet_sections()
        self.assertEqual([section["title"] for section in sections],
                         ["Section one", "Section two"])
        self.assertEqual([question["title"] for question in sections[0]["questions"]],
                         ["Company name", "In your words", "How many sites"])

    def test_the_sheet_carries_the_survey_s_numbers(self):
        """With bf_survey_numbering, the sheet says 2.1 where the form said 2.1."""
        if "bf_numbering" not in self.survey._fields:
            self.skipTest("bf_survey_numbering is not installed")
        self.survey.bf_numbering = True
        sections = self._answer()._bf_sheet_sections()
        self.assertEqual([section["title"] for section in sections],
                         ["1. Section one", "2. Section two"])
        self.assertEqual([question["title"] for question in sections[1]["questions"]],
                         ["2.1 Your size", "2.2 Your markets", "2.3 Never answered"])

    def test_the_report_layout_does_not_restyle_the_sheet_s_tables(self):
        """Odoo's report layouts draw cell borders on every table that does not opt
        out, with no inner margin: the first character of each row touched the line."""
        html = self.env["ir.actions.report"]._render_qweb_html(
            "bf_survey_answer_sheet.report_answer_sheet", self._answer().ids)[0].decode()
        tables = re.findall(r"<table\b[^>]*>", html)
        self.assertTrue(tables)
        for table in tables:
            self.assertIn("o_ignore_layout_styling", table)

    def test_no_cell_of_the_sheet_draws_a_frame(self):
        """wkhtmltopdf draws a cell's borders as soon as their style is solid, which
        Bootstrap sets on every cell, even at zero width: each cell turns it off."""
        html = self.env["ir.actions.report"]._render_qweb_html(
            "bf_survey_answer_sheet.report_answer_sheet", self._answer().ids)[0].decode()
        cells = re.findall(r"<td\b[^>]*>", html)
        self.assertTrue(cells)
        for cell in cells:
            self.assertIn('style="border-style:none;', cell)

    def test_every_kind_of_answer_is_printed(self):
        sections = self._answer()._bf_sheet_sections()
        answers = {question["title"]: question["answer"]
                   for section in sections for question in section["questions"]}
        self.assertEqual(answers["Company name"]["kind"], "text")
        self.assertEqual(answers["Company name"]["text"], "Kestrel inc.")
        self.assertEqual(answers["In your words"]["text"], "Two lines\nof prose")
        self.assertEqual(answers["How many sites"]["text"], "5")
        self.assertEqual(answers["Your size"]["kind"], "list")
        self.assertEqual(answers["Your size"]["items"], ["Large"])
        self.assertEqual(answers["Your size"]["comment"], "and growing",
                         "a comment box is not an answer, and is not lost either")
        self.assertEqual(sorted(answers["Your markets"]["items"]), ["Canada", "Europe"])
        self.assertEqual(answers["Never answered"]["kind"], "none")

    def test_a_scale_answered_zero_is_an_answer(self):
        """Zero is the worst mark of a 0-10 question, not a silence."""
        question = self.env["survey.question"].create({
            "survey_id": self.survey.id, "sequence": 9,
            "title": "Your maturity", "question_type": "scale",
            "scale_min": 0, "scale_max": 10})
        answered = self._answer()
        answered._save_lines(question, "0")
        answers = {q["title"]: q["answer"] for section in answered._bf_sheet_sections()
                   for q in section["questions"]}
        self.assertEqual(answers["Your maturity"]["kind"], "text")
        self.assertEqual(answers["Your maturity"]["text"], "0")

    def test_numbers_given_row_by_row_print_row_by_row(self):
        """The shape a percentage allocation stores, measured without the
        module that produces it."""
        question = self.env["survey.question"].create({
            "survey_id": self.survey.id, "sequence": 10,
            "title": "Hours by site", "question_type": "matrix",
            "matrix_row_ids": [(0, 0, {"value": "Montreal"}), (0, 0, {"value": "Quebec"})]})
        answered = self._answer()
        rows = question.matrix_row_ids
        self.env["survey.user_input.line"].create([
            {"user_input_id": answered.id, "question_id": question.id,
             "answer_type": "numerical_box", "matrix_row_id": rows[0].id,
             "value_numerical_box": 60.0},
            {"user_input_id": answered.id, "question_id": question.id,
             "answer_type": "numerical_box", "matrix_row_id": rows[1].id,
             "value_numerical_box": 12.5},
        ])
        answered.invalidate_recordset()
        answers = {q["title"]: q["answer"] for section in answered._bf_sheet_sections()
                   for q in section["questions"]}
        self.assertEqual(answers["Hours by site"]["kind"], "pairs")
        self.assertEqual(dict(answers["Hours by site"]["pairs"]),
                         {"Montreal": "60", "Quebec": "12.5"})

    def test_a_question_the_respondent_never_saw_is_not_printed(self):
        """A conditional question nobody was shown is not a question left
        empty: printing "No answer" would make the sheet lie."""
        trigger = self.q_size.suggested_answer_ids[0]      # "Small"
        hidden = self.env["survey.question"].create({
            "survey_id": self.survey.id, "sequence": 11,
            "title": "Only for small companies", "question_type": "char_box",
            "triggering_answer_ids": [(6, 0, [trigger.id])]})
        answered = self._answer()                          # answered "Large"
        titles = [q["title"] for section in answered._bf_sheet_sections()
                  for q in section["questions"]]
        self.assertNotIn(hidden.title, titles)

    def test_a_percentage_allocation_prints_its_rows(self):
        if "percent_allocation" not in dict(
                self.env["survey.question"]._fields["question_type"].selection):
            self.skipTest("bf_survey_allocation is not installed")
        question = self.env["survey.question"].create({
            "survey_id": self.survey.id, "sequence": 9,
            "title": "Sales by territory", "question_type": "percent_allocation",
            "matrix_row_ids": [(0, 0, {"value": "Canada"}), (0, 0, {"value": "Europe"})],
        })
        answered = self._answer()
        rows = question.matrix_row_ids
        answered._save_lines(question, {str(rows[0].id): 60, str(rows[1].id): 40})
        answers = {q["title"]: q["answer"] for section in answered._bf_sheet_sections()
                   for q in section["questions"]}
        self.assertEqual(answers["Sales by territory"]["kind"], "pairs")
        self.assertEqual(dict(answers["Sales by territory"]["pairs"]),
                         {"Canada": "60 %", "Europe": "40 %"})

    # ------------------------------------------------------------------ #
    # The encryption
    # ------------------------------------------------------------------ #
    def test_the_sheet_is_encrypted_and_only_the_password_opens_it(self):
        from pyhanko.pdf_utils.reader import PdfFileReader

        answered = self._answer()
        password = self.survey._bf_sheet_get_password()
        self.assertEqual(len(password), 20, "a drawn password is long enough to be one")
        encrypted = answered._bf_sheet_encrypt(answered._bf_sheet_render(), password)

        self.assertIn(b"/Filter /Standard", encrypted)
        self.assertIn(b"/V 5", encrypted)
        self.assertIn(b"/R 6", encrypted, "AES-256, not RC4")
        self.assertNotIn(b"Kestrel", encrypted, "the answers are not in the clear")

        reader = PdfFileReader(io.BytesIO(encrypted))
        self.assertEqual(reader.decrypt("not the password").status.name, "FAILED")
        reader = PdfFileReader(io.BytesIO(encrypted))
        self.assertNotEqual(reader.decrypt(password).status.name, "FAILED")
        self.assertEqual(str(reader.trailer["/Info"]["/Keywords"]),
                         answered._bf_sheet_marker())

    def test_the_password_is_drawn_once_and_kept(self):
        first = self.survey._bf_sheet_get_password()
        self.assertEqual(first, self.survey._bf_sheet_get_password())

    def test_the_password_exists_from_the_moment_the_filing_is_switched_on(self):
        """It must not be drawn by the filing: a filing that fails halfway is
        rolled back, the password with it, and the next attempt can no longer
        read the marker inside the file the first one left on Nextcloud."""
        plain = self.env["survey.survey"].create({"title": "No filing"})
        self.assertFalse(plain.bf_sheet_password)
        plain.write({"bf_sheet_enabled": True, "bf_sheet_config_id": self.config.id,
                     "bf_sheet_root": "/Programme"})
        self.assertTrue(plain.bf_sheet_password, "switching it on draws the password")
        self.assertEqual(len(plain.bf_sheet_password), 20)

        born = self.env["survey.survey"].create({
            "title": "Filing from birth", "bf_sheet_enabled": True,
            "bf_sheet_config_id": self.config.id, "bf_sheet_root": "/Programme"})
        self.assertTrue(born.bf_sheet_password)

    # ------------------------------------------------------------------ #
    # The folder of the company that answered
    # ------------------------------------------------------------------ #
    def test_the_folder_is_found_whatever_the_programme_words_around_it(self):
        answered = self._answer()
        with patch.object(type(self.config), "_bf_sheet_children",
                          return_value=["Kestrel Programme Nord", "Tanager Electric", "00 Template"]):
            self.assertEqual(answered._bf_sheet_client_folder(self.config.sudo()),
                             "/Programme/Kestrel Programme Nord")

    def test_two_folders_that_could_match_are_never_guessed_between(self):
        """Two folders whose names say the same thing once the noise is off."""
        answered = self._answer()
        with patch.object(type(self.config), "_bf_sheet_children",
                          return_value=["Kestrel programme", "Kestrel nord"]):
            with self.assertRaises(UserError):
                answered._bf_sheet_client_folder(self.config.sudo())

    def test_a_name_that_is_only_part_of_a_folder_is_not_that_folder(self):
        """The survey is public: the company names itself. "Groupe" is not
        "Groupe Alpha", and filing it there would hand one company's answers to
        another. It gets a folder of its own, for a human to triage."""
        answered = self._answer()
        answered.nickname = "Groupe"
        made = []
        with patch.object(type(self.config), "_bf_sheet_children",
                          return_value=["Groupe Alpha", "00 Template"]), \
             patch.object(type(self.config), "_bf_sheet_copy",
                          side_effect=lambda source, destination, under: made.append(
                              destination) or destination):
            path = answered._bf_sheet_client_folder(self.config.sudo())
        self.assertEqual(path, "/Programme/Groupe")
        self.assertEqual(made, ["/Programme/Groupe"])

    def test_a_company_with_no_folder_gets_one_from_the_template(self):
        answered = self._answer()
        copied = []
        with patch.object(type(self.config), "_bf_sheet_children",
                          return_value=["Tanager Electric", "00 Template"]), \
             patch.object(type(self.config), "_bf_sheet_copy",
                          side_effect=lambda source, destination, under: copied.append(
                              (source, destination)) or destination):
            path = answered._bf_sheet_client_folder(self.config.sudo())
        self.assertEqual(path, "/Programme/Kestrel inc")
        self.assertEqual(copied, [("/Programme/00 Template", "/Programme/Kestrel inc")])

    def test_nothing_is_ever_filed_inside_the_folder_template(self):
        """A sheet filed in the template would ride into every folder made from
        it afterwards."""
        answered = self._answer()
        answered.nickname = "00 Template"
        with patch.object(type(self.config), "_bf_sheet_children",
                          return_value=["00 Template"]), \
             patch.object(type(self.config), "_bf_sheet_copy",
                          side_effect=lambda source, destination, under: destination):
            with self.assertRaises(UserError):
                answered._bf_sheet_client_folder(self.config.sudo())

    # ------------------------------------------------------------------ #
    # The deposit
    # ------------------------------------------------------------------ #
    def test_the_deposit_writes_every_destination_and_says_where(self):
        answered = self._answer()
        written = []
        with patch.object(type(self.config), "_bf_sheet_children",
                          return_value=["Kestrel Programme Nord"]), \
             patch.object(type(self.config), "_bf_sheet_mkdirs",
                          side_effect=lambda path, under: path), \
             patch.object(type(self.config), "_bf_sheet_deposit_file",
                          side_effect=lambda path, content, etag, password, marker:
                              (written.append(path) or (path, "etag-1"))):
            paths = answered._bf_sheet_deposit(b"%PDF-1.4", "sheet.pdf", "secret")
        self.assertEqual(paths, [
            "/Programme/Kestrel Programme Nord/02 Questionnaire/sheet.pdf",
            "/Programme/Kestrel Programme Nord/09 Shared/sheet.pdf"])
        self.assertEqual(written, paths)
        self.assertEqual(answered.bf_sheet_etags.count("etag-1"), 2,
                         "what was written is remembered, so a retry can replace it")

    def test_a_sheet_is_recognised_only_by_its_own_marker(self):
        """The guard that tells this response's own copy from anybody else's
        file, measured on real encrypted PDFs rather than on a mock."""
        config = self.config.sudo()
        cases = [
            (self._sheet("marker-A", "secret"), "marker-A", "its own marker"),
            (self._sheet("marker-B", "secret"), "marker-B", "another response's marker"),
            (self._sheet("marker-A", "another secret"), None, "another password"),
            (b"%PDF-1.4 not encrypted at all", None, "no encryption at all"),
            (b"not a pdf", None, "not even a PDF"),
        ]
        for content, expected, what in cases:
            with patch.object(type(config), "_webdav_get", return_value=content):
                self.assertEqual(config._bf_sheet_mark_of("/P/x/sheet.pdf", "secret"),
                                 expected, what)

    def test_its_own_earlier_copy_is_replaced_where_it_lies(self):
        """An attempt the database rolled back leaves a file with no etag on
        record: the next one recognises it and replaces that very version."""
        config = self.config.sudo()
        calls = []

        def put(path, content, etag=None):
            calls.append((path, etag))
            if etag is None:
                raise StrangerFile("already there")
            return "etag-2"

        with patch.object(type(config), "_bf_sheet_put", side_effect=put), \
             patch.object(type(config), "_bf_sheet_etag", return_value="etag-1"), \
             patch.object(type(config), "_webdav_get",
                          return_value=self._sheet("mine", "secret")):
            landed, etag = config._bf_sheet_deposit_file(
                "/P/x/sheet.pdf", b"%PDF", None, "secret", "mine")
        self.assertEqual((landed, etag), ("/P/x/sheet.pdf", "etag-2"))
        self.assertEqual(calls, [("/P/x/sheet.pdf", None), ("/P/x/sheet.pdf", "etag-1")],
                         "the second write is conditional on the version it looked at")

    def test_another_response_s_sheet_sends_this_one_beside_it(self):
        """Two people of one company answering the same day: the second sheet
        does not erase the first, and is not lost either."""
        config = self.config.sudo()
        first = self._sheet("response-1", "secret")
        taken = {"/P/x/sheet.pdf": first}
        put_calls = []

        def put(path, content, etag=None):
            put_calls.append((path, etag))
            if etag is None and path in taken:
                raise StrangerFile("taken")
            taken[path] = content
            return "etag-new"

        with patch.object(type(config), "_bf_sheet_put", side_effect=put), \
             patch.object(type(config), "_bf_sheet_etag",
                          side_effect=lambda path: "etag-1" if path in taken else False), \
             patch.object(type(config), "_webdav_get", side_effect=lambda path: taken[path]):
            landed, _etag = config._bf_sheet_deposit_file(
                "/P/x/sheet.pdf", b"%PDF", None, "secret", "response-2")
        self.assertEqual(landed, "/P/x/sheet (2).pdf")
        self.assertEqual(taken["/P/x/sheet.pdf"], first,
                         "the first response's sheet was not touched")
        self.assertIn(("/P/x/sheet (2).pdf", None), put_calls)

    def test_a_nextcloud_that_does_not_answer_is_not_a_verdict(self):
        """An outage must not read as "somebody else's file": that would burn
        the attempts of the loop built to survive outages."""
        config = self.config.sudo()
        with patch.object(type(config), "_webdav_get",
                          side_effect=UserError("Error while downloading: timeout")):
            with self.assertRaises(UserError):
                config._bf_sheet_mark_of("/P/x/sheet.pdf", "secret")

    def test_a_stranger_file_is_left_alone(self):
        """A file this module did not write stays where it is, whatever its
        name, and the response says so."""
        config = self.config.sudo()
        put_calls = []

        def put(path, content, etag=None):
            put_calls.append((path, etag))
            if etag is None:
                raise StrangerFile("taken")
            return "etag-2"

        with patch.object(type(config), "_bf_sheet_put", side_effect=put), \
             patch.object(type(config), "_bf_sheet_etag", return_value="etag-x"), \
             patch.object(type(config), "_webdav_get",
                          return_value=b"%PDF-1.4 somebody else's file"):
            with self.assertRaises(StrangerFile):
                config._bf_sheet_deposit_file("/P/x/sheet.pdf", b"%PDF",
                                              None, "secret", "mine")
        self.assertEqual([etag for _path, etag in put_calls], [None],
                         "no second write was even attempted")

    def test_a_file_edited_since_we_wrote_it_is_not_overwritten(self):
        """With an etag on record, a file that changed meanwhile is somebody's
        work, not a version to replace."""
        config = self.config.sudo()
        with patch.object(type(config), "_bf_sheet_put", side_effect=StrangerFile("moved on")):
            with self.assertRaises(StrangerFile):
                config._bf_sheet_deposit_file("/P/x/sheet.pdf", b"%PDF",
                                              "etag-old", "secret", "mine")

    # ------------------------------------------------------------------ #
    # The courier
    # ------------------------------------------------------------------ #
    def test_one_failure_never_stops_the_round(self):
        first = self._answer()
        second = self._answer()
        with patch.object(type(self.env["survey.user_input"]), "_bf_sheet_file",
                          side_effect=UserError("Nextcloud is down")):
            self.env["survey.user_input"]._bf_sheet_cron()
        for answered in (first, second):
            answered.invalidate_recordset()
            self.assertEqual(answered.bf_sheet_state, "error")
            self.assertEqual(answered.bf_sheet_tries, 1)
            self.assertIn("Nextcloud is down", answered.bf_sheet_message)

    def test_a_failure_is_taken_up_again_at_the_next_round(self):
        """A Nextcloud unreachable for ten minutes delays a sheet; it must not
        lose it. Nothing else ever puts a response back in the round."""
        answered = self._answer()
        for expected_tries in (1, 2, 3):
            with patch.object(type(self.env["survey.user_input"]), "_bf_sheet_file",
                              side_effect=UserError("Nextcloud is down")):
                self.env["survey.user_input"]._bf_sheet_cron()
            answered.invalidate_recordset()
            self.assertEqual(answered.bf_sheet_state, "error")
            self.assertEqual(answered.bf_sheet_tries, expected_tries)

    def test_a_nextcloud_in_pieces_never_breaks_a_submission(self):
        """The promise at the top of the README, measured: the respondent's own
        request touches no network."""
        with patch.object(type(self.config), "_bf_sheet_children",
                          side_effect=UserError("Nextcloud is down")), \
             patch.object(type(self.config), "_bf_sheet_mkdirs",
                          side_effect=UserError("Nextcloud is down")):
            answered = self._answer()
        self.assertEqual(answered.state, "done")
        self.assertEqual(answered.bf_sheet_state, "pending")

    def test_a_response_that_failed_too_often_waits_for_a_human(self):
        answered = self._answer()
        answered.sudo().bf_sheet_tries = 5
        taken = []
        with patch.object(type(self.env["survey.user_input"]), "_bf_sheet_file",
                          side_effect=lambda: taken.append(True)):
            self.env["survey.user_input"]._bf_sheet_cron()
        self.assertFalse(taken, "five tries is enough: the sixth is a human's call")
