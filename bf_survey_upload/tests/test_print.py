# -*- coding: utf-8 -*-
"""A file upload question on the printable page.

The question type was injected into the form's question container only, never
into `survey.survey_page_print`: printing a survey showed the question's title
and nothing under it, and the printed copy of a filled answer did not name a
single one of the files the respondent had sent.
"""
import base64

from lxml import html

from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestFileUploadOnThePrintedPage(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="upload_print_user", password="upload_print_user",
            name="Upload print", groups="base.group_user,survey.group_survey_user",
        )
        cls.survey = cls.env["survey.survey"].create({
            "title": "One upload question",
            "access_mode": "public",
            "users_login_required": False,
        })
        cls.question = cls.env["survey.question"].create({
            "survey_id": cls.survey.id,
            "title": "Your organisation chart",
            "question_type": "file_upload",
            "file_upload_allowed_extensions": "pdf,docx",
            "file_upload_max_size_mb": 7,
        })

    def _printed_question(self, url):
        self.authenticate("upload_print_user", "upload_print_user")
        page = self.url_open(url)
        self.assertEqual(page.status_code, 200)
        doc = html.fromstring(page.content)
        wrappers = doc.xpath("//div[contains(@class, 'js_question-wrapper')]")
        self.assertEqual(len(wrappers), 1, "the upload question is on the printed page")
        return wrappers[0]

    def test_a_blank_sheet_says_what_may_be_attached(self):
        wrapper = self._printed_question("/survey/print/%s" % self.survey.access_token)
        text = " ".join(wrapper.text_content().split())
        self.assertIn("Your organisation chart", text)
        self.assertTrue(wrapper.xpath(".//*[contains(@class, 'o_bf_upload_print_hint')]"))
        self.assertIn("pdf,docx", text, "the accepted formats are printed")
        self.assertIn("7", text, "the size limit is printed")
        self.assertFalse(wrapper.xpath(".//input[@type='file']"),
                         "a file picker has no meaning on paper")
        self.assertFalse(wrapper.xpath(".//*[contains(@class, 'o_survey_form_file_upload_dropzone')]"),
                         "neither has a dropzone")

    def test_a_filled_sheet_names_the_files(self):
        answer = self.survey._create_answer(partner=self.user.partner_id)
        attachment = self.env["ir.attachment"].create({
            "name": "organisation-chart.pdf",
            "datas": base64.b64encode(b"%PDF-1.4 chart"),
            "res_model": "survey.user_input",
            "res_id": answer.id,
        })
        self.env["survey.user_input.line"].create({
            "user_input_id": answer.id, "question_id": self.question.id,
            "answer_type": "file_upload", "skipped": False,
            "bf_attachment_ids": [(6, 0, attachment.ids)],
        })
        answer._mark_done()

        wrapper = self._printed_question("/survey/print/%s?answer_token=%s&review=True" % (
            self.survey.access_token, answer.access_token))
        text = " ".join(wrapper.text_content().split())
        self.assertIn("organisation-chart.pdf", text,
                      "the file the respondent sent is named on the printout")
        self.assertTrue(wrapper.xpath(".//*[contains(@class, 'o_bf_upload_print_files')]"))
        self.assertFalse(wrapper.xpath(".//*[contains(@class, 'o_bf_upload_print_hint')]"),
                         "the instructions belong to the blank sheet only")

    def test_a_skipped_question_says_so(self):
        answer = self.survey._create_answer(partner=self.user.partner_id)
        self.env["survey.user_input.line"].create({
            "user_input_id": answer.id, "question_id": self.question.id,
            "answer_type": False, "skipped": True,
        })
        answer._mark_done()

        wrapper = self._printed_question("/survey/print/%s?answer_token=%s&review=True" % (
            self.survey.access_token, answer.access_token))
        # On a French database the page reads "Cette question a ete ignoree":
        # the marker to check is the class, not the wording.
        self.assertTrue(
            wrapper.xpath(".//*[contains(@class, 'o_survey_choice_question_skipped')]"),
            "the printed question says it was skipped, in the reader's language")
        self.assertFalse(wrapper.xpath(".//*[contains(@class, 'o_bf_upload_print_none')]"),
                         "a skipped question is not an answer with no file")
        self.assertFalse(wrapper.xpath(".//*[contains(@class, 'o_bf_upload_print_hint')]"),
                         "the instructions belong to the blank sheet only")
