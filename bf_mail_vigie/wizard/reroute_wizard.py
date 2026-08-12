import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_TARGET_MODELS = [
    ("project.task", "T\u00e2che"),
    ("crm.lead", "Piste / Opportunit\u00e9 CRM"),
    ("helpdesk.ticket", "Ticket helpdesk"),
    ("res.partner", "Contact"),
    ("sale.order", "Bon de commande"),
    ("account.move", "Facture"),
    ("hr.expense", "Note de frais"),
    ("hosting.service", "Service h\u00e9bergement"),
    ("hosting.domain", "Domaine h\u00e9bergement"),
    ("project.project", "Projet"),
    ("calendar.event", "\u00c9v\u00e9nement"),
    ("mailing.contact", "Contact mailing"),
]


class BfMailRerouteWizard(models.TransientModel):
    _name = "bf.mail.reroute.wizard"
    _description = "Re-router un courriel vers une autre chatter"

    email_id = fields.Many2one(
        "bf.email", string="Courriel", required=True, readonly=True, ondelete="cascade"
    )
    current_display = fields.Char("Chatter actuelle", readonly=True)
    suggested_model = fields.Char("Mod\u00e8le sugg\u00e9r\u00e9", readonly=True)
    suggested_res_id = fields.Integer("ID sugg\u00e9r\u00e9", readonly=True)
    suggested_display = fields.Char("Cible sugg\u00e9r\u00e9e", readonly=True)
    suggested_reason = fields.Char("Raison", readonly=True)

    target_ref = fields.Reference(
        selection=_TARGET_MODELS,
        string="Cible",
        required=True,
    )

    def _get_source_record(self):
        self.ensure_one()
        email = self.email_id
        if not email.res_model or not email.res_id:
            return None
        if email.res_model not in self.env:
            return None
        source = self.env[email.res_model].browse(email.res_id)
        return source if source.exists() else None

    def _validate_access(self, target):
        self.ensure_one()
        target.check_access("write")
        email = self.email_id
        email.check_access("write")
        source = self._get_source_record()
        if source is not None:
            source.check_access("write")
        if email.mail_message_id:
            email.mail_message_id.check_access("read")

    def _validate_target(self):
        self.ensure_one()
        target = self.target_ref
        if not target:
            raise UserError(_("Choisissez une cible."))
        if not target.exists():
            raise UserError(_("L'enregistrement cible n'existe pas."))
        if not hasattr(target, "message_post"):
            raise UserError(
                _("Le mod\u00e8le %s ne supporte pas la chatter.", target._name)
            )
        self._validate_access(target)
        return target

    def action_apply_suggested(self):
        self.ensure_one()
        if not self.suggested_model or not self.suggested_res_id:
            raise UserError(_("Aucune suggestion disponible."))
        target = self.env[self.suggested_model].browse(self.suggested_res_id)
        if not target.exists():
            raise UserError(_("La suggestion pointe vers un enregistrement supprim\u00e9."))
        self.target_ref = target
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.mail.reroute.wizard",
            "res_id": self.id,
            "views": [[False, "form"]],
            "target": "new",
        }

    def action_reroute(self):
        self.ensure_one()
        target = self._validate_target()
        email = self.email_id
        msg = email.mail_message_id
        if not msg:
            raise UserError(_("mail.message source introuvable."))
        old_model = email.res_model
        old_res_id = email.res_id
        if old_model == target._name and old_res_id == target.id:
            raise UserError(_("Le courriel est d\u00e9j\u00e0 sur cette chatter."))

        source = self._get_source_record()

        msg.sudo().write({
            "model": target._name,
            "res_id": target.id,
        })

        attachments = msg.attachment_ids
        if attachments and old_model:
            linked = attachments.filtered(
                lambda a: a.res_model == old_model and a.res_id == old_res_id
            )
            if linked:
                linked.sudo().write({
                    "res_model": target._name,
                    "res_id": target.id,
                })

        email.sudo().write({
            "res_model": target._name,
            "res_id": target.id,
        })

        subj = (email.subject or "").strip() or _("(sans objet)")
        note_body = _(
            "Courriel \u00ab %(subj)s \u00bb re-rout\u00e9 par %(user)s",
            subj=subj,
            user=self.env.user.display_name,
        )
        target.message_post(
            body=note_body,
            subtype_xmlid="mail.mt_note",
            message_type="comment",
        )
        if source is not None and hasattr(source, "message_post"):
            source.message_post(
                body=_(
                    "Courriel \u00ab %(subj)s \u00bb d\u00e9plac\u00e9 vers %(tgt)s "
                    "par %(user)s",
                    subj=subj,
                    tgt=target.display_name,
                    user=self.env.user.display_name,
                ),
                subtype_xmlid="mail.mt_note",
                message_type="comment",
            )

        _logger.info(
            "bf_mail_vigie: user=%s moved mail.message=%s from %s,%s to %s,%s",
            self.env.user.login, msg.id,
            old_model, old_res_id, target._name, target.id,
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": target._name,
            "res_id": target.id,
            "views": [[False, "form"]],
            "target": "current",
        }
