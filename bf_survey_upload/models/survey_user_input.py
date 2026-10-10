import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)


class SurveyUserInput(models.Model):
    _inherit = "survey.user_input"

    bf_attachment_ids = fields.Many2many(
        "ir.attachment",
        string="Uploaded files",
        compute="_compute_bf_attachment_ids",
    )
    bf_attachment_count = fields.Integer(
        string="Number of files",
        compute="_compute_bf_attachment_ids",
    )

    @api.depends("user_input_line_ids.bf_attachment_ids")
    def _compute_bf_attachment_ids(self):
        for ui in self:
            atts = ui.user_input_line_ids.mapped("bf_attachment_ids")
            ui.bf_attachment_ids = atts
            ui.bf_attachment_count = len(atts)

    def _save_lines(self, question, answer, comment=None, overwrite_existing=True):
        if question.question_type == "file_upload":
            return self._bf_save_file_upload(question, answer, overwrite_existing)
        return super()._save_lines(question, answer, comment, overwrite_existing)

    def _bf_save_file_upload(self, question, answer, overwrite_existing):
        self.ensure_one()
        candidate_ids = question._coerce_attachment_ids(answer)

        # Only accept attachments that were uploaded against this very user_input.
        # Prevents a respondent from claiming an arbitrary ir.attachment id.
        attachment_ids = []
        if candidate_ids:
            attachment_ids = (
                self.env["ir.attachment"]
                .sudo()
                .search(
                    [
                        ("id", "in", candidate_ids),
                        ("res_model", "=", "survey.user_input"),
                        ("res_id", "=", self.id),
                    ]
                )
                .ids
            )

        existing = self.env["survey.user_input.line"].search(
            [("user_input_id", "=", self.id), ("question_id", "=", question.id)]
        )
        if existing and not overwrite_existing:
            raise UserError(_("This answer cannot be overwritten."))
        if existing:
            existing.unlink()

        skipped = not attachment_ids
        # answer_type must be falsy when skipped, non-falsy when answered, to
        # satisfy native _check_answer_type_skipped. We extend the selection
        # with 'file_upload' on survey.user_input.line so that's a valid value.
        line_vals = {
            "user_input_id": self.id,
            "question_id": question.id,
            "survey_id": self.survey_id.id,
            "skipped": skipped,
            "answer_type": False if skipped else "file_upload",
            "bf_attachment_ids": [(6, 0, attachment_ids)],
        }
        return self.env["survey.user_input.line"].create(line_vals)

    def _mark_done(self):
        res = super()._mark_done()
        for ui in self:
            try:
                ui._bf_link_uploads_to_project()
            except (UserError, ValidationError, AccessError) as e:
                _logger.warning(
                    "bf_survey_upload: skipped project link for user_input %s: %s",
                    ui.id,
                    e,
                )
        return res

    def _bf_link_uploads_to_project(self):
        self.ensure_one()
        survey = self.survey_id
        if not survey.bf_link_uploads_to_project or not survey.bf_target_project_id:
            return
        project = survey.bf_target_project_id.sudo()
        if not project.active:
            return
        attachments = self.user_input_line_ids.mapped("bf_attachment_ids")
        if not attachments:
            return

        # Lock this user_input row to serialize concurrent _mark_done calls
        # (e.g. respondent double-submit, network retry). Without it, two
        # transactions could each read existing_checksums concurrently and
        # both create copies before either commits.
        self.env.cr.execute(
            "SELECT id FROM survey_user_input WHERE id = %s FOR UPDATE",
            (self.id,),
        )

        # Idempotency: skip attachments already copied to this project (by checksum).
        # Filter out falsy checksums so an attachment without datas (e.g. url type)
        # doesn't poison the set and silently skip distinct uploads.
        existing_checksums = {
            c
            for c in self.env["ir.attachment"]
            .sudo()
            .search(
                [
                    ("res_model", "=", "project.project"),
                    ("res_id", "=", project.id),
                ]
            )
            .mapped("checksum")
            if c
        }

        # The copy is described for the project's team, not for the respondent
        # who submits (often from the public survey page, in their own language).
        team = self.with_context(lang=self._bf_project_reader_lang(project))
        partner_label = (self.partner_id.display_name or "").strip() or team.env._("(anonymous)")
        description = team.env._(
            "Uploaded through the \"%(survey)s\" survey by %(who)s",
            survey=survey.with_context(lang=team.env.lang).title,
            who=partner_label,
        )
        for att in attachments:
            if att.checksum and att.checksum in existing_checksums:
                continue
            att.sudo().copy(
                {
                    "res_model": "project.project",
                    "res_id": project.id,
                    "description": description,
                }
            )

    def _bf_project_reader_lang(self, project):
        """Language of the project's team: its manager's, else the company's.

        None leaves the context without a language: the text is then written in
        the source language (English).
        """
        installed = {code for code, _name in self.env["res.lang"].get_installed()}
        manager = project.user_id
        langs = [manager.lang] if manager and manager.active and not manager.share else []
        langs.append((project.company_id or self.env.company).partner_id.lang)
        return next((lang for lang in langs if lang in installed), None)
