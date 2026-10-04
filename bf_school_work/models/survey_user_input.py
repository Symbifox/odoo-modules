from odoo import models


class SurveyUserInput(models.Model):
    _inherit = "survey.user_input"

    def _mark_done(self):
        res = super()._mark_done()
        Submission = self.env["bf.school.homework.submission"].sudo()
        for answer in self:
            # A "retry" opened while the school's answer was in progress shares its invitation
            # (one attempt by invitation, set by the homework): the first one finished counts,
            # so a practice run is the hand-in, not a free look at the answers.
            domain = [("survey_answer_id", "=", answer.id)]
            if answer.invite_token:
                domain = ["|", ("survey_answer_id", "=", answer.id),
                          ("survey_answer_id.invite_token", "=", answer.invite_token)]
            for submission in Submission.search(domain):
                if submission.state != "todo":
                    continue
                if submission.survey_answer_id != answer:
                    submission.survey_answer_id = answer
                submission._school_quiz_done(answer)
        return res
