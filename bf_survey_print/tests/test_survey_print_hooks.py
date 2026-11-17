# -*- coding: utf-8 -*-
"""This module is a stylesheet and one fixed condition: it has no model of its
own. What could break it is silent — a renamed class, a moved separator, an
asset path that no longer resolves — and nothing in the log would say so. A
printed questionnaire would simply start cutting questions in two again.

So these tests read what a browser is served: the page, and the stylesheet the
page links to.
"""
from lxml import html

from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSurveyPrintHooks(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="survey_print_user", password="survey_print_user",
            name="Survey print", groups="base.group_user,survey.group_survey_user",
        )
        # Three sections on purpose: one undescribed (the core hides those on a
        # blank page), one described, and one holding nothing but a question
        # that a filled page will not ask.
        cls.survey = cls.env["survey.survey"].create({
            "title": "Three sections, four questions",
            "questions_layout": "page_per_section",
            "access_mode": "public",
            "users_login_required": False,
            "question_and_page_ids": [
                (0, 0, {"title": "First section", "is_page": True, "sequence": 1}),
                (0, 0, {"title": "A free text question", "question_type": "char_box",
                        "sequence": 2}),
                (0, 0, {"title": "Second section", "is_page": True, "sequence": 3,
                        "description": "<p>This one carries a description.</p>"}),
                (0, 0, {"title": "A single choice question", "question_type": "simple_choice",
                        "sequence": 4,
                        "suggested_answer_ids": [(0, 0, {"value": "Yes"}), (0, 0, {"value": "No"})]}),
                (0, 0, {"title": "A matrix question", "question_type": "matrix", "sequence": 5,
                        "matrix_row_ids": [(0, 0, {"value": "First row"})],
                        "suggested_answer_ids": [(0, 0, {"value": "A column"})]}),
                (0, 0, {"title": "Third section", "is_page": True, "sequence": 6}),
            ],
        })
        cls.choice = cls.survey.question_ids.filtered(
            lambda q: q.question_type == "simple_choice")
        cls.conditional = cls.env["survey.question"].create({
            "survey_id": cls.survey.id,
            "title": "A question asked only of those who said No",
            "question_type": "char_box",
            "sequence": 7,
            # Odoo 18 carries conditionality on the triggering ANSWERS alone:
            # `triggering_question_ids` is computed from them, and there is no
            # `is_conditional` field any more.
            "triggering_answer_ids": [
                cls.choice.suggested_answer_ids.filtered(lambda a: a.value == "No").id],
        })

    def _page(self, url, authenticated=True):
        if authenticated:
            self.authenticate("survey_print_user", "survey_print_user")
        page = self.url_open(url)
        self.assertEqual(page.status_code, 200)
        return html.fromstring(page.content)

    def _blank_page(self, **kw):
        return self._page("/survey/print/%s" % self.survey.access_token, **kw)

    # -- the hooks the stylesheet rides on ---------------------------------

    def test_separator_is_a_direct_child_of_the_fieldset(self):
        """`fieldset > hr` carries the forced break, one per section but the
        first. The stylesheet would break nothing if it moved or multiplied."""
        doc = self._blank_page()
        fieldsets = doc.xpath("//div[contains(@class, 'o_survey_print')]//fieldset")
        self.assertEqual(len(fieldsets), 1, "the printable page is built around one fieldset")
        self.assertEqual(len(fieldsets[0].xpath("./hr")), 2,
                         "three sections, so two separators — and they are direct children")
        self.assertEqual(len(doc.xpath("//div[contains(@class, 'o_survey_print')]//hr")), 2,
                         "no other rule on the page, which would break a sheet for nothing")

    def test_every_question_is_wrapped(self):
        """`.js_question-wrapper` is the box kept in one piece."""
        doc = self._blank_page()
        self.assertEqual(len(doc.xpath("//div[contains(@class, 'js_question-wrapper')]")), 4,
                         "one wrapper per question, sections excluded")

    def test_section_headers_are_marked(self):
        """`.o_page_header` is the box that must not end a sheet alone — here
        for a described section and for two undescribed ones."""
        doc = self._blank_page()
        headers = doc.xpath("//div[contains(@class, 'o_page_header')]")
        self.assertEqual(len(headers), 3, "one header per section")
        titles = [h.xpath("normalize-space(.//h1)") for h in headers]
        self.assertEqual(titles, ["First section", "Second section", "Third section"])

    def test_matrix_carries_a_header_row_group(self):
        """The repeated header row of a long matrix needs a `thead`."""
        doc = self._blank_page()
        matrices = doc.xpath("//table[contains(@class, 'o_survey_question_matrix')]")
        self.assertEqual(len(matrices), 1, "the matrix question renders a matrix table")
        self.assertTrue(matrices[0].xpath("./thead/tr"),
                        "the matrix header row sits in a thead, where print can repeat it")

    def test_a_respondent_who_is_not_logged_in_gets_the_same_page(self):
        """The review link of a respondent goes through the same template, and
        that reader is anonymous."""
        doc = self._blank_page(authenticated=False)
        self.assertEqual(len(doc.xpath("//div[contains(@class, 'js_question-wrapper')]")), 4)
        self.assertEqual(len(doc.xpath("//div[contains(@class, 'o_page_header')]")), 3)

    # -- the stylesheet actually reaches that page -------------------------

    def test_the_print_rules_are_served_with_the_page(self):
        """Without this, the module could lose its stylesheet — a typo in the
        asset path, a renamed bundle — and every test above would stay green
        while the printout went back to cutting questions in two."""
        doc = self._blank_page()
        hrefs = doc.xpath("//link[@rel='stylesheet']/@href")
        bundles = [h for h in hrefs if "survey.survey_assets" in h]
        self.assertTrue(bundles, "the page links the survey asset bundle: %s" % hrefs)
        css = self.url_open(bundles[0])
        self.assertEqual(css.status_code, 200)
        served = css.content.decode()
        for regle in ("break-before: page", "break-inside: avoid",
                      "display: table-header-group", "max-width: none",
                      "margin: auto 14mm"):
            self.assertIn(regle, served, "the served bundle carries %r" % regle)
        self.assertIn("o_survey_print", served)

    # -- the condition added to the core template --------------------------

    def test_a_section_without_a_description_is_printed(self):
        """On a blank page the core shows a section only if it is described
        (`questions_to_display` is empty without an answer), so an undescribed
        one printed neither its title nor anything to break on."""
        undescribed = self.survey.question_and_page_ids.filtered(
            lambda q: q.is_page and not q.description)
        self.assertEqual(len(undescribed), 2, "the fixture keeps two undescribed sections")
        doc = self._blank_page()
        printed = [h.xpath("normalize-space(.//h1)")
                   for h in doc.xpath("//div[contains(@class, 'o_page_header')]")]
        for section in undescribed:
            self.assertIn(section.title, printed)

    def test_a_filled_page_only_shows_the_sections_that_were_asked(self):
        """The clause added to the core condition is gated on `not answer`:
        without that gate, a section whose only question was never asked would
        print, and break a sheet, for nothing."""
        answer = self.survey._create_answer(partner=self.user.partner_id)
        for question in self.survey.question_ids:
            if question == self.conditional:
                continue
            if question.question_type == "char_box":
                answer._save_lines(question, "Something")
            elif question.question_type == "simple_choice":
                answer._save_lines(question, str(
                    question.suggested_answer_ids.filtered(lambda a: a.value == "Yes").id))
            elif question.question_type == "matrix":
                answer._save_lines(question, {
                    str(question.matrix_row_ids[0].id): [str(question.suggested_answer_ids[0].id)],
                })
        answer._mark_done()
        self.assertNotIn(self.conditional, answer._get_print_questions(),
                         "the conditional question was not triggered, so it is not printed")

        doc = self._page("/survey/print/%s?answer_token=%s&review=True" % (
            self.survey.access_token, answer.access_token))
        headers = [h.xpath("normalize-space(.//h1)")
                   for h in doc.xpath("//div[contains(@class, 'o_page_header')]")]
        self.assertEqual(headers, ["First section", "Second section"],
                         "the third section held only the question that was not asked")
        self.assertEqual(len(doc.xpath("//div[contains(@class, 'js_question-wrapper')]")), 3)
        self.assertEqual(
            len(doc.xpath("//div[contains(@class, 'o_survey_print')]//fieldset/hr")), 1)
