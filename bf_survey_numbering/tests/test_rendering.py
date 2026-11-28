"""What the respondent and the reader are served.

The number is put in by template inheritance: a renamed heading in the core
would silently drop it, and nothing in the log would say so. These tests read
the pages themselves.
"""
import re

from lxml import html

from odoo.tests import HttpCase, new_test_user, tagged

from .test_numbering import NumberingCase


def _text(node):
    return re.sub(r"\s+", " ", node.text_content()).strip()


@tagged("post_install", "-at_install")
class TestRendering(NumberingCase, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.survey.write({
            "access_mode": "public",
            "users_login_required": False,
            "questions_layout": "one_page",
        })
        cls.answer = cls.survey._create_answer()
        # A new answer is served the start screen; the questions come once begun.
        cls.answer.state = "in_progress"

    def headings(self, url, xpath):
        response = self.url_open(url)
        self.assertEqual(response.status_code, 200)
        return [_text(n) for n in html.fromstring(response.content).xpath(xpath)]

    def test_fill_form_numbers_sections_and_questions(self):
        url = f"/survey/{self.survey.access_token}/{self.answer.access_token}"
        sections = self.headings(url, "//h2[contains(@class, 'o_survey_title')]")
        self.assertEqual(sections, ["1. Your details", "2. Your company", "3. Consent"])
        questions = self.headings(url, "//div[contains(@class, 'js_question-wrapper')]//h3")
        self.assertIn("1.1 Company name", questions)
        self.assertIn("2.1.1 Who are they?", questions)

    def test_fill_form_one_page_per_section(self):
        self.survey.questions_layout = "page_per_section"
        answer = self.survey._create_answer()
        url = f"/survey/{self.survey.access_token}/{answer.access_token}"
        # The form serves the section that follows the last one displayed.
        answer.last_displayed_page_id = self.s1
        answer.state = "in_progress"
        sections = self.headings(url, "//h2[contains(@class, 'o_survey_title')]")
        self.assertEqual(sections, ["2. Your company"])

    def test_breadcrumb_numbers_the_sections(self):
        # The core shows the breadcrumb only on a survey without conditional
        # questions, laid out one page per section.
        self.q211.triggering_answer_ids = False
        self.survey.write({"questions_layout": "page_per_section", "users_can_go_back": True})
        answer = self.survey._create_answer()
        answer.last_displayed_page_id = self.s1
        answer.state = "in_progress"
        response = self.url_open(f"/survey/{self.survey.access_token}/{answer.access_token}")
        pages = html.fromstring(response.content).xpath("//*[@data-pages]/@data-pages")
        self.assertTrue(pages)
        import json
        self.assertEqual([p["title"] for p in json.loads(pages[0])],
                         ["1. Your details", "2. Your company", "3. Consent"])

    def test_print_page(self):
        url = (f"/survey/print/{self.survey.access_token}"
               f"?answer_token={self.answer.access_token}&review=1")
        sections = self.headings(url, "//div[contains(@class, 'o_page_header')]/h1")
        self.assertEqual(sections[:1], ["1. Your details"])
        questions = self.headings(url, "//div[contains(@class, 'js_question-wrapper')]/h2")
        self.assertIn("2.2 Founding year", questions)

    def test_results_page(self):
        new_test_user(self.env, login="bf_numbering_manager", password="bf_numbering_manager",
                      groups="base.group_user,survey.group_survey_manager")
        self.authenticate("bf_numbering_manager", "bf_numbering_manager")
        url = f"/survey/results/{self.survey.id}"
        sections = self.headings(url, "//div[contains(@class, 'o_survey_question_page')]/h3")
        self.assertEqual(sections, ["1. Your details", "2. Your company", "3. Consent"])
        questions = self.headings(url, "//div[contains(@class, 'o_survey_results_question_header')]//h5")
        self.assertIn("3.1 Signed by", questions)

    def test_unnumbered_survey_shows_bare_titles(self):
        self.survey.bf_numbering = False
        url = f"/survey/{self.survey.access_token}/{self.answer.access_token}"
        sections = self.headings(url, "//h2[contains(@class, 'o_survey_title')]")
        self.assertEqual(sections, ["Your details", "Your company", "Consent"])
        self.assertFalse(html.fromstring(self.url_open(url).content).xpath(
            "//*[contains(@class, 'o_bf_survey_number')]"))

    def test_editor_form_loads(self):
        views = self.env["survey.survey"].get_views([(False, "form")])
        arch = views["views"]["form"]["arch"]
        self.assertIn('name="bf_number"', arch)
        self.assertIn('limit="1000"', arch)
        self.assertIn('name="action_bf_move"', arch)

    def test_question_dialog_offers_the_move(self):
        views = self.env["survey.question"].get_views([(False, "form")])
        arch = html.fromstring(views["views"]["form"]["arch"])
        button = arch.xpath("//button[@name='action_bf_move']")
        self.assertEqual(len(button), 1)
        self.assertIn("bf_move_from_question", button[0].get("context"))
        self.assertTrue(arch.xpath("//field[@name='page_id' and @readonly='1']"))
