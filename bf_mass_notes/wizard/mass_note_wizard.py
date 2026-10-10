import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import is_html_empty

_logger = logging.getLogger(__name__)


class MassNoteWizard(models.TransientModel):
    _name = "bf.mass.note.wizard"
    _description = "Add a note to several threads in bulk"

    model_name = fields.Char(string="Technical model", readonly=True)
    model_label = fields.Char(string="Model", readonly=True)
    record_count = fields.Integer(string="Selected records", readonly=True)
    body = fields.Html(string="Content", sanitize_style=True)
    post_type = fields.Selection(
        selection=[
            ("note", "Internal note (log)"),
            ("comment", "Message (notifies followers)"),
        ],
        string="Post type",
        default="note",
        required=True,
    )
    confirm_message = fields.Boolean(
        string="I confirm sending this as a message to the followers of "
               "each record",
    )

    @api.model
    def action_open_for(self, model_name, record_ids):
        """Window action of the wizard on a selection (list-view Action menu)."""
        return {
            "type": "ir.actions.act_window",
            "name": _("Add a note in bulk"),
            "res_model": self._name,
            "view_mode": "form",
            "target": "new",
            "context": dict(self.env.context, active_model=model_name, active_ids=record_ids),
        }

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context
        model = ctx.get("active_model")
        active_ids = ctx.get("active_ids") or []
        res["model_name"] = model
        res["record_count"] = len(active_ids)
        if model and model in self.env:
            res["model_label"] = self.env["ir.model"]._get(model).name
        return res

    def action_post(self):
        self.ensure_one()
        if is_html_empty(self.body):
            raise UserError(_("Please enter the content of the note."))

        model = self.env.context.get("active_model") or self.model_name
        active_ids = self.env.context.get("active_ids") or []
        if not model or not active_ids:
            raise UserError(_("No record selected."))

        if self.post_type == "comment" and not self.confirm_message:
            raise UserError(_(
                "Posting as a \"Message\" will notify the followers of %s "
                "record(s). Tick the confirmation box to continue."
            ) % len(active_ids))

        records = self.env[model].browse(active_ids).exists()
        if not records or not hasattr(records, "message_post"):
            raise UserError(_("This model has no discussion thread."))

        subtype = "mail.mt_note" if self.post_type == "note" else "mail.mt_comment"
        posted, failed = 0, 0
        for record in records:
            try:
                record.message_post(
                    body=self.body,
                    message_type="comment",
                    subtype_xmlid=subtype,
                )
                posted += 1
            except Exception as exc:  # noqa: BLE001 - one bad record must not abort the batch
                failed += 1
                _logger.warning(
                    "bf_mass_notes: post failed on %s,%s: %s", model, record.id, exc
                )

        message = _("%s note(s) posted.") % posted
        if failed:
            message += _(" %s failed.") % failed
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Notes in bulk"),
                "message": message,
                "type": "success" if not failed else "warning",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
