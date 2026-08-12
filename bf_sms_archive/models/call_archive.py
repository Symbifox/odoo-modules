import hashlib
import logging
from datetime import datetime, timezone

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
    _description = "Appel archivé"
    _order = "date desc, id desc"

    thread_id = fields.Many2one(
        comodel_name="sms.archive.thread",
        string="Conversation",
        required=True,
        ondelete="cascade",
        index=True,
    )
    call_hash = fields.Char(
        string="Hash de dédoublonnage",
        size=64,
        required=True,
        index=True,
    )
    call_type = fields.Selection(
        selection=[
            ("incoming", "Entrant"),
            ("outgoing", "Sortant"),
            ("missed", "Manqué"),
            ("voicemail", "Messagerie vocale"),
            ("rejected", "Rejeté"),
            ("blocked", "Bloqué"),
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
        help="Timestamp brut en millisecondes conservé du XML",
    )
    duration = fields.Integer(
        string="Durée (s)",
        default=0,
    )
    duration_display = fields.Char(
        string="Durée",
        compute="_compute_duration_display",
        store=True,
    )
    contact_name = fields.Char(
        string="Nom du contact",
    )
    owner_id = fields.Many2one(
        related="thread_id.owner_id",
        store=True,
        index=True,
        string="Propriétaire",
    )
    import_batch_id = fields.Char(
        string="Lot d'import",
        help="Identifiant backup_set du fichier XML",
    )
    presentation = fields.Selection(
        selection=[
            ("allowed", "Autorisé"),
            ("restricted", "Restreint"),
            ("unknown", "Inconnu"),
            ("payphone", "Téléphone public"),
        ],
        string="Présentation",
    )
    recording_url = fields.Char(
        string="Enregistrement",
        help="Lien interne Nextcloud vers le fichier audio de l'appel",
    )

    display_name = fields.Char(
        compute="_compute_display_name",
    )

    _sql_constraints = [
        (
            "hash_uniq",
            "UNIQUE(call_hash)",
            "Cet appel existe déjà (hash dupliqué).",
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

        thread = Thread._get_or_create(phone_norm, owner_id, phone_raw, contact_name)
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
