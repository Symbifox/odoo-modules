import base64

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

QUESTIONS = "bf_survey_export.action_report_bf_survey_questions"
ANSWERS = "bf_survey_export.action_report_bf_survey_answers"
SECRET = "RÉPONSE-CONFIDENTIELLE-PDF-41"
# The first bytes of a TrueType file, enough for the format check.
TTF = base64.b64encode(b"\x00\x01\x00\x00" + b"\x00" * 60)


@tagged("post_install", "-at_install")
class PdfCase(TransactionCase):
    """A small questionnaire and one response to it, printed both ways."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.survey = cls.env["survey.survey"].create(
            {
                "title": "Workshop intake",
                "description": "<p>Tell us about your workshop.</p>",
                "description_done": "<p>Thank you for your time.</p>",
            }
        )
        if "bf_numbering" in cls.survey._fields:
            cls.survey.bf_numbering = False
        cls.s1 = cls._line("About you", 10, page=True)
        cls.q_name = cls._line("Your name", 20, constr_mandatory=True)
        cls.q_mentor = cls._line(
            "Has a mentor?",
            30,
            question_type="simple_choice",
            answers=["Yes", "No"],
        )
        cls.q_who = cls._line("Who is it?", 40, question_type="text_box")
        cls.q_who.triggering_answer_ids = cls.q_mentor.suggested_answer_ids[:1]
        cls.q_tools = cls._line(
            "Tools used",
            50,
            question_type="multiple_choice",
            answers=["Spreadsheet", "Whiteboard", "Other"],
            comments_allowed=True,
            comments_message="If other, which one:",
        )
        cls.s2 = cls._line("Your company", 60, page=True)
        cls.q_staff = cls._line("Staff", 70, question_type="numerical_box")
        cls.q_founded = cls._line("Founded on", 80, question_type="date")
        cls.q_facts = cls._line(
            "Facts",
            90,
            question_type="matrix",
            matrix_subtype="simple",
            answers=["True", "False"],
            rows=["Exports", "Hires"],
        )
        cls.q_skip = cls._line("Anything else?", 95)
        cls.s3 = cls._line("Mentor details", 100, page=True)
        cls.q_mentor_since = cls._line("Mentor since", 110)
        cls.q_mentor_since.triggering_answer_ids = cls.q_mentor.suggested_answer_ids[:1]

        cls.response = cls.env["survey.user_input"].create(
            {
                "survey_id": cls.survey.id,
                "state": "done",
                "email": "respondent@example.com",
            }
        )
        r = cls.response
        r._save_lines(cls.q_name, SECRET)
        # "No": the mentor questions stay hidden from this respondent.
        r._save_lines(cls.q_mentor, cls.q_mentor.suggested_answer_ids[1].id)
        r._save_lines(
            cls.q_tools,
            [cls.q_tools.suggested_answer_ids[0].id],
            comment="Kanban board",
        )
        r._save_lines(cls.q_staff, "12")
        r._save_lines(cls.q_founded, "2002-05-17")
        r._save_lines(
            cls.q_facts,
            {
                cls.q_facts.matrix_row_ids[0].id: [
                    cls.q_facts.suggested_answer_ids[0].id
                ]
            },
        )
        r._save_lines(cls.q_skip, "")

        cls.Theme = cls.env["bf.survey.pdf.theme"]
        cls.theme = cls.Theme.create(
            {
                "name": "Partner programme",
                "kicker": "Partner programme",
                "signature": "Printed by the programme",
                "color_primary": "#2B7ABF",
                "color_accent": "#F47D25",
            }
        )

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

    def _html(self, report, records):
        html, _kind = self.env["ir.actions.report"]._render_qweb_html(
            report, records.ids
        )
        return html.decode()

    def _sections(self):
        return {s["title"]: s for s in self.response._bf_answer_sections()}

    def _items(self):
        return {
            i["title"]: i
            for s in self.response._bf_answer_sections()
            for i in s["items"]
        }

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def test_without_theme_the_company_colours(self):
        company = self.env.company
        company.write({"primary_color": "#112233", "secondary_color": "#445566"})
        style = self.survey._bf_pdf_style()
        self.assertEqual(style["primary"], "#112233")
        self.assertEqual(style["accent"], "#445566")
        self.assertEqual(style["signature"], company.name)
        self.assertEqual(style["kicker"], "")
        self.assertTrue(style["logo_boxed"])

    def test_without_theme_nor_company_colours_neutral_ones(self):
        self.env.company.write({"primary_color": False, "secondary_color": False})
        style = self.survey._bf_pdf_style()
        self.assertEqual(style["primary"], "#1F3864")
        self.assertEqual(style["accent"], "#2E75B6")

    def test_theme_wins(self):
        self.env.company.write({"primary_color": "#112233"})
        self.survey.bf_pdf_theme_id = self.theme
        style = self.survey._bf_pdf_style()
        self.assertEqual(style["primary"], "#2B7ABF")
        self.assertEqual(style["accent"], "#F47D25")
        self.assertEqual(style["kicker"], "Partner programme")
        self.assertEqual(style["signature"], "Printed by the programme")

    def test_colour_must_be_a_code(self):
        for bad in (
            "red",
            "#12345",
            "#1234567",
            "#12345G",
            "#123456;}body{x:y",
            "#123456\n",
        ):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.theme.color_accent = bad

    def test_font_name_cannot_leave_the_rule(self):
        self.theme.font_family = "Lexend, 'Liberation Sans'"
        for bad in (
            "A; } body { display: none",
            "A</style><script>",
            "A{",
            "Lexend\n",
            "Lexend'",
            "'Lexend",
        ):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.theme.font_family = bad

    def test_unsafe_values_in_the_database_are_not_used(self):
        """A value written past the constraints (SQL, import) never reaches
        the stylesheet."""
        self.env.company.write({"primary_color": False, "secondary_color": False})
        self.survey.bf_pdf_theme_id = self.theme
        self.env.cr.execute(
            "UPDATE bf_survey_pdf_theme SET color_primary = %s, font_family = %s "
            "WHERE id = %s",
            ("red;}</style><script>x()</script>", "A;}</style>", self.theme.id),
        )
        self.theme.invalidate_recordset()
        style = self.survey._bf_pdf_style()
        css = str(self.Theme._bf_css(style))
        self.assertNotIn("script", css)
        self.assertNotIn("</style>", css)
        self.assertEqual(style["primary"], "#1F3864")
        self.assertNotIn("A;}", style["family"])

    def test_font_file_size(self):
        big = base64.b64encode(b"\x00\x01\x00\x00" + b"\x00" * (1024 * 1024 + 10))
        with self.assertRaises(ValidationError):
            self.theme.font_regular = big

    def test_font_file(self):
        with self.assertRaises(ValidationError):
            self.theme.font_regular = base64.b64encode(b"<svg>not a font</svg>")
        self.theme.font_regular = TTF
        self.survey.bf_pdf_theme_id = self.theme
        style = self.survey._bf_pdf_style()
        self.assertIn("@font-face", style["faces"])
        self.assertIn("format('truetype')", style["faces"])
        self.assertTrue(style["family"].startswith("'BF Survey Theme'"))

    def test_officer_reads_themes_manager_writes(self):
        officer = new_test_user(
            self.env, "bf_pdf_off", groups="base.group_user,survey.group_survey_user"
        )
        self.assertEqual(self.theme.with_user(officer).name, "Partner programme")
        with self.assertRaises(AccessError):
            self.theme.with_user(officer).name = "Mine"
        manager = new_test_user(
            self.env, "bf_pdf_mgr", groups="base.group_user,survey.group_survey_manager"
        )
        self.theme.with_user(manager).name = "Renamed"

    def test_theme_of_another_company_is_not_seen(self):
        other = self.env["res.company"].create({"name": "Other company"})
        self.theme.company_id = other
        officer = new_test_user(
            self.env, "bf_pdf_off2", groups="base.group_user,survey.group_survey_user"
        )
        self.assertFalse(
            self.Theme.with_user(officer).search([("id", "=", self.theme.id)])
        )

    def test_theme_of_another_company_still_prints(self):
        """A theme an administrator picked is drawn for every person who may
        print the survey, in whichever company they are."""
        other = self.env["res.company"].create({"name": "Other company"})
        self.theme.company_id = other
        self.survey.bf_pdf_theme_id = self.theme
        officer = new_test_user(
            self.env, "bf_pdf_off4", groups="base.group_user,survey.group_survey_user"
        )
        Report = self.env["ir.actions.report"].with_user(officer)
        html = Report._render_qweb_html(ANSWERS, self.response.ids)[0].decode()
        self.assertIn("Printed by the programme", html)
        html = Report._render_qweb_html(QUESTIONS, self.survey.ids)[0].decode()
        self.assertIn("#2B7ABF", html)

    # ------------------------------------------------------------------
    # Blank questionnaire
    # ------------------------------------------------------------------

    def test_blank_shows_the_questionnaire(self):
        self.survey.bf_pdf_theme_id = self.theme
        html = self._html(QUESTIONS, self.survey)
        for text in (
            "Workshop intake",
            "Partner programme",
            "Printed by the programme",
            "Tell us about your workshop.",
            "About you",
            "Your name",
            "Mandatory",
            "Spreadsheet",
            "Whiteboard",
            "If other, which one:",
            "Exports",
            "Thank you for your time.",
            "#2B7ABF",
        ):
            self.assertIn(text, html)
        # The condition of the mentor question, in the Excel export's words.
        self.assertIn("If “Has a mentor?” = “Yes”", html.replace("&#34;", '"'))

    def test_blank_has_no_answer(self):
        self.assertIn(
            SECRET, self.response.user_input_line_ids.mapped("value_char_box")
        )
        html = self._html(QUESTIONS, self.survey)
        self.assertNotIn(SECRET, html)
        self.assertNotIn("Kanban board", html)

    def test_blank_has_no_answer_key(self):
        before = self._html(QUESTIONS, self.survey)
        self.survey.scoring_type = "scoring_with_answers"
        self.q_mentor.suggested_answer_ids[0].write(
            {"is_correct": True, "answer_score": 7}
        )
        self.q_staff.write({"answer_numerical_box": 4242, "answer_score": 5})
        after = self._html(QUESTIONS, self.survey)
        self.assertEqual(before, after)

    def test_blank_comment_counted_as_answer_is_not_labelled_twice(self):
        self.q_mentor.write(
            {
                "comments_allowed": True,
                "comment_count_as_answer": True,
                "comments_message": "Something else, namely:",
            }
        )
        html = self._html(QUESTIONS, self.survey)
        self.assertEqual(html.count("Something else, namely:"), 1)

    def test_section_title_stays_with_its_first_question(self):
        rows = self.survey._bf_questions_rows()
        blocks = self.survey._bf_pdf_blocks(rows)
        first = blocks[0]
        self.assertEqual(first["section"]["title"], "About you")
        self.assertEqual(first["question"]["title"], "Your name")
        self.assertEqual(
            sum(1 for b in blocks if b["question"]),
            sum(1 for r in rows if not r["section"]),
        )

    def test_blank_counts(self):
        rows = self.survey._bf_questions_rows()
        self.assertEqual(
            self.survey._bf_pdf_counts(rows), "3 section(s) · 9 question(s)"
        )

    # ------------------------------------------------------------------
    # One response
    # ------------------------------------------------------------------

    def test_response_ticked_choices_only(self):
        tools = self._items()["Tools used"]
        self.assertEqual(tools["kind"], "choices")
        self.assertEqual(tools["values"], ["Spreadsheet"])
        # The "please specify" text, under the name of its own box.
        self.assertEqual(tools["comments"], [("If other, which one:", "Kanban board")])
        self.assertEqual(self._items()["Has a mentor?"]["values"], ["No"])

    def test_response_questions_hidden_from_the_respondent_are_left_out(self):
        items = self._items()
        self.assertNotIn("Who is it?", items)
        # A section whose questions were all hidden is not printed at all.
        self.assertNotIn("Mentor details", self._sections())

    def test_response_hidden_question_shows_once_triggered(self):
        self.response.user_input_line_ids.filtered(
            lambda line: line.question_id == self.q_mentor
        ).unlink()
        self.response._save_lines(
            self.q_mentor, self.q_mentor.suggested_answer_ids[0].id
        )
        items = self._items()
        self.assertEqual(items["Who is it?"]["kind"], "none")
        self.assertIn("Mentor details", self._sections())

    def test_response_random_selection_prints_only_questions_drawn(self):
        self.survey.questions_selection = "random"
        drawn = self.survey.question_ids - self.q_skip
        self.response.predefined_question_ids = drawn
        self.assertNotIn("Anything else?", self._items())
        self.assertIn("Staff", self._items())

    def test_response_matrix_comment(self):
        self.q_facts.write(
            {"comments_allowed": True, "comments_message": "Anything to add?"}
        )
        self.response._save_lines(
            self.q_facts,
            {
                self.q_facts.matrix_row_ids[0].id: [
                    self.q_facts.suggested_answer_ids[0].id
                ]
            },
            comment="Mostly in winter",
        )
        facts = self._items()["Facts"]
        self.assertEqual(facts["comments"], [("Anything to add?", "Mostly in winter")])
        self.assertIn("Mostly in winter", self._html(ANSWERS, self.response))

    def test_response_values(self):
        items = self._items()
        self.assertEqual(
            items["Your name"], {"title": "Your name", "kind": "text", "value": SECRET}
        )
        self.assertEqual(items["Staff"]["value"], "12")
        self.assertIn("2002", items["Founded on"]["value"])
        self.assertEqual(items["Facts"]["rows"], [("Exports", "True"), ("Hires", "—")])
        self.assertEqual(items["Anything else?"]["kind"], "none")

    def test_response_unanswered_section(self):
        self.response.user_input_line_ids.filtered(
            lambda line: line.question_id.page_id == self.s2
        ).unlink()
        section = self._sections()["Your company"]
        self.assertFalse(section["answered"])
        self.assertTrue(self._sections()["About you"]["answered"])

    def test_response_pdf_shows_the_answers(self):
        self.survey.bf_pdf_theme_id = self.theme
        html = self._html(ANSWERS, self.response)
        for text in (
            "Workshop intake",
            "respondent@example.com",
            SECRET,
            "Spreadsheet",
            "Kanban board",
            "No answer",
            "Partner programme",
        ):
            self.assertIn(text, html)
        self.assertNotIn("Whiteboard", html)
        self.assertNotIn("Who is it?", html)

    def test_response_has_no_score(self):
        self.survey.scoring_type = "scoring_with_answers"
        self.q_mentor.suggested_answer_ids[1].write(
            {"is_correct": True, "answer_score": 7}
        )
        before = self.response._bf_answer_sections()
        self.q_mentor.suggested_answer_ids[1].write({"is_correct": False})
        self.assertEqual(before, self.response._bf_answer_sections())

    def test_response_meta(self):
        meta = dict(self.response._bf_pdf_meta())
        self.assertEqual(meta["Respondent"], "respondent@example.com")
        self.assertIn("Submitted on", meta)
        self.response.state = "in_progress"
        meta = dict(self.response._bf_pdf_meta())
        self.assertIn("Status", meta)

    # ------------------------------------------------------------------
    # Rights, names, language, the PDF itself
    # ------------------------------------------------------------------

    def test_printed_as_the_person_who_prints(self):
        employee = new_test_user(self.env, "bf_pdf_emp", groups="base.group_user")
        Report = self.env["ir.actions.report"].with_user(employee)
        with self.assertRaises(AccessError):
            Report._render_qweb_html(QUESTIONS, self.survey.ids)
        with self.assertRaises(AccessError):
            Report._render_qweb_html(ANSWERS, self.response.ids)

    def test_officer_outside_restricted_users(self):
        officer = new_test_user(
            self.env, "bf_pdf_off3", groups="base.group_user,survey.group_survey_user"
        )
        manager = new_test_user(
            self.env,
            "bf_pdf_mgr3",
            groups="base.group_user,survey.group_survey_manager",
        )
        self.survey.restrict_user_ids = manager
        self.env.flush_all()
        Report = self.env["ir.actions.report"].with_user(officer)
        with self.assertRaises(AccessError):
            Report._render_qweb_html(QUESTIONS, self.survey.ids)
        with self.assertRaises(AccessError):
            Report._render_qweb_html(ANSWERS, self.response.ids)
        self.survey.restrict_user_ids = officer
        self.env.flush_all()
        self.env.invalidate_all()
        Report._render_qweb_html(ANSWERS, self.response.ids)
        Report._render_qweb_html(QUESTIONS, self.survey.ids)

    def test_file_names(self):
        self.survey.title = 'Intake: "2026"/Q1'
        report = self.env["ir.actions.report"]._get_report(QUESTIONS)
        self.assertEqual(report.print_report_name, "object._bf_pdf_filename()")
        self.assertEqual(self.survey._bf_pdf_filename(), "Intake 2026 Q1 - questions")
        self.assertEqual(
            self.response._bf_pdf_filename(),
            "Intake 2026 Q1 - respondent@example.com",
        )

    def test_actions(self):
        action = self.survey.action_bf_export_questions_pdf()
        self.assertEqual(
            action["report_name"], "bf_survey_export.report_bf_survey_questions"
        )
        action = self.response.action_bf_export_answers_pdf()
        self.assertEqual(
            action["report_name"], "bf_survey_export.report_bf_survey_answers"
        )

    def test_in_the_language_of_the_person_who_prints(self):
        lang = self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"]._load_module_terms(
            ["bf_survey_export"], [lang.code]
        )
        Report = self.env["ir.actions.report"].with_context(lang=lang.code)
        html = Report._render_qweb_html(QUESTIONS, self.survey.ids)[0].decode()
        self.assertIn("Obligatoire", html)
        self.assertNotIn(">Mandatory<", html)
        html = Report._render_qweb_html(ANSWERS, self.response.ids)[0].decode()
        self.assertIn("Sans réponse", html)
        self.assertIn("Répondant", html)

    def test_real_pdf(self):
        Report = self.env["ir.actions.report"].with_context(force_report_rendering=True)
        for report, records in ((QUESTIONS, self.survey), (ANSWERS, self.response)):
            pdf, kind = Report._render_qweb_pdf(report, records.ids)
            self.assertEqual(kind, "pdf")
            self.assertTrue(pdf.startswith(b"%PDF"), report)
