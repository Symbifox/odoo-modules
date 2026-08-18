import base64
import csv
import io
import logging
import re
from datetime import timezone
from xml.etree.ElementTree import Element, SubElement, tostring

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SmsArchiveThread(models.Model):
    _name = "sms.archive.thread"
    _description = "Fil de conversation SMS"
    _order = "last_message_date desc nulls last, id desc"

    phone_raw = fields.Char(
        string="Numéro original",
        help="Numéro tel que lu dans le fichier XML d'export",
    )
    phone_normalized = fields.Char(
        string="Téléphone (E.164)",
        required=True,
        index=True,
        help="Numéro normalisé utilisé comme clé de regroupement",
    )
    contact_name = fields.Char(
        string="Nom du contact",
        help="Dernier nom connu provenant du fichier XML",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Contact Odoo",
        help="Lien vers un contact Odoo (correspondance automatique par téléphone)",
    )
    owner_id = fields.Many2one(
        comodel_name="res.users",
        string="Propriétaire",
        required=True,
        default=lambda self: self.env.uid,
        index=True,
    )
    active = fields.Boolean(
        string="Actif",
        default=True,
    )
    is_hidden = fields.Boolean(
        string="Confidentiel",
        default=False,
        help="Masquer ce fil des recherches et listes MCP",
    )
    is_pinned = fields.Boolean(
        string="Épinglé",
        default=False,
        help="Garder ce fil en haut de la liste de la Messagerie.",
    )
    message_ids = fields.One2many(
        comodel_name="sms.archive.message",
        inverse_name="thread_id",
        string="Messages",
    )
    task_ids = fields.Many2many(
        comodel_name="project.task",
        relation="sms_thread_task_rel",
        column1="thread_id",
        column2="task_id",
        string="Tâches liées",
    )
    auto_post_task_id = fields.Many2one(
        comodel_name="project.task",
        string="Suivi dans la tâche",
        help="Quand c'est renseigné, chaque nouveau message de cette conversation est "
             "relayé automatiquement au chatter de cette tâche. Ne s'applique qu'aux "
             "messages qui arrivent ensuite, jamais à un import rétroactif.",
    )
    call_ids = fields.One2many(
        comodel_name="call.archive.call",
        inverse_name="thread_id",
        string="Appels",
    )

    message_count = fields.Integer(
        string="Nombre de messages",
        compute="_compute_message_stats",
        store=True,
    )
    last_message_date = fields.Datetime(
        string="Dernier message",
        compute="_compute_message_stats",
        store=True,
    )
    last_message_preview = fields.Char(
        string="Aperçu",
        compute="_compute_message_stats",
        store=True,
    )

    call_count = fields.Integer(
        string="Nombre d'appels",
        compute="_compute_call_stats",
        store=True,
    )
    last_call_date = fields.Datetime(
        string="Dernier appel",
        compute="_compute_call_stats",
        store=True,
    )
    unread_count = fields.Integer(
        string="Non lus",
        compute="_compute_unread_count",
        store=True,
    )

    display_name = fields.Char(
        compute="_compute_display_name",
    )

    _sql_constraints = [
        (
            "phone_owner_uniq",
            "UNIQUE(phone_normalized, owner_id)",
            "Un fil existe déjà pour ce numéro et ce propriétaire.",
        ),
    ]

    @api.depends("message_ids", "message_ids.date_sent", "message_ids.body")
    def _compute_message_stats(self):
        for thread in self:
            messages = thread.message_ids.sorted("date_sent", reverse=True)
            thread.message_count = len(messages)
            if messages:
                thread.last_message_date = messages[0].date_sent
                preview = (messages[0].body or "")[:100]
                thread.last_message_preview = preview
            else:
                thread.last_message_date = False
                thread.last_message_preview = False

    @api.depends("call_ids", "call_ids.date")
    def _compute_call_stats(self):
        for thread in self:
            calls = thread.call_ids.sorted("date", reverse=True)
            thread.call_count = len(calls)
            thread.last_call_date = calls[0].date if calls else False

    @api.depends("message_ids.is_read", "message_ids.direction")
    def _compute_unread_count(self):
        for thread in self:
            thread.unread_count = len(thread.message_ids.filtered(
                lambda m: m.direction == "in" and not m.is_read
            ))

    @api.depends("contact_name", "partner_id", "phone_normalized")
    def _compute_display_name(self):
        for thread in self:
            name = thread.contact_name or (
                thread.partner_id.name if thread.partner_id else False
            )
            if name:
                thread.display_name = f"{name} ({thread.phone_normalized})"
            else:
                thread.display_name = thread.phone_normalized or "?"

    def write(self, vals):
        """Auto-update contact_name when partner_id is set."""
        res = super().write(vals)
        if "partner_id" in vals and vals["partner_id"]:
            for thread in self:
                if thread.partner_id and thread.partner_id.name:
                    super(SmsArchiveThread, thread).write(
                        {"contact_name": thread.partner_id.name}
                    )
        return res

    def action_view_messages(self):
        """Open messages for this thread."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Messages — {self.display_name}",
            "res_model": "sms.archive.message",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("thread_id", "=", self.id)],
            "context": {"default_thread_id": self.id},
        }

    def action_view_calls(self):
        """Open calls for this thread."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Appels — {self.display_name}",
            "res_model": "call.archive.call",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("thread_id", "=", self.id)],
            "context": {"default_thread_id": self.id},
        }

    def action_match_partner(self):
        """Try to match this thread's phone number to an Odoo contact."""
        self.ensure_one()
        phone = self.phone_normalized
        if not phone:
            return
        partner = self.env["res.partner"].search(
            ["|", ("phone", "ilike", phone[-10:]), ("mobile", "ilike", phone[-10:])],
            limit=1,
        )
        if partner:
            self.partner_id = partner

    # ── Exports ────────────────────────────────────────────────

    def action_export_csv(self):
        """Export conversation as CSV and download."""
        self.ensure_one()
        return self._build_csv_download(
            self.message_ids,
            filename=f"sms_{self.phone_normalized}_{fields.Date.today()}.csv",
            res_id=self.id,
        )

    def action_export_xml(self):
        """Export conversation as SMS Backup & Restore compatible XML."""
        self.ensure_one()
        return self._build_xml_download(
            self.message_ids,
            filename=f"sms_{self.phone_normalized}_{fields.Date.today()}.xml",
            res_id=self.id,
        )

    def action_print_pdf(self):
        """Print the conversation as a branded PDF."""
        self.ensure_one()
        return self.env.ref(
            "bf_sms_archive.action_report_sms_thread"
        ).report_action(self)

    @api.model
    def _build_csv_download(self, messages, filename, res_id=False):
        """Build a CSV attachment for an arbitrary message recordset."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Conversation", "Date", "Direction", "Contact", "Contenu", "MMS"])
        for msg in messages.sorted("date_sent"):
            writer.writerow([
                msg.thread_id.display_name or "",
                str(msg.date_sent) if msg.date_sent else "",
                msg.direction,
                msg.contact_name or "",
                msg.body or "",
                "Oui" if msg.is_mms else "Non",
            ])
        data = base64.b64encode(output.getvalue().encode("utf-8-sig"))
        att = self.env["ir.attachment"].create({
            "name": filename,
            "type": "binary",
            "datas": data,
            "mimetype": "text/csv",
            "res_model": "sms.archive.thread" if res_id else False,
            "res_id": res_id or False,
        })
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{att.id}?download=true",
            "target": "new",
        }

    @api.model
    def _build_xml_download(self, messages, filename, res_id=False):
        """Build an SMS Backup & Restore compatible XML attachment for a message recordset."""
        messages = messages.sorted("date_sent")
        direction_map = {"in": "1", "out": "2", "draft": "3"}

        root = Element("smses", count=str(len(messages)), type="full")
        for msg in messages:
            thread = msg.thread_id
            SubElement(root, "sms",
                       protocol="0",
                       address=thread.phone_raw or thread.phone_normalized or "",
                       date=msg.date_sent_ms or str(int(msg.date_sent.timestamp() * 1000)),
                       type=direction_map.get(msg.direction, "1"),
                       body=msg.body or "",
                       contact_name=msg.contact_name or thread.contact_name or "",
                       readable_date=str(msg.date_sent) if msg.date_sent else "")

        xml_bytes = b'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n'
        xml_bytes += tostring(root, encoding="unicode").encode("utf-8")
        data = base64.b64encode(xml_bytes)
        att = self.env["ir.attachment"].create({
            "name": filename,
            "type": "binary",
            "datas": data,
            "mimetype": "application/xml",
            "res_model": "sms.archive.thread" if res_id else False,
            "res_id": res_id or False,
        })
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{att.id}?download=true",
            "target": "new",
        }

    # MIME types that wkhtmltopdf (Qt WebKit) can render inline
    _PDF_RENDERABLE_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/gif"}

    # wkhtmltopdf 0.12.6 cannot render supplementary plane Unicode (U+10000+).
    # Map common emoji to their closest BMP equivalents.
    _EMOJI_BMP_MAP = {
        "\U0001F642": "\u263A",  # 🙂 → ☺
        "\U0001F600": "\u263A",  # 😀 → ☺
        "\U0001F601": "\u263A",  # 😁 → ☺
        "\U0001F603": "\u263A",  # 😃 → ☺
        "\U0001F604": "\u263A",  # 😄 → ☺
        "\U0001F60A": "\u263A",  # 😊 → ☺
        "\U0001F60E": "\u263A",  # 😎 → ☺
        "\U0001F609": "\u263A",  # 😉 → ☺
        "\U0001F60D": "\u2665",  # 😍 → ♥
        "\U0001F622": "\u2639",  # 😢 → ☹
        "\U0001F62D": "\u2639",  # 😭 → ☹
        "\U0001F621": "\u2639",  # 😡 → ☹
        "\U0001F614": "\u2639",  # 😔 → ☹
        "\U0001F44D": "\u2713",  # 👍 → ✓
        "\U0001F44E": "\u2717",  # 👎 → ✗
        "\U0001F525": "\u2605",  # 🔥 → ★
        "\U0001F4AA": "\u2605",  # 💪 → ★
        "\U0001F389": "\u2605",  # 🎉 → ★
        "\U0001F44B": "\u263A",  # 👋 → ☺
        "\U0001F64F": "\u263A",  # 🙏 → ☺
    }

    # BMP emoji characters used as replacements
    _BMP_EMOJI = set("\u263A\u2639\u2665\u2605\u2713\u2717\u2726")

    @staticmethod
    def _sanitize_body_for_pdf(text):
        """Replace supplementary plane emoji with BMP equivalents for wkhtmltopdf.

        Also strips trailing lines that contain only emoji (e.g. a lone ☺ on the last line).
        """
        if not text:
            return text
        result = []
        for ch in text:
            if ord(ch) > 0xFFFF:
                mapped = SmsArchiveThread._EMOJI_BMP_MAP.get(ch)
                if mapped:
                    result.append(mapped)
                # else: silently drop (same as wkhtmltopdf would do)
            else:
                result.append(ch)
        sanitized = "".join(result).rstrip()
        # Strip trailing lines that are only emoji (e.g. "\nsome text\n☺" → "\nsome text")
        lines = sanitized.split("\n")
        while lines and all(
            ch in SmsArchiveThread._BMP_EMOJI or ch == " "
            for ch in lines[-1]
        ):
            lines.pop()
        return "\n".join(lines)

    @staticmethod
    def _convert_image_for_pdf(datas_b64, content_type):
        """Convert HEIC/WEBP image to JPEG data URI for wkhtmltopdf.

        Returns a data:image/jpeg;base64,... string, or empty string on failure.
        """
        try:
            # Register HEIC opener if available
            try:
                import pillow_heif
                pillow_heif.register_heif_opener()
            except ImportError:
                if "heic" in content_type:
                    return ""

            from PIL import Image
            Image.MAX_IMAGE_PIXELS = 25_000_000
            raw = base64.b64decode(datas_b64)
            try:
                img = Image.open(io.BytesIO(raw))
            except Image.DecompressionBombError:
                _logger.warning("Decompression bomb detected for %s image", content_type)
                return ""
            # Resize if very large (max 800px wide for PDF)
            if img.width > 800:
                ratio = 800 / img.width
                img = img.resize((800, int(img.height * ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=80)
            jpeg_b64 = base64.b64encode(buf.getvalue()).decode()
            return f"data:image/jpeg;base64,{jpeg_b64}"
        except Exception:
            _logger.warning("Failed to convert %s image for PDF", content_type, exc_info=True)
            return ""

    @staticmethod
    def _get_emoji_font_data_uri():
        """Return NotoEmoji font as base64 data URI for embedding in CSS."""
        import os
        font_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static", "fonts", "NotoEmoji-Regular.ttf",
        )
        try:
            with open(font_path, "rb") as f:
                import base64 as b64mod
                return "data:font/truetype;base64," + b64mod.b64encode(f.read()).decode()
        except FileNotFoundError:
            _logger.warning("NotoEmoji-Regular.ttf not found at %s", font_path)
            return ""

    @staticmethod
    def _build_report_css(brand_primary, brand_dark):
        """Return report CSS with interpolated brand colours as ``Markup``."""
        from markupsafe import Markup
        # Escape values (hex colours are safe, but be defensive)
        p = Markup.escape(brand_primary)
        d = Markup.escape(brand_dark)
        return Markup(
            f".sms-page {{"
            f"  font-family: 'Lexend', 'NotoEmoji', 'Noto Color Emoji', system-ui, sans-serif;"
            f"  font-size: 9pt; color: #333;"
            f"}}"
            f".sms-banner {{"
            f"  background: {d}; padding: 18pt 24pt 16pt 24pt;"
            f"  display: table; width: 100%;"
            f"}}"
            f".sms-banner-logo {{"
            f"  display: table-cell; vertical-align: middle; width: 90pt;"
            f"}}"
            f".sms-banner-logo img {{ max-width: 80pt; max-height: 40pt; }}"
            f".sms-banner-content {{"
            f"  display: table-cell; vertical-align: middle;"
            f"}}"
            f".sms-banner .doc-type {{"
            f"  font-size: 8pt; font-weight: 600; text-transform: uppercase;"
            f"  letter-spacing: 1pt; color: {p}; margin-bottom: 3pt;"
            f"}}"
            f".sms-banner h1 {{"
            f"  font-size: 16pt; font-weight: 600; color: #ffffff;"
            f"  margin: 0; line-height: 1.2;"
            f"}}"
            f".sms-banner .doc-subtitle {{"
            f"  font-size: 9pt; color: rgba(255,255,255,0.7); margin-top: 3pt;"
            f"}}"
            f".sms-accent-bar {{"
            f"  height: 3px; background: {p}; margin: 0; width: 100%;"
            f"}}"
            f".sms-meta {{"
            f"  background: #f8f9fb; padding: 8pt 24pt; display: table;"
            f"  width: 100%; font-size: 8pt; color: #666;"
            f"  border-bottom: 1px solid #e8e8e8;"
            f"}}"
            f".sms-meta-item {{ display: table-cell; padding-right: 24pt; }}"
            f".sms-meta-label {{"
            f"  font-weight: 600; color: #999; text-transform: uppercase;"
            f"  font-size: 7pt; letter-spacing: 0.5pt;"
            f"}}"
            f".sms-meta-value {{ color: #333; font-size: 9pt; }}"
            f".sms-messages {{ padding: 8pt 24pt; }}"
            f".sms-row {{ margin-bottom: 2pt; overflow: hidden; }}"
            f".sms-row-left {{ text-align: left; }}"
            f".sms-row-right {{ text-align: right; }}"
            f".sms-bubble {{"
            f"  display: inline-block; max-width: 85%; text-align: left;"
            f"  padding: 5pt 10pt; border-radius: 8pt; font-size: 9pt;"
            f"  line-height: 1.35; word-wrap: break-word; white-space: pre-line;"
            f"  font-family: 'Lexend', 'NotoEmoji', 'Noto Color Emoji', system-ui, sans-serif;"
            f"}}"
            f".sms-bubble-in {{ background: #f0f0f0; color: #333; }}"
            f".sms-bubble-out {{ background: {p}; color: #ffffff; }}"
            f".sms-bubble-draft {{"
            f"  background: #fef3cd; color: #664d03; border: 1px dashed #ccc;"
            f"}}"
            f".sms-ts {{ font-size: 7pt; color: #999; margin-top: 2pt; white-space: nowrap; }}"
            f".sms-bubble-out .sms-ts {{ color: rgba(255,255,255,0.65); }}"
            f".sms-img {{"
            f"  max-width: 100%; max-height: 200pt; border-radius: 4pt;"
            f"  margin-top: 4pt; display: block;"
            f"}}"
            f".sms-img-placeholder {{"
            f"  font-size: 7pt; color: #999; font-style: italic; margin-top: 4pt;"
            f"}}"
            f".sms-bubble-out .sms-img-placeholder {{ color: rgba(255,255,255,0.6); }}"
            f".sms-date-sep {{"
            f"  text-align: center; font-size: 7pt; font-weight: 600; color: #999;"
            f"  text-transform: uppercase; letter-spacing: 0.5pt; margin: 6pt 0 4pt 0;"
            f"}}"
            f".sms-footer {{"
            f"  font-size: 7pt; color: #999; text-align: center;"
            f"  border-top: 1px solid #ddd; padding-top: 4pt; margin-top: 8pt;"
            f"}}"
        )

    def _get_report_data(self):
        """Prepare data for the per-thread PDF report (binding_model: sms.archive.thread)."""
        data = self._build_shared_report_data()
        data["groups"] = [self._build_thread_group(self, self.message_ids)]
        group = data["groups"][0]
        data.update({
            "thread": self,
            "messages": group["messages"],
            "msg_bodies": group["msg_bodies"],
            "msg_images": group["msg_images"],
            "contact": group["contact"],
            "phone": group["phone"],
            "count": group["count"],
        })
        return data

    @api.model
    def _build_shared_report_data(self):
        """Brand/CSS/font payload shared across thread and message reports."""
        from odoo.tools.image import image_data_uri

        company = self.env.company
        brand_primary = getattr(company, 'report_brand_primary', None) or '#714B67'
        brand_dark = getattr(company, 'report_brand_dark', None) or '#212529'

        has_lexend = bool(
            self.env['ir.module.module'].sudo().search(
                [('name', '=', 'bf_lexend'), ('state', '=', 'installed')],
                limit=1,
            )
        )

        company_logo = ""
        if company.logo:
            company_logo = image_data_uri(company.logo)

        return {
            "emoji_font_uri": self._get_emoji_font_data_uri(),
            "report_css": self._build_report_css(brand_primary, brand_dark),
            "has_lexend": has_lexend,
            "company_name": company.name or "",
            "company_logo": company_logo,
        }

    @api.model
    def _build_thread_group(self, thread, messages):
        """Build a per-thread rendering block (used by thread and message reports)."""
        from odoo.tools.image import image_data_uri

        messages = messages.sorted("date_sent")

        msg_bodies = {msg.id: self._sanitize_body_for_pdf(msg.body) for msg in messages}

        msg_images = {}
        for msg in messages:
            if msg.is_mms and msg.mms_part_ids:
                images = []
                for part in msg.mms_part_ids:
                    if part.is_image and part.attachment_id and part.attachment_id.datas:
                        ct = (part.content_type or "").lower()
                        if ct in self._PDF_RENDERABLE_TYPES:
                            images.append({
                                "data_uri": image_data_uri(part.attachment_id.datas),
                                "filename": part.filename or "image",
                                "content_type": ct,
                                "renderable": True,
                            })
                        else:
                            converted = self._convert_image_for_pdf(
                                part.attachment_id.datas, ct,
                            )
                            if converted:
                                images.append({
                                    "data_uri": converted,
                                    "filename": part.filename or "image",
                                    "content_type": "image/jpeg",
                                    "renderable": True,
                                })
                            else:
                                images.append({
                                    "data_uri": "",
                                    "filename": part.filename or "image",
                                    "content_type": ct,
                                    "renderable": False,
                                })
                if images:
                    msg_images[msg.id] = images

        return {
            "thread": thread,
            "messages": messages,
            "msg_bodies": msg_bodies,
            "msg_images": msg_images,
            "contact": thread.contact_name or thread.phone_normalized,
            "phone": thread.phone_normalized,
            "count": len(messages),
        }

    @staticmethod
    def normalize_phone(raw):
        """Normalize a phone number to E.164-ish format."""
        digits = re.sub(r"[^\d+]", "", raw or "")
        if digits.startswith("+"):
            return digits
        digits = re.sub(r"\D", "", digits)
        if len(digits) == 10:
            return f"+1{digits}"
        if len(digits) == 11 and digits.startswith("1"):
            return f"+{digits}"
        return f"+{digits}" if digits else ""

    @api.model
    def _get_or_create(self, phone_normalized, owner_id, phone_raw=None, contact_name=None):
        """Idempotent thread upsert by (phone_normalized, owner_id)."""
        # active_test=False : retrouver aussi les fils archivés (sinon la contrainte
        # d'unicité (phone, owner) bloquerait la recréation).
        thread = self.sudo().with_context(active_test=False).search([
            ("phone_normalized", "=", phone_normalized),
            ("owner_id", "=", owner_id),
        ], limit=1)
        if thread:
            vals = {}
            if contact_name and not thread.contact_name:
                vals["contact_name"] = contact_name
            if not thread.active:
                vals["active"] = True  # un nouveau message désarchive le fil
            if vals:
                thread.sudo().write(vals)
            return thread
        thread = self.sudo().create({
            "phone_normalized": phone_normalized,
            "phone_raw": phone_raw or phone_normalized,
            "contact_name": contact_name or "",
            "owner_id": owner_id,
        })
        # Best-effort: auto-match an Odoo contact by phone so names/avatars populate.
        if not thread.partner_id and phone_normalized:
            try:
                tail = phone_normalized[-10:]
                partner = self.env["res.partner"].sudo().search(
                    ["|", ("phone", "ilike", tail), ("mobile", "ilike", tail)], limit=1,
                )
                if partner:
                    thread.partner_id = partner.id
            except Exception:  # noqa: BLE001 — un échec de matching ne doit pas bloquer l'ingestion
                _logger.debug("auto-match partner échoué pour %s", phone_normalized, exc_info=True)
        return thread

    # ── Archivage (boutons formulaire) ─────────────────────────────

    def action_archive_thread(self):
        self.write({"active": False})
        return True

    def action_unarchive_thread(self):
        self.write({"active": True})
        return True

    # ── API « Messagerie » (SPA OWL) ───────────────────────────────

    @staticmethod
    def _dt_to_ms(dt):
        if not dt:
            return 0
        return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)

    def _check_messenger_access(self):
        """Limite l'accès aux fils du propriétaire courant (sauf gestionnaire)."""
        is_manager = self.env.user.has_group("bf_sms_archive.group_sms_manager")
        for thread in self:
            if not is_manager and thread.owner_id.id != self.env.uid:
                raise UserError("Accès refusé à cette conversation.")

    def _ping_unread(self, users):
        """Ping temps réel « lu » (sans bip) → rafraîchit le compteur systray et
        les badges de la liste dans tous les onglets/appareils du propriétaire."""
        Bus = self.env["bus.bus"]
        for usr in users:
            if usr and usr.partner_id:
                Bus._sendone(usr.partner_id, "sms.archive/read", {"kind": "read"})

    def _messenger_thread_dict(self, line_label=None):
        self.ensure_one()
        if line_label is None:
            last = self.env["sms.archive.message"].search(
                [("thread_id", "=", self.id), ("line_id", "!=", False)],
                order="id desc", limit=1,
            )
            line_label = last.line_id.label if last else ""
        return {
            "id": self.id,
            "contact_name": self.contact_name or "",
            "phone": self.phone_normalized or "",
            "phone_raw": self.phone_raw or "",
            "partner_id": self.partner_id.id or False,
            "partner_name": self.partner_id.name or "",
            "last_message_date": self._dt_to_ms(self.last_message_date),
            "last_preview": self.last_message_preview or "",
            "unread_count": self.unread_count,
            "active": self.active,
            "is_hidden": self.is_hidden,
            "is_pinned": self.is_pinned,
            "line_label": line_label or "",
        }

    @api.model
    def get_messenger_config(self):
        """Préférences d'affichage de la SPA. ``tz`` = fuseau de l'utilisateur
        (via bf_timezone si présent), sinon False → l'horloge du navigateur."""
        tz = False
        if "bf.timezone" in self.env:
            tz = self.env["bf.timezone"].sudo().resolve([self.env.user.tz]) \
                or self.env.user.tz or False
        return {
            "tz": tz or False,
            # Clé publique VAPID (application server key) pour l'abonnement Web
            # Push côté navigateur — générée à l'installation, idempotent ici.
            "vapid_public_key":
                self.env["sms.archive.push.subscription"]._ensure_vapid_keys(),
        }

    @api.model
    def push_subscribe(self, endpoint, p256dh, auth, ua=None):
        """Enregistre (ou réactive) l'abonnement Web Push du navigateur courant
        pour l'utilisateur connecté. Appelé par la SPA après ``pushManager``."""
        if not (endpoint and p256dh and auth):
            return False
        self.env["sms.archive.push.subscription"]._upsert(
            self.env.uid, endpoint, p256dh, auth, ua,
        )
        return True

    @api.model
    def push_unsubscribe(self, endpoint):
        """Désactive l'abonnement Web Push correspondant (déconnexion propre)."""
        subs = self.env["sms.archive.push.subscription"].sudo().search([
            ("endpoint", "=", endpoint), ("user_id", "=", self.env.uid),
        ])
        subs.write({"active": False})
        return True

    @api.model
    def get_lines(self):
        """Lignes (DID) disponibles pour l'utilisateur courant."""
        Line = self.env["sms.archive.line"]
        lines = Line.search([("owner_id", "=", self.env.uid)])
        if not lines and self.env.user.has_group("bf_sms_archive.group_sms_manager"):
            lines = Line.search([])
        return [{
            "id": line.id,
            "label": line.label,
            "did": line.did_normalized or line.did,
            "sms_enabled": line.sms_enabled,
            "mms_enabled": line.mms_enabled,
            "is_default": line.is_default,
        } for line in lines]

    @api.model
    def get_messenger_threads(self, archived=False, search=None, line_id=None, limit=200):
        """Liste des fils (volet gauche) : épinglés d'abord, puis par dernier message."""
        domain = [("owner_id", "=", self.env.uid), ("active", "=", not archived)]
        if search:
            term = search.strip()
            domain += [
                "|", "|",
                ("contact_name", "ilike", term),
                ("phone_normalized", "ilike", term),
                ("partner_id.name", "ilike", term),
            ]
        if line_id:
            domain.append(("message_ids.line_id", "=", int(line_id)))
        threads = self.with_context(active_test=False).search(
            domain, limit=limit,
            order="is_pinned desc, last_message_date desc nulls last, id desc",
        )
        # Étiquette de ligne par fil (dernier message portant une ligne) — une requête.
        label_map = {}
        if threads.ids:
            rows = self.env["sms.archive.message"].search_read(
                [("thread_id", "in", threads.ids), ("line_id", "!=", False)],
                ["thread_id", "line_id"], order="id desc",
            )
            for r in rows:
                tid = r["thread_id"][0]
                if tid not in label_map:
                    label_map[tid] = r["line_id"][1]
        return [t._messenger_thread_dict(line_label=label_map.get(t.id, "")) for t in threads]

    @api.model
    def get_conversation(self, thread_id, before_id=None, limit=50):
        """Messages d'un fil (volet droit), pagination par ``before_id`` (défilement infini)."""
        thread = self.with_context(active_test=False).browse(int(thread_id))
        if not thread.exists():
            raise UserError("Conversation introuvable.")
        thread._check_messenger_access()
        Msg = self.env["sms.archive.message"]
        domain = [("thread_id", "=", thread.id)]
        if before_id:
            domain.append(("id", "<", int(before_id)))
        found = Msg.search(domain, order="id desc", limit=limit)
        messages = [m._messenger_dict() for m in found.sorted("id")]
        # Marque les entrants comme lus à l'ouverture
        unread = found.filtered(lambda m: m.direction == "in" and not m.is_read)
        if unread:
            unread.write({"is_read": True})
            self._ping_unread(thread.owner_id)
            self._up_clear(thread.owner_id, thread.id)
        return {
            "thread": thread._messenger_thread_dict(),
            "messages": messages,
            "has_more": len(found) == limit,
        }

    def _up_clear(self, owner, thread_id=None):
        """Sync lu → efface la notification correspondante sur l'app mobile
        (UnifiedPush). Défensif : ne perturbe jamais la lecture."""
        try:
            UP = self.env["sms.archive.unifiedpush"]
            if thread_id is None:
                UP._notify_clear_all(owner)
            else:
                UP._notify_clear(owner, thread_id)
        except Exception:  # noqa: BLE001
            pass

    @api.model
    def mark_thread_read(self, thread_id):
        thread = self.with_context(active_test=False).browse(int(thread_id))
        thread._check_messenger_access()
        thread.message_ids.filtered(
            lambda m: m.direction == "in" and not m.is_read
        ).write({"is_read": True})
        self._ping_unread(thread.owner_id)
        self._up_clear(thread.owner_id, thread.id)
        return self.get_unread_summary()

    @api.model
    def messenger_set_archived(self, thread_id, archived):
        thread = self.with_context(active_test=False).browse(int(thread_id))
        thread._check_messenger_access()
        thread.write({"active": not archived})
        return True

    @api.model
    def messenger_bulk_archive(self, thread_ids, archived):
        """Archive/désarchive plusieurs fils d'un coup (sélection multiple)."""
        threads = self.with_context(active_test=False).browse(
            [int(t) for t in thread_ids]
        ).exists()
        threads._check_messenger_access()
        threads.write({"active": not archived})
        return True

    @api.model
    def messenger_link_partner(self, thread_id, partner_id):
        """Rattache un fil (numéro inconnu) à un contact Odoo existant."""
        thread = self.with_context(active_test=False).browse(int(thread_id))
        thread._check_messenger_access()
        thread.write({"partner_id": int(partner_id)})  # write() reporte le nom du contact
        return thread._messenger_thread_dict()

    @api.model
    def messenger_create_partner(self, thread_id, name=None):
        """Crée un nouveau contact Odoo depuis un numéro inconnu et l'y rattache."""
        thread = self.with_context(active_test=False).browse(int(thread_id))
        thread._check_messenger_access()
        partner = self.env["res.partner"].create({
            "name": (name or "").strip() or thread.contact_name or thread.phone_normalized,
            "phone": thread.phone_normalized,
        })
        thread.write({"partner_id": partner.id})
        return thread._messenger_thread_dict()

    # ── Poster sur une tâche depuis la Messagerie ──────────────────

    @api.model
    def messenger_task_targets(self, thread_id):
        """Tâches proposables pour ce fil, du plus probable au moins probable.

        Sert au bouton « → Tâche » : quand une seule tâche sort, la Messagerie poste
        directement sans ouvrir le sorcier — c'est ce qui fait passer le geste de six
        clics à un."""
        thread = self.with_context(active_test=False).browse(int(thread_id))
        thread._check_messenger_access()
        linked = thread.task_ids.filtered(lambda t: t.active)
        return {
            "auto_post_task_id": thread.auto_post_task_id.id or False,
            "auto_post_task_name": thread.auto_post_task_id.name or "",
            "tasks": [{
                "id": t.id,
                "name": t.name,
                "project_name": t.project_id.name or "",
            } for t in linked],
        }

    @api.model
    def messenger_search_tasks(self, term, limit=8):
        """Recherche de tâches pour le sélecteur inline de la Messagerie."""
        domain = [("name", "ilike", term)] if term else []
        tasks = self.env["project.task"].search(domain, limit=int(limit), order="write_date desc")
        return [{
            "id": t.id,
            "name": t.name,
            "project_name": t.project_id.name or "",
        } for t in tasks]

    @api.model
    def messenger_post_to_task(self, thread_id, task_id, message_ids=None, call_ids=None):
        """Poste une sélection (ou toute la conversation chargée) sur une tâche.

        ``message_ids``/``call_ids`` vides = tout le fil. Les identifiants reçus du client
        sont réduits au fil visé avant tout envoi : on ne poste jamais un message qu'un
        appelant aurait glissé dans la liste."""
        thread = self.with_context(active_test=False).browse(int(thread_id))
        thread._check_messenger_access()
        task = self.env["project.task"].browse(int(task_id)).exists()
        if not task:
            raise UserError("Tâche introuvable.")

        if message_ids:
            messages = thread.message_ids.filtered(lambda m: m.id in set(message_ids))
        elif call_ids:
            messages = self.env["sms.archive.message"]
        else:
            messages = thread.message_ids
        calls = (thread.call_ids.filtered(lambda c: c.id in set(call_ids))
                 if call_ids else self.env["call.archive.call"])

        if not messages and not calls:
            raise UserError("Rien à poster.")

        bodies = []
        if messages:
            bodies.append(messages._render_task_post_body())
        if calls:
            bodies.append(calls._render_task_post_body())
        task.message_post(
            body=Markup("").join(b for b in bodies if b),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        if task.id not in thread.task_ids.ids:
            thread.write({"task_ids": [(4, task.id, 0)]})
        return {
            "count": len(messages) + len(calls),
            "task_name": task.name,
            "project_name": task.project_id.name or "",
        }

    @api.model
    def messenger_set_auto_task(self, thread_id, task_id):
        """Active ou coupe le suivi automatique du fil vers une tâche."""
        thread = self.with_context(active_test=False).browse(int(thread_id))
        thread._check_messenger_access()
        thread.write({"auto_post_task_id": int(task_id) if task_id else False})
        return {
            "auto_post_task_id": thread.auto_post_task_id.id or False,
            "auto_post_task_name": thread.auto_post_task_id.name or "",
        }

    @api.model
    def messenger_toggle_pin(self, thread_id):
        thread = self.with_context(active_test=False).browse(int(thread_id))
        thread._check_messenger_access()
        thread.is_pinned = not thread.is_pinned
        return thread.is_pinned

    @api.model
    def mark_all_read(self):
        """Marque tous les SMS entrants non lus de l'utilisateur comme lus."""
        self.env["sms.archive.message"].sudo().search([
            ("owner_id", "=", self.env.uid),
            ("direction", "=", "in"),
            ("is_read", "=", False),
        ]).write({"is_read": True})
        self._ping_unread(self.env.user)
        self._up_clear(self.env.user)
        return self.get_unread_summary()

    @api.model
    def messenger_set_thread_read(self, thread_id, read):
        """Bascule l'état lu/non-lu d'un fil (pour « marquer non lu »)."""
        thread = self.with_context(active_test=False).browse(int(thread_id))
        thread._check_messenger_access()
        if read:
            thread.message_ids.filtered(
                lambda m: m.direction == "in" and not m.is_read
            ).write({"is_read": True})
        else:
            inbound = thread.message_ids.filtered(lambda m: m.direction == "in").sorted("id")
            if inbound:
                inbound[-1].is_read = False
        self._ping_unread(thread.owner_id)
        return self.get_unread_summary()

    @api.model
    def start_conversation(self, line_id, phone, contact_name=None):
        """Ouvre (ou crée) un fil pour composer un nouveau message."""
        line = self.env["sms.archive.line"].browse(int(line_id))
        is_manager = self.env.user.has_group("bf_sms_archive.group_sms_manager")
        if line.exists() and line.owner_id.id != self.env.uid and not is_manager:
            raise UserError("Cette ligne ne vous appartient pas.")
        owner = line.owner_id.id if line.exists() else self.env.uid
        phone_norm = self.normalize_phone(phone)
        if not phone_norm:
            raise UserError("Numéro invalide.")
        thread = self._get_or_create(phone_norm, owner, phone, contact_name)
        return thread.id

    @api.model
    def get_unread_summary(self):
        """Total + ventilation par fil des entrants non lus (compteur systray)."""
        Msg = self.env["sms.archive.message"]
        domain = [
            ("owner_id", "=", self.env.uid),
            ("direction", "=", "in"),
            ("is_read", "=", False),
        ]
        total = Msg.search_count(domain)
        groups = Msg.read_group(domain, ["thread_id"], ["thread_id"], limit=20)
        threads = []
        for g in groups:
            if not g.get("thread_id"):
                continue
            tid = g["thread_id"][0]
            thread = self.browse(tid)
            threads.append({
                "id": tid,
                "contact": thread.contact_name or thread.phone_normalized or "",
                "count": g.get("__count") or g.get("thread_id_count") or 0,
                "preview": thread.last_message_preview or "",
            })
        return {"total": total, "threads": threads}
