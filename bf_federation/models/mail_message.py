import base64

from odoo import api, models

from . import transport


class MailMessage(models.Model):
    _inherit = "mail.message"

    @api.model_create_multi
    def create(self, vals_list):
        messages = super().create(vals_list)
        if not self.env.context.get("federation_inbound"):
            messages.sudo()._federation_forward()
        return messages

    def _federation_forward(self):
        Task = self.env["project.task"].sudo()
        note_id = self.env.ref("mail.mt_note").id
        comment_id = self.env.ref("mail.mt_comment").id
        for msg in self:
            if msg.model != "project.task" or not msg.res_id or msg.message_type not in ("comment", "email"):
                continue
            if msg.subtype_id.id not in (note_id, comment_id):
                continue
            task = Task.browse(msg.res_id).exists()
            if not task:
                continue
            link = task._federation_link()
            if not link:
                continue
            peer = link.peer_id
            if msg.subtype_id.id == note_id and not peer.send_notes:
                continue
            if transport.is_private(msg.body):
                continue
            limit = (peer.attachment_limit_mb or 2) * 1024 * 1024
            attachments = []
            for att in msg.attachment_ids:
                size = att.file_size or 0
                entry = {"name": att.name, "mimetype": att.mimetype, "size": size}
                if size <= limit and att.datas:
                    entry["data"] = att.datas.decode() if isinstance(att.datas, bytes) else att.datas
                attachments.append(entry)
            author = msg.author_id
            payload = {
                "body_text": transport.html_to_text(msg.body),
                "subtype": "note" if msg.subtype_id.id == note_id else "comment",
                "author": {"name": author.name if author else (msg.email_from or ""), "email": author.email if author else msg.email_from},
                "date": msg.date.strftime("%Y-%m-%d %H:%M:%S") if msg.date else False,
                "attachments": attachments,
                "sender_message_ref": str(msg.id),
            }
            peer._enqueue("message.new", payload, link)
            self.env["federation.link.message"].sudo().create({"link_id": link.id, "local_message_id": msg.id, "direction": "out"})
