from odoo import api, fields, models


class SurveyUserInputLine(models.Model):
    _inherit = "survey.user_input.line"

    # Extend the native answer_type selection so a saved file_upload line
    # satisfies the native _check_answer_type_skipped constraint, which
    # requires a non-falsy answer_type when skipped is False.
    answer_type = fields.Selection(
        selection_add=[("file_upload", "Téléversement de fichiers")],
        ondelete={"file_upload": "set null"},
    )

    bf_attachment_ids = fields.Many2many(
        "ir.attachment",
        "bf_survey_input_line_attachment_rel",
        "line_id",
        "attachment_id",
        string="Fichiers téléversés",
    )

    # Native _check_answer_type_skipped reads `line['value_<answer_type>']` to
    # confirm the answer is non-empty. We expose value_file_upload as a
    # computed boolean derived from the M2M presence so the check passes.
    value_file_upload = fields.Boolean(
        string="A des fichiers téléversés",
        compute="_compute_value_file_upload",
        store=False,
    )

    @api.depends("bf_attachment_ids")
    def _compute_value_file_upload(self):
        for line in self:
            line.value_file_upload = bool(line.bf_attachment_ids)
