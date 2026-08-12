import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class SmsArchiveMmsPart(models.Model):
    _name = "sms.archive.mms.part"
    _description = "Pièce jointe MMS"
    _order = "sequence, id"

    message_id = fields.Many2one(
        comodel_name="sms.archive.message",
        string="Message",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(
        string="Séquence",
        default=0,
    )
    content_type = fields.Char(
        string="Type MIME",
    )
    filename = fields.Char(
        string="Nom de fichier",
    )
    attachment_id = fields.Many2one(
        comodel_name="ir.attachment",
        string="Fichier",
        ondelete="cascade",
    )
    text_content = fields.Text(
        string="Contenu texte",
        help="Pour les parties text/plain",
    )
    owner_id = fields.Many2one(
        related="message_id.owner_id",
        store=True,
        index=True,
        string="Propriétaire",
    )

    is_image = fields.Boolean(
        compute="_compute_is_image",
        store=True,
    )

    @api.depends("content_type")
    def _compute_is_image(self):
        for part in self:
            part.is_image = (part.content_type or "").startswith("image/")

    @api.model
    def _ingest_parts(self, message_id, parts):
        """Create MMS parts (and binary attachments) for a message.

        ``parts`` is a list of dicts: {content_type, filename?, data_b64?, text?, sequence?}.
        - text/plain parts may use ``text`` instead of ``data_b64``.
        - Binary parts (image/video/audio) use ``data_b64`` and create an ir.attachment.
        Failures on individual parts are logged but do not abort the whole batch.
        """
        Attachment = self.env["ir.attachment"].sudo()
        created = 0
        for idx, part_data in enumerate(parts or []):
            try:
                content_type = (part_data.get("content_type") or "").strip()
                filename = part_data.get("filename") or f"part-{idx}"
                sequence = part_data.get("sequence", idx)
                text_blob = part_data.get("text")
                data_b64 = part_data.get("data_b64")

                if content_type.startswith("text/") and text_blob is not None and not data_b64:
                    # Text part: store inline, no attachment
                    self.sudo().create({
                        "message_id": message_id,
                        "content_type": content_type,
                        "filename": filename,
                        "text_content": text_blob,
                        "sequence": sequence,
                    })
                    created += 1
                    continue

                if not data_b64:
                    _logger.warning("MMS part %s missing data_b64 (ct=%s)", idx, content_type)
                    continue

                att = Attachment.create({
                    "name": filename,
                    "type": "binary",
                    "datas": data_b64,
                    "mimetype": content_type or "application/octet-stream",
                    "res_model": "sms.archive.mms.part",
                    "res_id": 0,
                })
                part = self.sudo().create({
                    "message_id": message_id,
                    "content_type": content_type,
                    "filename": filename,
                    "attachment_id": att.id,
                    "sequence": sequence,
                })
                att.write({"res_id": part.id})
                created += 1
            except Exception:
                _logger.warning(
                    "Failed to ingest MMS part %s for message %s",
                    part_data.get("filename"), message_id, exc_info=True,
                )
        return created
