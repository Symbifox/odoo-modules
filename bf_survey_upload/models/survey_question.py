from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SurveyQuestion(models.Model):
    _inherit = "survey.question"

    question_type = fields.Selection(
        selection_add=[("file_upload", "Téléversement de fichiers")],
        # On uninstall, downgrade existing file_upload questions to text_box
        # so the survey remains valid (admin can decide what to do next).
        ondelete={"file_upload": lambda recs: recs.write({"question_type": "text_box"})},
    )

    file_upload_max_size_mb = fields.Integer(
        string="Taille max par fichier (Mo)",
        default=25,
    )
    file_upload_allowed_extensions = fields.Char(
        string="Extensions autorisées",
        default="pdf,docx,doc,xlsx,jpg,jpeg,png",
        help="Liste séparée par des virgules, sans le point. Vide = toutes extensions.",
    )
    file_upload_multiple = fields.Boolean(
        string="Plusieurs fichiers autorisés",
        default=True,
    )
    max_file_count = fields.Integer(
        string="Nombre max de fichiers",
        default=0,
        help="0 = illimité. Nombre maximal de fichiers que le répondant peut "
             "téléverser pour cette question (s'applique seulement lorsque "
             "plusieurs fichiers sont autorisés).",
    )

    @api.constrains("file_upload_max_size_mb")
    def _check_file_upload_max_size_mb(self):
        for q in self:
            if q.question_type == "file_upload" and q.file_upload_max_size_mb <= 0:
                raise ValidationError(
                    _("La taille maximale par fichier doit être supérieure à 0 Mo.")
                )

    @api.constrains("max_file_count")
    def _check_max_file_count(self):
        for q in self:
            if q.question_type == "file_upload" and q.max_file_count < 0:
                raise ValidationError(
                    _("Le nombre maximal de fichiers ne peut pas être négatif "
                      "(0 = illimité).")
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
            return {self.id: self.constr_error_msg or _("Cette question est obligatoire.")}
        if not self.file_upload_multiple and len(attachment_ids) > 1:
            return {self.id: _("Un seul fichier est autorisé pour cette question.")}
        if (
            self.file_upload_multiple
            and self.max_file_count > 0
            and len(attachment_ids) > self.max_file_count
        ):
            return {
                self.id: _(
                    "Vous pouvez téléverser au maximum %(n)s fichier(s) "
                    "pour cette question.",
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
