from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SurveyQuestion(models.Model):
    _inherit = "survey.question"

    question_type = fields.Selection(
        selection_add=[("file_upload", "File upload")],
        # On uninstall, downgrade existing file_upload questions to text_box
        # so the survey remains valid (admin can decide what to do next).
        ondelete={"file_upload": lambda recs: recs.write({"question_type": "text_box"})},
    )

    file_upload_max_size_mb = fields.Integer(
        string="Max size per file (MB)",
        default=25,
    )
    file_upload_allowed_extensions = fields.Char(
        string="Allowed extensions",
        default="pdf,docx,doc,xlsx,jpg,jpeg,png",
        help="Comma-separated list, without the dot. Empty = all extensions.",
    )
    file_upload_multiple = fields.Boolean(
        string="Multiple files allowed",
        default=True,
    )
    max_file_count = fields.Integer(
        string="Max number of files",
        default=0,
        help="0 = unlimited. Maximum number of files the respondent can "
             "upload for this question (applies only when several files "
             "are allowed).",
    )

    @api.constrains("file_upload_max_size_mb")
    def _check_file_upload_max_size_mb(self):
        for q in self:
            if q.question_type == "file_upload" and q.file_upload_max_size_mb <= 0:
                raise ValidationError(
                    _("The maximum size per file must be greater than 0 MB.")
                )

    @api.constrains("max_file_count")
    def _check_max_file_count(self):
        for q in self:
            if q.question_type == "file_upload" and q.max_file_count < 0:
                raise ValidationError(
                    _("The maximum number of files cannot be negative (0 "
                      "= unlimited).")
                )

    def _get_allowed_extensions_list(self):
        self.ensure_one()
        if not self.file_upload_allowed_extensions:
            return []
        return [
            ext.strip().lower().lstrip(".")
            for ext in self.file_upload_allowed_extensions.split(",")
            if ext.strip()
        ]

    def validate_question(self, answer, comment=None):
        self.ensure_one()
        if self.question_type == "file_upload":
            return self._validate_file_upload(answer)
        return super().validate_question(answer, comment)

    def _validate_file_upload(self, answer):
        # answer here is expected to be a list of ir.attachment ids (ints)
        attachment_ids = self._coerce_attachment_ids(answer)
        if self.constr_mandatory and not attachment_ids:
            return {self.id: self.constr_error_msg or _("This question is "
                                                        "required.")}
        if not self.file_upload_multiple and len(attachment_ids) > 1:
            return {self.id: _("Only one file is allowed for this question.")}
        if (
            self.file_upload_multiple
            and self.max_file_count > 0
            and len(attachment_ids) > self.max_file_count
        ):
            return {
                self.id: _(
                    "You can upload at most %(n)s file(s) for this question.",
                    n=self.max_file_count,
                )
            }
        return {}

    @staticmethod
    def _coerce_attachment_ids(value):
        if not value:
            return []
        if isinstance(value, (list, tuple)):
            out = []
            for v in value:
                try:
                    out.append(int(v))
                except (TypeError, ValueError):
                    continue
            return out
        try:
            return [int(value)]
        except (TypeError, ValueError):
            return []
