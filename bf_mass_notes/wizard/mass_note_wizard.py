import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import is_html_empty

_logger = logging.getLogger(__name__)


class MassNoteWizard(models.TransientModel):
    _name = "bf.mass.note.wizard"
    _description = "Ajouter une note à plusieurs fils en lot"

    model_name = fields.Char(string="Modèle technique", readonly=True)
    model_label = fields.Char(string="Modèle", readonly=True)
    record_count = fields.Integer(string="Enregistrements sélectionnés", readonly=True)
    body = fields.Html(string="Contenu", sanitize_style=True)
    post_type = fields.Selection(
        selection=[
            ("note", "Note interne (journal)"),
            ("comment", "Message (notifie les abonnés)"),
        ],
        string="Type de publication",
        default="note",
        required=True,
    )
    confirm_message = fields.Boolean(
        string="Je confirme l'envoi en tant que message aux abonnés de chaque enregistrement",
    )

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
            raise UserError(_("Veuillez saisir le contenu de la note."))

        model = self.env.context.get("active_model") or self.model_name
        active_ids = self.env.context.get("active_ids") or []
        if not model or not active_ids:
            raise UserError(_("Aucun enregistrement sélectionné."))

        if self.post_type == "comment" and not self.confirm_message:
            raise UserError(_(
                "Publier en tant que « Message » notifiera les abonnés de %s "
                "enregistrement(s). Cochez la case de confirmation pour continuer."
            ) % len(active_ids))

        records = self.env[model].browse(active_ids).exists()
        if not records or not hasattr(records, "message_post"):
            raise UserError(_("Ce modèle ne gère pas les fils de discussion."))

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

        message = _("%s note(s) publiée(s).") % posted
        if failed:
            message += _(" %s échec(s).") % failed
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Notes en lot"),
                "message": message,
                "type": "success" if not failed else "warning",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
