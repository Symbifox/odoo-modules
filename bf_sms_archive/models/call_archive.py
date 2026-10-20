import hashlib
import logging
from datetime import datetime, timezone

import markupsafe

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


def _ms_to_naive_utc(date_ms):
    """Convert millisecond epoch (int/str) to naive UTC datetime."""
    ms = int(date_ms)
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(tzinfo=None)

# SMS Backup & Restore call type mapping
_CALL_TYPE_MAP = {
    "1": "incoming",
    "2": "outgoing",
    "3": "missed",
    "4": "voicemail",
    "5": "rejected",
    "6": "blocked",
}

_CALL_TYPE_LABELS = {
    "incoming": "↙",
    "outgoing": "↗",
    "missed": "✕",
    "voicemail": "✉",
    "rejected": "⊘",
    "blocked": "⛔",
}

# Presentation attribute mapping
_PRESENTATION_MAP = {
    "1": "allowed",
    "2": "restricted",
    "3": "unknown",
    "4": "payphone",
}


class CallArchiveCall(models.Model):
    _name = "call.archive.call"
    _description = "Archived call"
    _order = "date desc, id desc"

    thread_id = fields.Many2one(
        comodel_name="sms.archive.thread",
        string="Conversation",
        required=True,
        ondelete="cascade",
        index=True,
    )
    call_hash = fields.Char(
        string="Deduplication hash",
        size=64,
        required=True,
        index=True,
    )
    call_type = fields.Selection(
        selection=[
            ("incoming", "Incoming"),
            ("outgoing", "Outgoing"),
            ("missed", "Missed"),
            ("voicemail", "Voicemail"),
            ("rejected", "Rejected"),
            ("blocked", "Blocked"),
        ],
        string="Type",
        required=True,
    )
    date = fields.Datetime(
        string="Date",
        required=True,
        index=True,
    )
    date_ms = fields.Char(
        string="Timestamp (ms)",
        help="Raw timestamp in milliseconds kept from the XML",
    )
    duration = fields.Integer(
        string="Duration (s)",
        default=0,
    )
    duration_display = fields.Char(
        string="Duration",
        compute="_compute_duration_display",
        store=True,
    )
    contact_name = fields.Char(
        string="Contact name",
    )
    owner_id = fields.Many2one(
        related="thread_id.owner_id",
        store=True,
        index=True,
        string="Owner",
    )
    import_batch_id = fields.Char(
        string="Import batch",
        help="backup_set identifier from the XML file",
    )
    presentation = fields.Selection(
        selection=[
            ("allowed", "Allowed"),
            ("restricted", "Restricted"),
            ("unknown", "Unknown"),
            ("payphone", "Payphone"),
        ],
        string="Presentation",
    )
    recording_url = fields.Char(
        string="Recording",
        help="Internal Nextcloud link to the call's audio file",
    )

    link_ids = fields.One2many(
        comodel_name="sms.archive.link",
        inverse_name="call_id",
        string="Links",
        help="The records on whose chatter this call was posted.",
    )
    link_count = fields.Integer(
        string="Records",
        compute="_compute_link_count",
        store=True,
    )

    display_name = fields.Char(
        compute="_compute_display_name",
    )

    @api.depends("link_ids")
    def _compute_link_count(self):
        for call in self:
            call.link_count = len(call.link_ids)

    def _post_to_record(self, record):
        """Pose ces appels sur le chatter de `record` et enregistre le rattachement."""
        if not self or not record:
            return record
        note = record.message_post(
            body=self._render_task_post_body(),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        self.env["sms.archive.link"]._register_links(self, record, note=note)
        return record

    def action_post_to_task(self):
        """Open the wizard to post the selected call(s) to a project task."""
        if not self:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Post to a record"),
            "res_model": "sms.archive.post.to.task.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_call_ids": [(6, 0, self.ids)],
            },
        }

    # ── Rendu pour le chatter d'une tâche ──────────────────────────

    def _render_task_post_body(self):
        """HTML consolidé de ces appels, destiné au chatter d'une tâche.

        L'entrée du journal ne vaut pas grand-chose sans l'audio : quand l'appel a été
        enregistré, le lien Nextcloud est repris ici pour qu'on puisse l'écouter depuis la
        tâche sans repasser par la Messagerie."""
        calls = self.sorted("date")
        if not calls:
            return ""

        # Langue de la note : celle du contexte (l'usager qui pose les appels).
        threads = calls.thread_id
        if len(calls) == 1:
            label = self.env._("1 call")
        else:
            label = self.env._("%(count)s calls", count=len(calls))
        if len(threads) == 1:
            contact = threads.contact_name or threads.phone_normalized or ""
            header = markupsafe.Markup("<p><strong>%s</strong> — %s</p>") % (label, contact)
        else:
            header = markupsafe.Markup(self.env._(
                "<p><strong>%(calls)s</strong> across %(threads)s conversations</p>",
            )) % {"calls": label, "threads": len(threads)}
        type_labels = dict(self._fields["call_type"]._description_selection(self.env))
        listen_label = self.env._("🔊 Listen")

        rows = []
        prev_thread_id = None
        for call in calls:
            if len(threads) > 1 and call.thread_id.id != prev_thread_id:
                contact = markupsafe.escape(
                    call.thread_id.contact_name or call.thread_id.phone_normalized or "")
                rows.append(
                    f"<p style='margin:8px 0 2px 0'><strong>— {contact}</strong></p>"
                )
                prev_thread_id = call.thread_id.id
            arrow = _CALL_TYPE_LABELS.get(call.call_type, "?")
            type_label = markupsafe.escape(type_labels.get(call.call_type, ""))
            color = "#b02a2a" if call.call_type in ("missed", "rejected", "blocked") else "#0f4960"
            date_str = markupsafe.escape(str(call.date or ""))
            duration = markupsafe.escape(call.duration_display or "")
            listen = ""
            if call.recording_url:
                url = markupsafe.escape(call.recording_url)
                listen = f" — <a href='{url}'>{markupsafe.escape(listen_label)}</a>"
            rows.append(
                f"<p style='margin:2px 0'>"
                f"<span style='color:{color};font-weight:600'>{arrow} {type_label}</span> "
                f"<span style='color:#666;font-size:0.9em'>{date_str}</span> — {duration}"
                f"{listen}</p>"
            )
        return header + markupsafe.Markup("".join(rows))

    _sql_constraints = [
        (
            "hash_uniq",
            "UNIQUE(call_hash)",
            "This call already exists (duplicate hash).",
        ),
    ]

    @api.depends("duration")
    def _compute_duration_display(self):
        for call in self:
            secs = call.duration or 0
            if secs < 60:
                call.duration_display = f"{secs}s"
            elif secs < 3600:
                call.duration_display = f"{secs // 60}m {secs % 60:02d}s"
            else:
                h = secs // 3600
                m = (secs % 3600) // 60
                s = secs % 60
                call.duration_display = f"{h}h {m:02d}m {s:02d}s"

    @api.depends("call_type", "thread_id.contact_name", "date", "duration_display")
    def _compute_display_name(self):
        for call in self:
            arrow = _CALL_TYPE_LABELS.get(call.call_type, "?")
            contact = (
                call.thread_id.contact_name
                or call.thread_id.phone_normalized
                or "?"
            )
            dt = str(call.date)[:16] if call.date else ""
            call.display_name = f"{arrow} {contact} — {dt} — {call.duration_display}"

    @api.model
    def _ingest_one(self, *, phone_raw, owner_id, call_type, date_ms,
                    duration=0, contact_name=None, presentation=None, batch_id=None):
        """Single-call ingestion with dedup. Returns (record, created: bool)."""
        Thread = self.env["sms.archive.thread"].sudo()
        phone_norm = Thread.normalize_phone(phone_raw)
        duration_int = int(duration or 0)
        call_hash = hashlib.sha256(
            f"{phone_norm}|{date_ms}|{duration_int}|{call_type}".encode("utf-8")
        ).hexdigest()

        existing = self.sudo().search([("call_hash", "=", call_hash)], limit=1)
        if existing:
            return existing, False

        # for_call=True : le fil n'est ici qu'un porteur de numéro. Il ne sort pas
        # de l'archive pour un appel, et s'il doit naître, il naît archivé.
        thread = Thread._get_or_create(phone_norm, owner_id, phone_raw, contact_name,
                                       for_call=True)
        rec = self.sudo().create({
            "thread_id": thread.id,
            "call_hash": call_hash,
            "call_type": call_type,
            "date": _ms_to_naive_utc(date_ms),
            "date_ms": str(date_ms),
            "duration": duration_int,
            "contact_name": contact_name or "",
            "presentation": presentation or "allowed",
            "import_batch_id": batch_id or "android-live",
        })
        return rec, True
