"""The copy filed on the project is described for the project's team.

The respondent often submits from the public survey page, in their own
language. The description written on the project's copy is read by the team:
in the project manager's language, else in the company's.
"""
import base64

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestUploadDescriptionLanguage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["res.lang"]._activate_lang("en_US")
        cls.env.company.partner_id.lang = "fr_CA"
        cls.english_manager = cls.env["res.users"].create({
            "name": "Manager en_US", "login": "manager.survey@example.test",
            "lang": "en_US",
            "groups_id": [(6, 0, [cls.env.ref("project.group_project_manager").id])],
        })
        cls.question = cls.env["survey.question"].create({
            "title": "Your file", "question_type": "file_upload",
            "survey_id": cls.env["survey.survey"].create({"title": "Intake"}).id,
        })

    def _submit(self, project):
        survey = self.question.survey_id
        survey.write({"bf_link_uploads_to_project": True, "bf_target_project_id": project.id})
        respondent = self.env["res.partner"].create({"name": "Respondent", "lang": "en_US"})
        user_input = self.env["survey.user_input"].create({
            "survey_id": survey.id, "partner_id": respondent.id,
        })
        attachment = self.env["ir.attachment"].create({
            "name": "proof.txt", "datas": base64.b64encode(b"proof %d" % project.id),
            "res_model": "survey.user_input", "res_id": user_input.id,
        })
        self.env["survey.user_input.line"].create({
            "user_input_id": user_input.id, "question_id": self.question.id,
            "answer_type": "file_upload", "skipped": False,
            "bf_attachment_ids": [(6, 0, attachment.ids)],
        })
        # The respondent's context: the public page, in English.
        user_input.with_context(lang="en_US")._bf_link_uploads_to_project()
        copy = self.env["ir.attachment"].search([
            ("res_model", "=", "project.project"), ("res_id", "=", project.id),
        ])
        self.assertEqual(len(copy), 1)
        return copy.description

    def test_without_a_manager_in_the_company_language(self):
        project = self.env["project.project"].create({"name": "Intake FR", "user_id": False})
        description = self._submit(project)
        self.assertIn("Téléversé via le sondage", description)
        self.assertIn("Respondent", description)

    def test_in_the_project_manager_language(self):
        project = self.env["project.project"].create({
            "name": "Intake EN", "user_id": self.english_manager.id,
        })
        self.assertIn("Uploaded through the", self._submit(project))
