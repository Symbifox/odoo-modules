from odoo import api, models

from . import transport


class MailMessage(models.Model):
    _inherit = "mail.message"

    @api.model_create_multi
    def create(self, vals_list):
        messages = super().create(vals_list)
        if not self.env.context.get("federation_inbound"):
            federable = set(self.env["federation.federable"]._federation_models().values())
            candidates = messages.filtered(
                lambda m: m.model in federable and m.res_id and m.message_type in ("comment", "email"))
            if candidates:
                candidates.sudo()._federation_forward()
        return messages

    def _federation_forward(self):
        note_id = self.env.ref("mail.mt_note").id
        comment_id = self.env.ref("mail.mt_comment").id
        pairs = {(m.model, m.res_id) for m in self}
        # Un sur-ensemble par domaine puis l'appariement exact en Python : un domaine
        # en « ou » sur chaque couple exploserait pour un lot de messages.
        links = self.env["federation.link"].sudo().search(
            [("res_model", "in", sorted({m for m, _i in pairs})),
             ("res_id", "in", sorted({i for _m, i in pairs}))])
        by_record = {(l.res_model, l.res_id): l for l in links}
        for msg in self:
            link = by_record.get((msg.model, msg.res_id))
            if not link or msg.subtype_id.id not in (note_id, comment_id):
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
