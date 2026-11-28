from odoo import api, fields, models
from odoo.exceptions import ValidationError


class SurveyQuestionAnswer(models.Model):
    _inherit = "survey.question.answer"

    # On a column of a matrix (question_id set).
    bf_rank_points = fields.Float(
        string="Points",
        digits=(16, 2),
        default=0.0,
        help="What a tick in this column is worth in the weighted trend of the "
        "matrix. When a respondent ticks several rows in this column, the "
        "points are shared between them.",
    )
    # On a row of a matrix (matrix_question_id set).
    bf_rank_group = fields.Char(
        string="Group",
        help="Optional. Rows that share a group are also added up together "
        "in the weighted trend (a skill, a theme, a department).",
    )

    @api.constrains("matrix_question_id", "question_id")
    def _check_bf_one_per_column_parent(self):
        # A row or a column added on its own (an import, a script) changes
        # the counts the question's own constraint relies on.
        (self.matrix_question_id | self.question_id)._check_bf_one_per_column()

    def unlink(self):
        questions = self.matrix_question_id | self.question_id
        result = super().unlink()
        questions.exists()._check_bf_one_per_column()
        return result

    @api.constrains("bf_rank_points")
    def _check_bf_rank_points(self):
        for answer in self:
            if answer.bf_rank_points < 0:
                raise ValidationError(
                    self.env._("The points of a column cannot be negative.")
                )
