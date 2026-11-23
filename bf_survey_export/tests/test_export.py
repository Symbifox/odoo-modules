import io
import zipfile

import openpyxl

from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

SECRET = "RÉPONSE-CONFIDENTIELLE-7Q3"


def _cells(book):
    return [
        c.value
        for ws in book.worksheets
        for row in ws.iter_rows()
        for c in row
        if c.value is not None
    ]


def _question_rows(ws, section_label="Section"):
    header = next(r for r in range(1, 10) if ws.cell(r, 1).value == section_label)
    names = [c.value for c in ws[header]]
    rows = [
        dict(zip(names, (c.value for c in row), strict=True))
        for row in ws.iter_rows(min_row=header + 1)
    ]
    return names, rows


# post_install: the numbers come from bf_survey_numbering, which the registry
# only holds once every module is loaded, as in production.
@tagged("post_install", "-at_install")
class ExportCase(TransactionCase):
    """A workshop questionnaire: sections with help texts, every kind of
    question it uses, a conditional question, a "please specify" box, and one
    response that must never reach the file."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.survey = cls.env["survey.survey"].create(
            {"title": "Workshop: feedback/2026"}
        )
        if "bf_numbering" in cls.survey._fields:
            cls.survey.bf_numbering = False
        cls.q0 = cls._line("Before any section", 5)
        cls.s1 = cls._line(
            "Your details",
            10,
            page=True,
            description="<p>Who answers.</p><p>And <b>for whom</b>.</p>",
        )
        cls.q11 = cls._line("Company name", 20, constr_mandatory=True)
        cls.q12 = cls._line(
            "Has a mentor?",
            30,
            question_type="simple_choice",
            answers=["Yes", "No", "Not yet"],
        )
        cls.q121 = cls._line("Who are they?", 40, question_type="text_box")
        cls.q121.triggering_answer_ids = cls.q12.suggested_answer_ids[:1]
        cls.s2 = cls._line("Your company", 50, page=True)
        cls.q21 = cls._line(
            "Tools used",
            60,
            question_type="multiple_choice",
            answers=["Spreadsheet", "Whiteboard", "Other"],
            comments_allowed=True,
            comments_message="If “Other”, please specify.",
            description="<ol><li>Tick all that apply.</li><li>Or none.</li></ol>",
        )
        cls.q22 = cls._line(
            "Facts",
            70,
            question_type="matrix",
            matrix_subtype="simple",
            answers=["Yes", "No"],
            rows=["Used every day?", "Shared with clients?"],
        )
        cls.q23 = cls._line('=HYPERLINK("https://example.com")', 80)
        # Created last, placed between two others: the order is the survey's.
        cls.q11b = cls._line("Phone", 25)

        # A completed response: none of it may reach the workbook.
        response = cls.env["survey.user_input"].create(
            {"survey_id": cls.survey.id, "state": "done"}
        )
        response._save_lines(cls.q11, SECRET)
        response._save_lines(cls.q12, cls.q12.suggested_answer_ids[1].id)

    @classmethod
    def _line(cls, title, sequence, page=False, answers=(), rows=(), **vals):
        vals.update({"title": title, "sequence": sequence, "is_page": page})
        if not page:
            vals.setdefault("question_type", "char_box")
        if answers:
            vals["suggested_answer_ids"] = [(0, 0, {"value": a}) for a in answers]
        if rows:
            vals["matrix_row_ids"] = [(0, 0, {"value": r}) for r in rows]
        cls.survey.write({"question_and_page_ids": [(0, 0, vals)]})
        return cls.survey.question_and_page_ids.filtered(lambda q: q.title == title)

    def _book(self, survey=None):
        data = (survey or self.survey)._bf_questions_workbook()
        return openpyxl.load_workbook(io.BytesIO(data))

    def _rows(self):
        return {r["title"]: r for r in self.survey._bf_questions_rows()}

    def test_order_and_sections(self):
        rows = self.survey._bf_questions_rows()
        self.assertEqual(
            [r["title"] for r in rows],
            [
                "Before any section",
                "Your details",
                "Company name",
                "Phone",
                "Has a mentor?",
                "Who are they?",
                "Your company",
                "Tools used",
                "Facts",
                '=HYPERLINK("https://example.com")',
            ],
        )
        by_title = {r["title"]: r for r in rows}
        self.assertEqual(by_title["Before any section"]["page"], "")
        self.assertEqual(by_title["Phone"]["page"], "Your details")
        self.assertEqual(by_title["Facts"]["page"], "Your company")

    def test_question_content(self):
        by_title = self._rows()
        self.assertEqual(
            by_title["Your details"]["description"], "Who answers.\nAnd for whom."
        )
        self.assertTrue(by_title["Company name"]["mandatory"])
        self.assertFalse(by_title["Has a mentor?"]["mandatory"])
        self.assertEqual(by_title["Has a mentor?"]["answers"], ["Yes", "No", "Not yet"])
        self.assertEqual(
            by_title["Who are they?"]["condition"], "If “Has a mentor?” = “Yes”"
        )
        self.assertEqual(
            by_title["Tools used"]["specify"], "If “Other”, please specify."
        )
        self.assertEqual(
            by_title["Tools used"]["description"],
            "1. Tick all that apply.\n2. Or none.",
        )
        self.assertEqual(by_title["Facts"]["answers"], ["Yes", "No"])
        self.assertEqual(
            by_title["Facts"]["rows"], ["Used every day?", "Shared with clients?"]
        )
        self.assertIn("(", by_title["Facts"]["type"], "the matrix names its subtype")

    def test_several_triggers_are_joined_by_or(self):
        self.q121.triggering_answer_ids = self.q12.suggested_answer_ids[:2]
        self.assertEqual(
            self._rows()["Who are they?"]["condition"],
            "If “Has a mentor?” = “Yes”\nor\nIf “Has a mentor?” = “No”",
        )

    def test_only_valid_triggers_are_listed(self):
        # One question per page: a second trigger placed after the question is
        # void, Odoo shows the question on the first one only.
        self.survey.questions_layout = "page_per_question"
        later = self._line(
            "Later choice", 45, question_type="simple_choice", answers=["Maybe"]
        )
        self.q121.triggering_answer_ids |= later.suggested_answer_ids
        self.assertEqual(
            self._rows()["Who are they?"]["condition"], "If “Has a mentor?” = “Yes”"
        )

    def test_trigger_placed_after_is_void_one_question_per_page(self):
        self.survey.questions_layout = "page_per_question"
        self.q121.sequence = 22  # before the question that would show it
        self.assertIn("Never shown", self._rows()["Who are they?"]["condition"])

    def test_trigger_placed_after_works_on_one_page(self):
        # On one page the browser shows the question whatever the order.
        self.survey.questions_layout = "one_page"
        self.q121.sequence = 22
        self.assertEqual(
            self._rows()["Who are they?"]["condition"], "If “Has a mentor?” = “Yes”"
        )

    def test_trigger_per_section(self):
        self.survey.questions_layout = "page_per_section"
        # Same section, placed after: shown when ticked.
        self.q121.sequence = 22
        self.assertEqual(
            self._rows()["Who are they?"]["condition"], "If “Has a mentor?” = “Yes”"
        )
        # A trigger in a later section comes too late.
        self.q121.triggering_answer_ids = self.q21.suggested_answer_ids[:1]
        self.assertIn("Never shown", self._rows()["Who are they?"]["condition"])

    def test_trigger_that_is_no_longer_a_choice_is_void(self):
        self.survey.questions_layout = "one_page"
        self.q12.question_type = "text_box"
        self.assertIn("Never shown", self._rows()["Who are they?"]["condition"])

    def test_random_selection_ignores_conditions(self):
        self.survey.questions_selection = "random"
        self.assertEqual(self._rows()["Who are they?"]["condition"], "")

    def test_leftovers_of_a_changed_type_stay_hidden(self):
        # Odoo keeps the choices, rows and comment box of a question whose
        # type changes, and hides them by type: so does the workbook.
        self.q21.question_type = "text_box"
        self.q22.question_type = "simple_choice"
        rows = self._rows()
        self.assertEqual(rows["Tools used"]["answers"], [])
        self.assertEqual(rows["Tools used"]["specify"], "")
        self.assertEqual(rows["Facts"]["rows"], [])
        self.assertEqual(rows["Facts"]["answers"], ["Yes", "No"])
        answers = list(self._book()["Possible answers"].iter_rows(values_only=True))
        self.assertIn(("Your company", "Facts", "Choice", "Yes"), answers)
        self.assertFalse([a for a in answers if a[1] == "Tools used"])

    def test_comment_box_not_allowed_says_nothing(self):
        self.q21.comments_allowed = False
        self.assertEqual(self._rows()["Tools used"]["specify"], "")

    def test_comment_counted_as_answer_is_a_choice(self):
        self.q21.comment_count_as_answer = True
        self.assertEqual(
            self._rows()["Tools used"]["answers"][-1], "If “Other”, please specify."
        )

    def test_scale(self):
        self.q11.write(
            {
                "question_type": "scale",
                "scale_min": 1,
                "scale_max": 5,
                "scale_min_label": "Not at all",
                "scale_mid_label": "Somewhat",
                "scale_max_label": "Fully",
            }
        )
        row = self._rows()["Company name"]
        self.assertEqual(row["answers"], ["1", "2", "3", "4", "5"])
        self.assertIn(
            "Scale labels: Left: Not at all; Centre: Somewhat; Right: Fully",
            row["description"],
        )

    def test_two_value_scale_keeps_both_labels(self):
        self.q11.write(
            {
                "question_type": "scale",
                "scale_min": 0,
                "scale_max": 1,
                "scale_min_label": "No",
                "scale_max_label": "Yes",
            }
        )
        row = self._rows()["Company name"]
        self.assertEqual(row["answers"], ["0", "1"])
        self.assertIn("Scale labels: Left: No; Right: Yes", row["description"])

    def test_placeholder(self):
        self.q11.question_placeholder = "ACME inc."
        self.assertIn("ACME inc.", self._rows()["Company name"]["description"])

    def test_placeholder_hidden_where_odoo_does_not_show_it(self):
        self.q11.write({"question_type": "scale", "scale_min": 1, "scale_max": 3})
        self.q11.question_placeholder = "ACME inc."
        self.assertNotIn("ACME inc.", self._rows()["Company name"]["description"])

    def test_image_only_choice(self):
        self.q12.suggested_answer_ids[2].write(
            {"value": False, "value_image_filename": "logo.png"}
        )
        self.assertEqual(self._rows()["Has a mentor?"]["answers"][2], "(image)")

    def test_list_item_holding_a_paragraph(self):
        self.q11.description = "<ol><li><p>One</p></li><li><p>Two</p></li></ol>"
        self.assertEqual(self._rows()["Company name"]["description"], "1. One\n2. Two")

    def test_workbook_sheets_and_cells(self):
        book = self._book()
        self.assertEqual(book.sheetnames, ["Questions", "Possible answers"])
        ws = book["Questions"]
        self.assertEqual(ws["A1"].value, "Workshop: feedback/2026")
        names, rows = _question_rows(ws)
        self.assertNotIn("No.", names, "no number column when nothing is numbered")
        self.assertEqual(len(rows), 10)
        section = next(
            r for r in rows if r["Section"] == "Your details" and not r["Question"]
        )
        self.assertEqual(section["Help text shown"], "Who answers.\nAnd for whom.")
        cert = next(r for r in rows if r["Question"] == "Tools used")
        self.assertEqual(cert["Section"], "Your company")
        self.assertEqual(
            cert["Possible answers (or matrix columns)"],
            "☐ Spreadsheet\n☐ Whiteboard\n☐ Other",
        )
        self.assertEqual(cert["Mandatory"], "No")
        facts = next(r for r in rows if r["Question"] == "Facts")
        self.assertEqual(
            facts["Rows (matrix or allocation)"],
            "☐ Used every day?\n☐ Shared with clients?",
        )
        # One line per possible answer: 3 + 3 choices, 2 columns + 2 rows.
        answers = list(book["Possible answers"].iter_rows(min_row=2, values_only=True))
        self.assertEqual(len(answers), 10)
        self.assertIn(("Your company", "Facts", "Row", "Shared with clients?"), answers)
        self.assertIn(("Your company", "Facts", "Column", "No"), answers)
        self.assertIn(("Your company", "Tools used", "Choice", "Whiteboard"), answers)

    def test_no_respondent_answer_reaches_the_file(self):
        self.assertTrue(self.survey.user_input_ids.user_input_line_ids)
        data = self.survey._bf_questions_workbook()
        book = openpyxl.load_workbook(io.BytesIO(data))
        self.assertNotIn(SECRET, " ".join(str(v) for v in _cells(book)))
        # Nor hidden anywhere in the archive (shared strings, properties).
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for name in archive.namelist():
                self.assertNotIn(SECRET.encode(), archive.read(name), name)

    def test_no_answer_key(self):
        """The workbook is the same, cell for cell, with or without a key."""
        self.q11.question_type = "numerical_box"
        self.q23.question_type = "date"
        before = _cells(self._book())
        self.survey.scoring_type = "scoring_with_answers"
        self.q12.suggested_answer_ids[0].write({"is_correct": True, "answer_score": 7})
        self.q21.suggested_answer_ids[1].write({"is_correct": True, "answer_score": 3})
        self.q11.write({"answer_numerical_box": 4242, "answer_score": 5})
        self.q23.write({"answer_date": "2031-07-19", "answer_score": 2})
        after = _cells(self._book())
        self.assertEqual(before, after)

    def test_text_is_never_a_formula_nor_a_link(self):
        ws = self._book()["Questions"]
        cell = next(
            c
            for row in ws.iter_rows()
            for c in row
            if isinstance(c.value, str) and c.value.startswith("=HYPERLINK")
        )
        self.assertEqual(cell.data_type, "s")
        self.assertIsNone(cell.hyperlink)

    def test_long_text_is_cut_not_dropped(self):
        self.q11.description = "<p>" + "x" * 40000 + "</p>"
        _names, rows = _question_rows(self._book()["Questions"])
        cell = next(r for r in rows if r["Question"] == "Company name")[
            "Help text shown"
        ]
        self.assertEqual(len(cell), 32767)
        self.assertTrue(cell.endswith("[truncated]"))

    def test_numbers_from_bf_survey_numbering(self):
        if "bf_number" not in self.env["survey.question"]._fields:
            self.skipTest("bf_survey_numbering is not installed")
        self.q0.unlink()  # a numbered survey starts with a section
        self.survey.bf_numbering = True
        names, rows = _question_rows(self._book()["Questions"])
        self.assertIn("No.", names)
        self.assertEqual(
            [(r["No."], r["Question"]) for r in rows if r["No."]][:5],
            [
                ("1.1", "Company name"),
                ("1.2", "Phone"),
                ("1.3", "Has a mentor?"),
                ("1.3.1", "Who are they?"),
                ("2.1", "Tools used"),
            ],
        )
        self.assertEqual(rows[0]["Section"], "1. Your details")
        who = next(r for r in rows if r["Question"] == "Who are they?")
        self.assertEqual(who["Section"], "1. Your details")
        self.assertEqual(who["Shown when"], "If 1.3 = “Yes”")

    def test_random_sections_say_so_only_when_drawing(self):
        self.survey.questions_selection = "random"
        self.s2.random_questions_count = 2
        self.assertIn("2", self._rows()["Your company"]["note"])
        # As many as the section holds (3): Odoo shows them all.
        self.s2.random_questions_count = 3
        self.assertEqual(self._rows()["Your company"]["note"], "")

    def test_language_of_the_exporter(self):
        lang = self.env["res.lang"]._activate_lang("fr_FR")
        self.q11.update_field_translations("title", {lang.code: "Nom de l'entreprise"})
        rows = self.survey.with_context(lang=lang.code)._bf_questions_rows()
        self.assertIn("Nom de l'entreprise", [r["title"] for r in rows])

    def test_filename(self):
        self.assertEqual(
            self.survey._bf_questions_filename(),
            "Workshop feedback 2026 - questions.xlsx",
        )
        self.survey.title = "Report\u202excod.exe"
        self.assertEqual(
            self.survey._bf_questions_filename(), "Report xcod.exe - questions.xlsx"
        )
        self.survey.title = "///"
        self.assertEqual(
            self.survey._bf_questions_filename(), "Survey - questions.xlsx"
        )

    def test_sections_only(self):
        only = self.env["survey.survey"].create(
            {
                "title": "Only",
                "question_and_page_ids": [(0, 0, {"title": "S", "is_page": True})],
            }
        )
        _names, rows = _question_rows(self._book(only)["Questions"])
        self.assertEqual([r["Section"] for r in rows], ["S"])

    def test_empty_survey(self):
        empty = self.env["survey.survey"].create({"title": "Empty"})
        self.assertEqual(
            self._book(empty).sheetnames, ["Questions", "Possible answers"]
        )

    def test_action(self):
        action = self.survey.action_bf_export_questions_xlsx()
        self.assertEqual(action["url"], f"/bf_survey_export/questions/{self.survey.id}")
        self.assertEqual(action["target"], "download")


@tagged("post_install", "-at_install")
class ExportRouteCase(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.survey = cls.env["survey.survey"].create(
            {
                "title": "Route",
                "question_and_page_ids": [
                    (
                        0,
                        0,
                        {
                            "title": "Q1",
                            "question_type": "char_box",
                            "constr_mandatory": True,
                        },
                    )
                ],
            }
        )
        cls.manager = new_test_user(
            cls.env,
            "bf_export_mgr",
            groups="survey.group_survey_manager",
            password="bf_export_mgr_pw_1",
        )
        cls.officer = new_test_user(
            cls.env,
            "bf_export_off",
            groups="survey.group_survey_user",
            password="bf_export_off_pw_1",
        )
        cls.employee = new_test_user(
            cls.env,
            "bf_export_emp",
            groups="base.group_user",
            password="bf_export_emp_pw_1",
        )

    def _get(self, survey_id, login=None):
        if login:
            self.authenticate(login, f"{login}_pw_1")
        return self.url_open(
            f"/bf_survey_export/questions/{survey_id}", allow_redirects=False
        )

    def test_manager_downloads(self):
        res = self._get(self.survey.id, "bf_export_mgr")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(
            res.headers["Content-Type"].startswith(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        )
        self.assertIn("Route%20-%20questions.xlsx", res.headers["Content-Disposition"])
        self.assertEqual(res.headers.get("Cache-Control"), "no-store")
        book = openpyxl.load_workbook(io.BytesIO(res.content))
        self.assertEqual(book["Questions"]["A1"].value, "Route")

    def test_in_the_language_of_the_exporter(self):
        lang = self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"]._load_module_terms(
            ["bf_survey_export"], [lang.code]
        )
        self.manager.lang = lang.code
        res = self._get(self.survey.id, "bf_export_mgr")
        book = openpyxl.load_workbook(io.BytesIO(res.content))
        self.assertEqual(book.sheetnames, ["Questions", "Réponses possibles"])
        names, rows = _question_rows(book["Questions"])
        self.assertIn("Type de réponse", names)
        self.assertIn("Condition d'affichage", names)
        self.assertEqual(rows[0]["Obligatoire"], "Oui")

    def test_employee_without_survey_rights_gets_nothing(self):
        res = self._get(self.survey.id, "bf_export_emp")
        self.assertEqual(res.status_code, 404)
        self.assertNotIn(b"Q1", res.content)

    def test_officer_outside_restricted_users_gets_nothing(self):
        self.survey.restrict_user_ids = self.manager
        self.env.flush_all()  # the request reads the database, not this cache
        res = self._get(self.survey.id, "bf_export_off")
        self.assertEqual(res.status_code, 404)
        self.survey.restrict_user_ids = self.officer
        self.env.flush_all()
        res = self._get(self.survey.id, "bf_export_off")
        self.assertEqual(res.status_code, 200)

    def test_unknown_survey(self):
        res = self._get(999999999, "bf_export_mgr")
        self.assertEqual(res.status_code, 404)

    def test_anonymous_is_sent_to_login(self):
        res = self._get(self.survey.id)
        self.assertIn(res.status_code, (302, 303))
        self.assertIn("/web/login", res.headers.get("Location", ""))
