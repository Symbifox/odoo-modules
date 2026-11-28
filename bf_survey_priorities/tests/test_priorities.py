import io
import re
import zipfile

import openpyxl

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from odoo.addons.survey.tests.common import SurveyCase

TREND_PDF = "bf_survey_priorities.action_report_bf_survey_trend"


class PrioritiesCase(TransactionCase):
    """A workshop survey: four needs, ranked priority 1, 2 and 3."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.survey = cls.env["survey.survey"].create(
            {
                "title": "Workshop intake",
                "users_can_go_back": False,
                "questions_layout": "page_per_section",
            }
        )
        if "bf_numbering" in cls.survey._fields:
            cls.survey.bf_numbering = False
        cls.page = cls._line("Your needs", 10, page=True)
        cls.question = cls._line(
            "Your three most important needs",
            20,
            question_type="matrix",
            matrix_subtype="simple",
            answers=[("Priority 1", 10), ("Priority 2", 5), ("Priority 3", 2)],
            rows=["Funding", "Hiring", "Training", "Marketing"],
        )
        cls.p1, cls.p2, cls.p3 = cls.question.suggested_answer_ids.sorted("sequence")
        cls.funding, cls.hiring, cls.training, cls.marketing = (
            cls.question.matrix_row_ids.sorted("sequence")
        )

    @classmethod
    def _line(cls, title, sequence, page=False, answers=(), rows=(), **vals):
        vals.update({"title": title, "sequence": sequence, "is_page": page})
        if not page:
            vals.setdefault("question_type", "char_box")
        if answers:
            vals["suggested_answer_ids"] = [
                (0, 0, {"value": a, "sequence": i, "bf_rank_points": p})
                for i, (a, p) in enumerate(answers)
            ]
        if rows:
            vals["matrix_row_ids"] = [
                (0, 0, {"value": r, "sequence": i}) for i, r in enumerate(rows)
            ]
        cls.survey.write({"question_and_page_ids": [(0, 0, vals)]})
        return cls.survey.question_and_page_ids.filtered(lambda q: q.title == title)

    def _respond(self, ticks, state="done", test_entry=False):
        """ticks: {row: [column, ...]}"""
        user_input = self.env["survey.user_input"].create(
            {"survey_id": self.survey.id, "state": state, "test_entry": test_entry}
        )
        user_input._save_lines(
            self.question,
            {
                str(row.id): [str(c.id) for c in columns]
                for row, columns in ticks.items()
            },
        )
        return user_input

    def _trend(self):
        return self.survey._bf_trend()["questions"][0]

    @staticmethod
    def _points(item):
        return {r["label"]: r["points"] for r in item["rows"]}


@tagged("post_install", "-at_install")
class TestTrend(PrioritiesCase):
    def test_points_follow_the_columns(self):
        self._respond(
            {self.funding: [self.p1], self.hiring: [self.p2], self.training: [self.p3]}
        )
        self._respond(
            {self.hiring: [self.p1], self.funding: [self.p2], self.marketing: [self.p3]}
        )
        item = self._trend()
        self.assertEqual(
            self._points(item),
            {"Funding": 15.0, "Hiring": 15.0, "Training": 2.0, "Marketing": 2.0},
        )
        self.assertEqual(item["respondents"], 2)
        self.assertEqual(item["shared"], 0)
        self.assertEqual(item["total"], 34.0)
        # Equal points share a rank, and the next rank skips.
        self.assertEqual([r["rank"] for r in item["rows"]], [1, 1, 3, 3])
        funding = next(r for r in item["rows"] if r["label"] == "Funding")
        self.assertEqual(funding["mentions"], [1, 1, 0])
        self.assertEqual(funding["share"], 44.1)
        self.assertEqual(funding["bar"], 100.0)

    def test_several_rows_in_one_column_share_its_points(self):
        # Three rows under priority 1: 10 points split three ways. This
        # respondent weighs 10 + 5, never 3 x 10 + 5.
        self._respond(
            {
                self.funding: [self.p1],
                self.hiring: [self.p1],
                self.training: [self.p1],
                self.marketing: [self.p2],
            }
        )
        item = self._trend()
        points = self._points(item)
        self.assertAlmostEqual(points["Funding"], 3.33)
        self.assertEqual(points["Marketing"], 5.0)
        self.assertAlmostEqual(item["total"], 15.0)
        self.assertEqual(item["shared"], 1)
        self.assertEqual(item["rows"][0]["label"], "Marketing")

    def test_an_empty_column_gives_nothing(self):
        self._respond({self.funding: [self.p1]})
        item = self._trend()
        self.assertEqual(item["total"], 10.0)
        self.assertEqual(item["rows"][0]["label"], "Funding")
        # Rows nobody ticked are listed, without a rank.
        self.assertEqual([r["rank"] for r in item["rows"]], [1, None, None, None])

    def test_only_completed_real_responses_count(self):
        self._respond({self.funding: [self.p1]})
        self._respond({self.hiring: [self.p1]}, test_entry=True)
        self._respond({self.training: [self.p1]}, state="in_progress")
        item = self._trend()
        self.assertEqual(self._points(item)["Funding"], 10.0)
        self.assertEqual(self._points(item)["Hiring"], 0.0)
        self.assertEqual(self._points(item)["Training"], 0.0)
        self.assertEqual(self.survey._bf_trend()["completed"], 1)

    def test_a_skipped_matrix_is_not_a_respondent(self):
        self._respond({})
        self._respond({self.funding: [self.p1]})
        self.assertEqual(self._trend()["respondents"], 1)

    def test_a_column_without_points_still_counts_mentions(self):
        self.p3.bf_rank_points = 0
        self._respond({self.funding: [self.p3]})
        funding = next(r for r in self._trend()["rows"] if r["label"] == "Funding")
        self.assertEqual(funding["points"], 0.0)
        self.assertEqual(funding["mentions"], [0, 0, 1])
        self.assertIsNone(funding["rank"])

    def test_points_changed_later_reweigh_the_answers(self):
        self._respond({self.funding: [self.p1], self.hiring: [self.p2]})
        self.p1.bf_rank_points = 1
        self.assertEqual(self._points(self._trend())["Hiring"], 5.0)
        self.assertEqual(self._trend()["rows"][0]["label"], "Hiring")

    def test_rows_are_added_up_by_group(self):
        self.funding.bf_rank_group = "Money"
        self.marketing.bf_rank_group = "Money"
        self.hiring.bf_rank_group = " People "
        self._respond(
            {self.funding: [self.p1], self.hiring: [self.p2], self.training: [self.p3]}
        )
        self._respond({self.marketing: [self.p1]})
        groups = {g["label"]: g for g in self._trend()["groups"]}
        self.assertEqual(groups["Money"]["points"], 20.0)
        self.assertEqual(groups["Money"]["rows"], 2)
        self.assertEqual(groups["People"]["points"], 5.0)
        self.assertEqual(groups["No group"]["points"], 2.0)
        self.assertEqual(
            [g["label"] for g in self._trend()["groups"]],
            ["Money", "People", "No group"],
        )

    def test_groups_ignore_case_and_never_merge_with_no_group(self):
        self.funding.bf_rank_group = "Money"
        self.marketing.bf_rank_group = "money"
        self.hiring.bf_rank_group = "No group"
        self._respond(
            {self.funding: [self.p1], self.marketing: [self.p2], self.hiring: [self.p3]}
        )
        self._respond({self.training: [self.p1]})
        groups = self._trend()["groups"]
        self.assertEqual(
            [(g["label"], g["points"], g["rows"]) for g in groups],
            [("Money", 15.0, 2), ("No group", 10.0, 1), ("No group", 2.0, 1)],
        )

    def test_a_group_label_does_not_move_with_the_answers(self):
        self.funding.bf_rank_group = "Money"
        self.marketing.bf_rank_group = "MONEY"
        self._respond({self.marketing: [self.p1]})
        self.assertEqual(self._trend()["groups"][0]["label"], "Money")

    def test_mentions_in_words(self):
        self._respond({self.funding: [self.p1]})
        item = self._trend()
        funding = next(r for r in item["rows"] if r["label"] == "Funding")
        self.assertEqual(
            self.question.with_context(lang="en_US")._bf_rank_mentions(item, funding),
            "Priority 1: 1 · Priority 2: 0 · Priority 3: 0",
        )

    def test_no_group_no_group_table(self):
        self._respond({self.funding: [self.p1]})
        self.assertEqual(self._trend()["groups"], [])

    def test_only_matrices_with_points_are_weighted(self):
        self.assertTrue(self.question.bf_rank_weighted)
        self.assertTrue(self.survey.bf_rank_has_weighted)
        self.question.suggested_answer_ids.write({"bf_rank_points": 0})
        self.assertFalse(self.question.bf_rank_weighted)
        self.assertFalse(self.survey.bf_rank_has_weighted)
        self.assertEqual(self.survey._bf_trend()["questions"], [])

    def test_negative_points_are_refused(self):
        with self.assertRaises(ValidationError):
            self.p1.bf_rank_points = -1

    def test_the_rule_in_words(self):
        self.assertEqual(
            self.question._bf_rank_rule(),
            "Priority 1 = 10 pts · Priority 2 = 5 pts · Priority 3 = 2 pts",
        )

    def test_numbers_in_the_readers_language(self):
        self.env["res.lang"]._activate_lang("fr_FR")
        survey = self.survey.with_context(lang="fr_FR")
        self.assertEqual(survey._bf_num(2.5), "2,5")
        self.assertEqual(survey._bf_num(10.0), "10")
        self.env["res.lang"]._activate_lang("en_US")
        english = self.survey.with_context(lang="en_US")
        self.assertEqual(english._bf_num(33.33), "33.33")
        self.assertEqual(english._bf_num(1234.5), "1,234.5")

    def test_statistics_carry_the_trend(self):
        self._respond({self.funding: [self.p1]})
        lines = self.env["survey.user_input.line"].search(
            [("question_id", "=", self.question.id)]
        )
        data = self.survey.question_and_page_ids._prepare_statistics(lines)
        by_question = {d["question"]: d for d in data}
        self.assertNotIn("bf_rank", by_question[self.page])
        self.assertEqual(
            by_question[self.question]["bf_rank"]["rows"][0]["label"], "Funding"
        )


@tagged("post_install", "-at_install")
class TestOnePerColumn(PrioritiesCase):
    def _validate(self, ticks):
        answer = {
            str(row.id): [str(c.id) for c in columns] for row, columns in ticks.items()
        }
        return self.question.validate_question(answer)

    def test_off_by_default(self):
        self.assertFalse(self.question.bf_one_per_column)
        self.assertEqual(
            self._validate({self.funding: [self.p1], self.hiring: [self.p1]}), {}
        )

    def test_two_rows_in_one_column_are_refused(self):
        self.question.bf_one_per_column = True
        errors = self._validate({self.funding: [self.p1], self.hiring: [self.p1]})
        self.assertIn(self.question.id, errors)
        self.assertIn("only one row in each column", errors[self.question.id])

    def test_one_row_per_column_passes(self):
        self.question.bf_one_per_column = True
        self.assertEqual(
            self._validate(
                {
                    self.funding: [self.p1],
                    self.hiring: [self.p2],
                    self.training: [self.p3],
                }
            ),
            {},
        )
        self.assertEqual(self._validate({self.funding: [self.p1]}), {})

    def test_multiple_subtype_checks_columns_too(self):
        self.question.write({"bf_one_per_column": True, "matrix_subtype": "multiple"})
        # One row under two columns is fine; two rows under one column is not.
        self.assertEqual(self._validate({self.funding: [self.p1, self.p2]}), {})
        errors = self._validate(
            {self.funding: [self.p1, self.p2], self.hiring: [self.p2]}
        )
        self.assertIn(self.question.id, errors)

    def test_native_errors_come_first(self):
        # As many columns as rows: the pair of options is allowed.
        self.question.matrix_row_ids[3:].unlink()
        self.question.write({"bf_one_per_column": True, "constr_mandatory": True})
        errors = self._validate({self.funding: [self.p1]})
        self.assertIn(self.question.id, errors)
        self.assertNotIn("only one row", errors[self.question.id])

    def test_a_column_written_07_is_the_same_column(self):
        self.question.bf_one_per_column = True
        answer = {
            str(self.funding.id): [str(self.p1.id)],
            str(self.hiring.id): [f"0{self.p1.id}"],
        }
        self.assertIn(self.question.id, self.question.validate_question(answer))

    def test_mandatory_with_more_rows_than_columns_is_refused(self):
        self.question.constr_mandatory = True
        with self.assertRaises(ValidationError):
            self.question.bf_one_per_column = True

    def test_mandatory_with_as_many_columns_as_rows_is_allowed(self):
        self.question.matrix_row_ids[3:].unlink()
        self.question.write({"constr_mandatory": True, "bf_one_per_column": True})
        self.assertTrue(self.question.bf_one_per_column)

    def test_a_row_added_or_a_column_removed_on_its_own_is_checked(self):
        self.question.matrix_row_ids[3:].unlink()
        self.question.write({"constr_mandatory": True, "bf_one_per_column": True})
        answers = self.env["survey.question.answer"]
        with self.assertRaises(ValidationError):
            answers.create({"matrix_question_id": self.question.id, "value": "Travel"})
        with self.assertRaises(ValidationError):
            self.p3.unlink()
        # A column added keeps the question fillable.
        answers.create({"question_id": self.question.id, "value": "Priority 4"})

    def test_the_error_is_in_french(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"]._load_module_terms(
            ["bf_survey_priorities"], ["fr_CA"]
        )
        self.question.bf_one_per_column = True
        question = self.question.with_context(lang="fr_CA")
        answer = {
            str(self.funding.id): [str(self.p1.id)],
            str(self.hiring.id): [str(self.p1.id)],
        }
        errors = question.validate_question(answer)
        self.assertEqual(
            errors[self.question.id],
            "Choisissez une seule ligne par colonne : décochez les lignes en trop.",
        )


@tagged("post_install", "-at_install")
class TestExports(PrioritiesCase, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The labels checked below are the English source: the database's own
        # language may be another one.
        cls.env["res.lang"]._activate_lang("en_US")
        cls.manager = new_test_user(
            cls.env,
            login="bf_rank_manager",
            groups="base.group_user,survey.group_survey_manager",
            lang="en_US",
        )
        cls.employee = new_test_user(
            cls.env, login="bf_rank_employee", groups="base.group_user"
        )

    def _xlsx_strings(self, data):
        with zipfile.ZipFile(io.BytesIO(data)) as book:
            shared = book.read("xl/sharedStrings.xml").decode()
        return re.findall(r"<t[^>]*>([^<]*)</t>", shared)

    def test_workbook(self):
        self.hiring.bf_rank_group = "=HYPERLINK(1)"
        self._respond({self.funding: [self.p1], self.hiring: [self.p1]})
        self._respond({self.hiring: [self.p1]})
        self._respond({self.training: [self.p1]}, test_entry=True)
        self.authenticate("bf_rank_manager", "bf_rank_manager")
        self.env.flush_all()
        response = self.url_open(f"/bf_survey_priorities/trend/{self.survey.id}")
        self.assertEqual(response.status_code, 200)
        disposition = response.headers["Content-Disposition"]
        self.assertIn("Workshop%20intake%20-%20", disposition)
        self.assertTrue(disposition.endswith(".xlsx"))
        strings = self._xlsx_strings(response.content)
        self.assertIn("Your three most important needs", strings)
        self.assertIn("Funding", strings)
        # A group typed like a formula stays text.
        self.assertIn("=HYPERLINK(1)", strings)
        with zipfile.ZipFile(io.BytesIO(response.content)) as book:
            sheet = book.read("xl/worksheets/sheet1.xml").decode()
        self.assertNotIn("<f>", sheet)
        self.assertIn("1 ticked several rows in one column", " ".join(strings))
        # The cells themselves: Hiring 10 + 5 = 15, Funding 5, and Training 0
        # (its only tick is a test entry); 2 completed responses.
        sheet = openpyxl.load_workbook(io.BytesIO(response.content)).active
        points = {
            row[1]: row[3]
            for row in sheet.iter_rows(values_only=True)
            if row[1] in ("Funding", "Hiring", "Training", "Marketing")
        }
        self.assertEqual(
            points, {"Hiring": 15, "Funding": 5, "Training": 0, "Marketing": 0}
        )
        self.assertIn("test entries left out: 2.", sheet.cell(2, 1).value)

    def test_a_closed_survey_still_exports(self):
        # "Close" archives the survey in Odoo 18.
        self._respond({self.funding: [self.p1]})
        self.survey.action_archive()
        self.env.flush_all()
        self.authenticate("bf_rank_manager", "bf_rank_manager")
        response = self.url_open(f"/bf_survey_priorities/trend/{self.survey.id}")
        self.assertEqual(response.status_code, 200)
        html, _kind = (
            self.env["ir.actions.report"]
            .with_user(self.manager)
            ._render_qweb_html(TREND_PDF, self.survey.ids)
        )
        self.assertIn("Funding", html.decode())

    def test_pdf_refused_on_a_restricted_survey(self):
        other = new_test_user(
            self.env,
            login="bf_rank_other2",
            groups="base.group_user,survey.group_survey_user",
        )
        officer = new_test_user(
            self.env,
            login="bf_rank_officer2",
            groups="base.group_user,survey.group_survey_user",
        )
        self.survey.write({"restrict_user_ids": [(6, 0, [other.id])]})
        self.env.flush_all()
        with self.assertRaises(AccessError):
            self.env["ir.actions.report"].with_user(officer)._render_qweb_html(
                TREND_PDF, self.survey.ids
            )

    def test_workbook_refused_without_access(self):
        self.authenticate("bf_rank_employee", "bf_rank_employee")
        response = self.url_open(f"/bf_survey_priorities/trend/{self.survey.id}")
        self.assertEqual(response.status_code, 404)
        response = self.url_open("/bf_survey_priorities/trend/999999999")
        self.assertEqual(response.status_code, 404)

    def test_workbook_refused_on_a_restricted_survey(self):
        other = new_test_user(
            self.env,
            login="bf_rank_other",
            groups="base.group_user,survey.group_survey_user",
        )
        self.survey.write({"restrict_user_ids": [(6, 0, [other.id])]})
        new_test_user(
            self.env,
            login="bf_rank_officer",
            groups="base.group_user,survey.group_survey_user",
        )
        self.env.flush_all()
        self.authenticate("bf_rank_officer", "bf_rank_officer")
        response = self.url_open(f"/bf_survey_priorities/trend/{self.survey.id}")
        self.assertEqual(response.status_code, 404)

    def test_pdf(self):
        self._respond({self.funding: [self.p1], self.hiring: [self.p1]})
        html, _kind = self.env["ir.actions.report"]._render_qweb_html(
            TREND_PDF, self.survey.ids
        )
        html = html.decode()
        self.assertIn("Weighted trend", html)
        self.assertIn("Your three most important needs", html)
        self.assertIn("Priority 1 = 10 pts", html)
        self.assertLess(html.index("Funding"), html.index("Training"))

    def test_pdf_refused_without_access(self):
        with self.assertRaises(AccessError):
            self.env["ir.actions.report"].with_user(self.employee)._render_qweb_html(
                TREND_PDF, self.survey.ids
            )

    def test_results_page_shows_the_trend(self):
        self._respond({self.hiring: [self.p1], self.funding: [self.p2]})
        self.env.flush_all()
        self.authenticate("bf_rank_manager", "bf_rank_manager")
        response = self.url_open(f"/survey/results/{self.survey.id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("o_bf_rank_rows", response.text)
        block = response.text[response.text.index("o_bf_rank_rows") :]
        self.assertLess(block.index("Hiring"), block.index("Funding"))
        # By default the page also counts responses in progress, and says so;
        # filtered on completed ones, it matches the exports and says nothing.
        self.assertIn("o_bf_rank_in_progress", response.text)
        response = self.url_open(f"/survey/results/{self.survey.id}?finished=true")
        self.assertIn("o_bf_rank_rows", response.text)
        self.assertNotIn("o_bf_rank_in_progress", response.text)


@tagged("post_install", "-at_install")
class TestPublicPage(PrioritiesCase, SurveyCase, HttpCase):
    """The public route, as a respondent's browser calls it."""

    def _start(self):
        self.survey.write({"access_mode": "public"})
        response = self._access_start(self.survey)
        self.assertEqual(response.status_code, 200)
        user_input = self.env["survey.user_input"].search(
            [("survey_id", "=", self.survey.id)], limit=1
        )
        response = self._access_page(self.survey, user_input.access_token)
        csrf_token = self._find_csrf_token(response.text)
        self._access_begin(self.survey, user_input.access_token)
        return user_input, csrf_token

    def _submit(self, user_input, csrf_token, ticks):
        response = self._access_submit(
            self.survey,
            user_input.access_token,
            {
                "page_id": self.page.id,
                "csrf_token": csrf_token,
                "token": user_input.access_token,
                str(self.question.id): {
                    str(row.id): [str(c.id) for c in columns]
                    for row, columns in ticks.items()
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        user_input.invalidate_recordset()
        return response.json()["result"][1]

    def test_the_matrix_carries_the_rule(self):
        self.question.bf_one_per_column = True
        user_input, _csrf = self._start()
        response = self._access_page(self.survey, user_input.access_token)
        self.assertIn('data-bf-one-per-column="1"', response.text)

    def test_the_rule_is_off_on_other_matrices(self):
        user_input, _csrf = self._start()
        response = self._access_page(self.survey, user_input.access_token)
        self.assertNotIn("data-bf-one-per-column", response.text)

    def test_submit_refuses_then_saves(self):
        self.question.bf_one_per_column = True
        user_input, csrf_token = self._start()
        result = self._submit(
            user_input, csrf_token, {self.funding: [self.p1], self.hiring: [self.p1]}
        )
        self.assertEqual(result.get("error"), "validation")
        self.assertIn(str(self.question.id), result["fields"])
        self.assertFalse(user_input.user_input_line_ids)

        result = self._submit(
            user_input, csrf_token, {self.funding: [self.p1], self.hiring: [self.p2]}
        )
        self.assertFalse(result.get("error"))
        self.assertEqual(len(user_input.user_input_line_ids), 2)
