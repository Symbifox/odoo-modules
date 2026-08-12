import logging

import markupsafe

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SmsPostToTaskWizard(models.TransientModel):
    _name = "sms.archive.post.to.task.wizard"
    _description = "Poster un ou plusieurs SMS sur une tâche"

    message_ids = fields.Many2many(
        comodel_name="sms.archive.message",
        relation="sms_post_to_task_wizard_message_rel",
        column1="wizard_id",
        column2="message_id",
        string="Messages",
        required=True,
    )
    message_count = fields.Integer(
        string="Nombre de messages",
        compute="_compute_message_count",
    )
    preview = fields.Html(
        string="Aperçu",
        compute="_compute_preview",
        readonly=True,
        sanitize=False,
    )
    project_id = fields.Many2one(
        comodel_name="project.project",
        string="Projet",
        required=True,
    )
    task_id = fields.Many2one(
        comodel_name="project.task",
        string="Tâche",
        required=True,
        domain="[('project_id', '=', project_id)]",
    )
    link_threads = fields.Boolean(
        string="Lier aussi la(les) conversation(s) à cette tâche",
        default=True,
    )

    @api.depends("message_ids")
    def _compute_message_count(self):
        for wiz in self:
            wiz.message_count = len(wiz.message_ids)

    @api.depends("message_ids")
    def _compute_preview(self):
        for wiz in self:
            wiz.preview = wiz._render_post_body(preview=True)

    def _render_post_body(self, preview=False):
        """Render the HTML body posted to the task chatter (or the preview)."""
        self.ensure_one()
        messages = self.message_ids.sorted("date_sent")
        if not messages:
            return ""

        threads = messages.thread_id
        if len(threads) == 1:
            t = threads
            contact = markupsafe.escape(t.contact_name or t.phone_normalized or "")
            header = (
                f"<p><strong>{len(messages)} SMS</strong> — {contact}</p>"
            )
        else:
            header = (
                f"<p><strong>{len(messages)} SMS</strong> "
                f"sur {len(threads)} conversations</p>"
            )

        rows = []
        prev_thread_id = None
        for msg in messages:
            if len(threads) > 1 and msg.thread_id.id != prev_thread_id:
                t = msg.thread_id
                contact = markupsafe.escape(t.contact_name or t.phone_normalized or "")
                rows.append(
                    f"<p style='margin:8px 0 2px 0'><strong>— {contact}</strong></p>"
                )
                prev_thread_id = msg.thread_id.id
            direction = "Reçu" if msg.direction == "in" else (
                "Envoyé" if msg.direction == "out" else "Brouillon"
            )
            arrow_color = "#0a7d3a" if msg.direction == "in" else (
                "#0f4960" if msg.direction == "out" else "#999"
            )
            date_str = markupsafe.escape(str(msg.date_sent or ""))
            body_escaped = markupsafe.escape(msg.body or "")
            rows.append(
                f"<p style='margin:2px 0'>"
                f"<span style='color:{arrow_color};font-weight:600'>{direction}</span> "
                f"<span style='color:#666;font-size:0.9em'>{date_str}</span><br/>"
                f"{body_escaped}"
                f"</p>"
            )
        return header + "".join(rows)

    def action_post(self):
        """Post the selected SMS messages to the chosen task's chatter as one consolidated note."""
        self.ensure_one()
        if not self.message_ids:
            raise UserError("Aucun message sélectionné.")
        if not self.task_id:
            raise UserError("Veuillez sélectionner une tâche.")

        body_html = self._render_post_body()
        self.task_id.message_post(
            body=body_html,
            message_type="comment",
            subtype_xmlid="mail.mt_note",
            body_is_html=True,
        )

        if self.link_threads:
            threads = self.message_ids.thread_id
            for thread in threads:
                if self.task_id.id not in thread.task_ids.ids:
                    thread.write({"task_ids": [(4, self.task_id.id, 0)]})

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "SMS posté",
                "message": (
                    f"{len(self.message_ids)} message(s) posté(s) sur "
                    f"« {self.task_id.name} » ({self.project_id.name})"
                ),
                "type": "success",
            },
        }
