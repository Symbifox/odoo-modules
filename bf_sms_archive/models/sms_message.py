import hashlib
import logging
import re
import unicodedata
from datetime import datetime, timezone

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _ms_to_naive_utc(date_ms):
    """Convert millisecond epoch (int/str) to naive UTC datetime (Odoo convention)."""
    ms = int(date_ms)
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(tzinfo=None)

# SMS Backup & Restore type mapping
_SMS_TYPE_MAP = {
    "1": "in",     # Received
    "2": "out",    # Sent
    "3": "draft",  # Draft
}

_DIRECTION_LABELS = {
    "in": "←",
    "out": "→",
    "draft": "~",
}


class SmsArchiveMessage(models.Model):
    _name = "sms.archive.message"
    _description = "Message SMS archivé"
    _order = "date_sent asc, id asc"

    thread_id = fields.Many2one(
        comodel_name="sms.archive.thread",
        string="Conversation",
        required=True,
        ondelete="cascade",
        index=True,
    )
    message_hash = fields.Char(
        string="Hash de dédoublonnage",
        size=64,
        required=True,
        index=True,
    )
    direction = fields.Selection(
        selection=[
            ("in", "Reçu"),
            ("out", "Envoyé"),
            ("draft", "Brouillon"),
        ],
        string="Direction",
        required=True,
    )
    body = fields.Text(
        string="Contenu",
    )
    date_sent = fields.Datetime(
        string="Date",
        required=True,
        index=True,
    )
    date_sent_ms = fields.Char(
        string="Timestamp (ms)",
        help="Timestamp brut en millisecondes conservé du XML",
    )
    is_mms = fields.Boolean(
        string="MMS",
        default=False,
    )
    is_read = fields.Boolean(
        string="Lu",
        default=True,
        help="Faux pour un message entrant live non encore consulté (compteur systray).",
    )
    delivery_state = fields.Selection(
        selection=[
            ("queued", "En file"),
            ("sent", "Envoyé"),
            ("failed", "Échec"),
            ("received", "Reçu"),
        ],
        string="État de livraison",
        help="Pour les messages sortants live. « sent » = accepté par l'API VOIP.ms "
             "(VOIP.ms ne fournit pas d'accusé de livraison fiable).",
    )
    voipms_id = fields.Char(
        string="ID VOIP.ms",
        index=True,
        copy=False,
        help="Identifiant du message côté VOIP.ms (déduplication live).",
    )
    line_id = fields.Many2one(
        comodel_name="sms.archive.line",
        string="Ligne",
        ondelete="set null",
        help="Ligne (DID) VOIP.ms utilisée pour ce message live.",
    )
    error = fields.Text(
        string="Erreur d'envoi",
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
    mms_part_ids = fields.One2many(
        comodel_name="sms.archive.mms.part",
        inverse_name="message_id",
        string="Pièces jointes MMS",
    )

    display_name = fields.Char(
        compute="_compute_display_name",
    )

    _sql_constraints = [
        (
            "hash_uniq",
            "UNIQUE(message_hash)",
            "Ce message existe déjà (hash dupliqué).",
        ),
    ]

    @api.depends("direction", "thread_id.contact_name", "date_sent", "body")
    def _compute_display_name(self):
        for msg in self:
            arrow = _DIRECTION_LABELS.get(msg.direction, "?")
            contact = msg.thread_id.contact_name or msg.thread_id.phone_normalized or "?"
            dt = str(msg.date_sent)[:16] if msg.date_sent else ""
            preview = (msg.body or "")[:40]
            if len(msg.body or "") > 40:
                preview += "…"
            msg.display_name = f"{arrow} {contact} — {dt} — {preview}"

    def action_post_to_task(self):
        """Open wizard to post the selected SMS message(s) to a project task."""
        if not self:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": "Poster sur une tâche",
            "res_model": "sms.archive.post.to.task.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_message_ids": [(6, 0, self.ids)],
            },
        }

    def action_export_csv(self):
        """Export the selected messages as a single CSV file."""
        if not self:
            return False
        filename = f"sms_selection_{fields.Date.today()}.csv"
        res_id = self.thread_id.id if len(self.thread_id) == 1 else False
        return self.env["sms.archive.thread"]._build_csv_download(
            self, filename=filename, res_id=res_id,
        )

    def action_export_xml(self):
        """Export the selected messages as a single SMS Backup & Restore XML file."""
        if not self:
            return False
        filename = f"sms_selection_{fields.Date.today()}.xml"
        res_id = self.thread_id.id if len(self.thread_id) == 1 else False
        return self.env["sms.archive.thread"]._build_xml_download(
            self, filename=filename, res_id=res_id,
        )

    def action_export_pdf(self):
        """Print the selected messages as a branded PDF."""
        if not self:
            return False
        return self.env.ref(
            "bf_sms_archive.action_report_sms_messages"
        ).report_action(self)

    def _get_messages_report_data(self):
        """Prepare data for the per-message PDF report (binding_model: sms.archive.message)."""
        Thread = self.env["sms.archive.thread"]
        data = Thread._build_shared_report_data()

        threads = self.thread_id.sorted(
            lambda t: (t.last_message_date or fields.Datetime.from_string("1970-01-01")),
            reverse=True,
        )
        groups = []
        for thread in threads:
            thread_msgs = self.filtered(lambda m, t=thread: m.thread_id.id == t.id)
            if thread_msgs:
                groups.append(Thread._build_thread_group(thread, thread_msgs))

        data["groups"] = groups
        data["total_count"] = sum(g["count"] for g in groups)
        data["thread_count"] = len(groups)
        return data

    @api.model
    def _ingest_one(self, *, phone_raw, owner_id, direction, body, date_ms,
                    contact_name=None, is_mms=False, parts=None, batch_id=None,
                    voipms_id=None, delivery_state=None, line_id=None, is_read=None):
        """Single-message ingestion with dedup. Returns (record, created: bool).

        ``parts`` is an optional list of {content_type, filename, data_b64, text}
        forwarded to ``sms.archive.mms.part._ingest_parts`` after the message is created.

        Live-specific optional kwargs (webhook / poller): ``voipms_id`` (preferred
        dedup key when present), ``delivery_state``, ``line_id`` and ``is_read``.
        """
        Thread = self.env["sms.archive.thread"].sudo()
        phone_norm = Thread.normalize_phone(phone_raw)
        body_text = body or ""

        # Préférer l'id VOIP.ms (stable) pour la déduplication quand disponible.
        if voipms_id:
            existing = self.sudo().search([("voipms_id", "=", str(voipms_id))], limit=1)
            if existing:
                return existing, False

        msg_hash = hashlib.sha256(
            f"{phone_norm}|{date_ms}|{body_text}".encode("utf-8")
        ).hexdigest()

        existing = self.sudo().search([("message_hash", "=", msg_hash)], limit=1)
        if existing:
            if voipms_id and not existing.voipms_id:
                existing.sudo().write({"voipms_id": str(voipms_id)})
            return existing, False

        thread = Thread._get_or_create(phone_norm, owner_id, phone_raw, contact_name)
        vals = {
            "thread_id": thread.id,
            "message_hash": msg_hash,
            "direction": direction,
            "body": body_text,
            "date_sent": _ms_to_naive_utc(date_ms),
            "date_sent_ms": str(date_ms),
            "is_mms": bool(is_mms),
            "contact_name": contact_name or "",
            "import_batch_id": batch_id or "android-live",
        }
        if voipms_id:
            vals["voipms_id"] = str(voipms_id)
        if delivery_state:
            vals["delivery_state"] = delivery_state
        if line_id:
            vals["line_id"] = line_id
        if is_read is not None:
            vals["is_read"] = is_read
        rec = self.sudo().create(vals)
        if parts:
            self.env["sms.archive.mms.part"].sudo()._ingest_parts(rec.id, parts)
        return rec, True

    # ── Envoi live (SMS/MMS via VOIP.ms) ───────────────────────────

    # Enveloppe d'un SMS unique côté VOIP.ms : 160 « unités » en GSM-7,
    # 70 caractères dès qu'on bascule en UCS-2 (au-delà → « sms_toolong »).
    # ⚠️ Le contrôle de longueur de VOIP.ms compte les OCTETS UTF-8, pas les
    # septets GSM-7 : é/è/à/ù (2 octets) comptent double même s'ils tiennent
    # dans un septet. Empirique (2026-07-23) : segment de 160 octets accepté,
    # 161 octets refusé (« sms_toolong »).
    _SMS_GSM7_LEN = 160
    _SMS_UCS2_LEN = 70

    # Alphabet GSM 7 bits (3GPP TS 23.038) : caractères tenant dans un SMS
    # « standard ». Tout caractère absent des deux tables force l'UCS-2.
    _GSM7_BASIC = frozenset(
        "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./"
        "0123456789:;<=>?¡"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿"
        "abcdefghijklmnopqrstuvwxyzäöñüà"
    )
    # Table d'extension : chacun coûte 2 septets (ESC + caractère).
    _GSM7_EXT = frozenset("^{}\\[~]|€")

    # Translittération des caractères typographiques courants vers leur
    # équivalent GSM-7. Objectif : ne PAS basculer tout le message en UCS-2
    # (budget 70 car./SMS au lieu de 160) juste à cause d'une apostrophe
    # courbe, d'un guillemet « », de points de suspension ou d'un tiret long.
    # N.B. : € n'est PAS ici (il est en table d'extension GSM-7).
    _GSM7_TRANSLIT = {
        "‘": "'", "’": "'", "‚": "'", "′": "'",   # ‘ ’ ‚ ′
        "‛": "'", "´": "'", "`": "'",                   # ‛ ´ `
        "“": '"', "”": '"', "„": '"', "″": '"',   # “ ” „ ″
        "«": '"', "»": '"', "‹": "<", "›": ">",   # « » ‹ ›
        "–": "-", "—": "-", "―": "-", "−": "-",   # – — ― −
        "…": "...",                                               # …
        " ": " ", " ": " ", " ": " ", " ": " ",   # nbsp, nnbsp, thin, figure
        "​": "", "‌": "", "‍": "", "﻿": "",       # zwsp, zwnj, zwj, bom
        "Œ": "OE", "œ": "oe",                                # Œ œ
        "•": "-", "·": ".", "‧": ".", "∙": ".",   # • · ‧ ∙
        "⁄": "/", "≤": "<=", "≥": ">=", "≠": "!=", # ⁄ ≤ ≥ ≠
        "½": "1/2", "¼": "1/4", "¾": "3/4",             # ½ ¼ ¾
        "™": "(TM)", "®": "(R)", "©": "(C)",            # ™ ® ©
    }

    @classmethod
    def _is_gsm7(cls, text):
        """True si ``text`` s'encode intégralement en GSM 7 bits."""
        return all(
            (c in cls._GSM7_BASIC or c in cls._GSM7_EXT) for c in (text or "")
        )

    @classmethod
    def _split_segments(cls, text):
        """Segmente ``text`` selon l'encodage réel du message.

        - GSM-7 (ASCII + accents de base) : 160 unités/segment, chaque
          caractère coûtant ``max(septets GSM-7, octets UTF-8)`` — les
          caractères d'extension ``^{}\\[~]|€`` comptent double (septets),
          et les lettres accentuées GSM-7 (é è à ù ì ò ä ö ñ ü…) comptent
          double aussi car le contrôle de longueur de VOIP.ms est en octets
          UTF-8 (161 octets → « sms_toolong », vécu 2026-07-23).
        - UCS-2 (dès qu'un caractère hors GSM-7 apparaît : ``œ``, ``’``,
          ``…``, ``À``/``È``, emoji…) : 70 caractères/segment.

        Chaque segment tient donc dans un SMS unique → plus de
        ``sms_toolong`` sur les messages accentués.
        """
        text = text or ""
        if not text:
            return [""]
        if not cls._is_gsm7(text):
            size = cls._SMS_UCS2_LEN
            return [text[i:i + size] for i in range(0, len(text), size)]
        # GSM-7 : empaquetage caractère par caractère (un caractère n'est
        # jamais scindé, donc une paire d'extension reste dans le même
        # segment).
        segments, current, cost = [], [], 0
        for ch in text:
            septets = 2 if ch in cls._GSM7_EXT else 1
            ch_cost = max(septets, len(ch.encode("utf-8")))
            if cost + ch_cost > cls._SMS_GSM7_LEN:
                segments.append("".join(current))
                current, cost = [], 0
            current.append(ch)
            cost += ch_cost
        if current:
            segments.append("".join(current))
        return segments

    @classmethod
    def _gsm7_fallback_char(cls, ch, flatten_accents=True):
        """Meilleur équivalent GSM-7 d'un caractère hors table, sinon ``''``.

        Ordre :
          1. Ponctuation typographique mappée (``_GSM7_TRANSLIT``) → toujours.
          2. Lettre accentuée hors GSM-7 (ê â î ô û ë ï ÿ…) → lettre de base
             par décomposition Unicode NFKD, MAIS seulement si
             ``flatten_accents`` (sinon on conserve la lettre telle quelle,
             au prix de l'UCS-2).
          3. Reste (emoji, symbole exotique) → ``''`` (retiré).
        """
        if ch in cls._GSM7_TRANSLIT:
            return cls._GSM7_TRANSLIT[ch]
        if unicodedata.category(ch).startswith("L"):
            if not flatten_accents:
                return ch  # lettre conservée exacte → force UCS-2, assumé
            base = "".join(
                c for c in unicodedata.normalize("NFKD", ch)
                if not unicodedata.combining(c)
            )
            base = "".join(
                c for c in base if c in cls._GSM7_BASIC or c in cls._GSM7_EXT
            )
            return base  # '' si la décomposition ne donne rien d'exploitable
        return ""

    @classmethod
    def _normalize_gsm7(cls, text, flatten_accents=True):
        """Ramène ``text`` dans l'alphabet GSM-7 quand c'est possible.

        Un SMS tient dans 160 caractères en GSM-7, mais le budget tombe à 70
        dès qu'UN SEUL caractère hors GSM-7 apparaît (bascule UCS-2). Un simple
        emoji (⚡ 😊) ou une apostrophe courbe suffit alors à fragmenter un
        message court en plusieurs SMS distincts (VOIP.ms n'assemble pas les
        segments → le destinataire reçoit plusieurs messages).

        Cette normalisation :
          - translittère la ponctuation typographique (’ « » … —, nbsp, œ…) ;
          - avec ``flatten_accents`` (défaut) : aplatit les lettres accentuées
            hors GSM-7 (ê→e, â→a…) — les accents GSM-7 (é è à ç ä ö ñ ü…) sont
            TOUJOURS préservés ;
          - retire les caractères sans équivalent 7 bits (emoji, symboles).

        Le texte déjà GSM-7 est renvoyé tel quel (chemin nominal, aucun coût).
        """
        text = text or ""
        if cls._is_gsm7(text):
            return text
        out = []
        for ch in text:
            if ch in cls._GSM7_BASIC or ch in cls._GSM7_EXT:
                out.append(ch)
            else:
                out.append(cls._gsm7_fallback_char(ch, flatten_accents))
        normalized = "".join(out)
        # Filet : garantir GSM-7 quand flatten_accents force l'aplatissement
        # (si un caractère restait hors table, on le retire).
        if flatten_accents and not cls._is_gsm7(normalized):
            normalized = "".join(
                c for c in normalized
                if c in cls._GSM7_BASIC or c in cls._GSM7_EXT
            )
        # Les caractères retirés laissent parfois une double espace : on tasse
        # les suites d'espaces horizontales, puis on enlève celles en fin de
        # ligne (avant un \n) et aux extrémités — sans toucher aux sauts de
        # ligne eux-mêmes.
        normalized = re.sub(r"[^\S\n]{2,}", " ", normalized)
        normalized = re.sub(r"[^\S\n]+(?=\n)", "", normalized)
        return normalized.strip()

    @api.model
    def _gsm7_normalize_enabled(self):
        """Interrupteur (ICP ``bf_sms_archive.gsm7_normalize``, défaut ON).

        Mettre à ``0`` réactive l'envoi UCS-2 tel quel (emoji conservés, au
        prix de la fragmentation).
        """
        val = self.env["ir.config_parameter"].sudo().get_param(
            "bf_sms_archive.gsm7_normalize", "1")
        return (val or "").strip().lower() in ("1", "true", "yes", "on", "t")

    @api.model
    def _gsm7_flatten_accents_enabled(self):
        """Aplatir les lettres à accent circonflexe/tréma hors GSM-7 (ê→e…) ?

        ICP ``bf_sms_archive.gsm7_flatten_accents``, **défaut OFF** (choix
        choix par défaut : garder les accents exacts). À ``1``, ces lettres
        sont aplaties pour éviter la bascule UCS-2 (donc la fragmentation) des
        messages qui en contiennent. Les accents déjà GSM-7 (é è à ç ä ö ñ ü…)
        sont préservés dans tous les cas, quel que soit ce réglage.
        """
        val = self.env["ir.config_parameter"].sudo().get_param(
            "bf_sms_archive.gsm7_flatten_accents", "0")
        return (val or "").strip().lower() in ("1", "true", "yes", "on", "t")

    @api.model
    def _mms_escalation_min_segments(self):
        """Seuil (nombre de segments SMS) à partir duquel on bascule en MMS.

        Réglable via l'ICP ``bf_sms_archive.mms_escalation_min_segments``
        (défaut 2 → tout message qui ne tient pas dans un seul SMS part en
        MMS). Une valeur < 2 désactive l'escalade (envoi SMS segmenté).
        """
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "bf_sms_archive.mms_escalation_min_segments", "2")
        try:
            threshold = int(raw)
        except (TypeError, ValueError):
            threshold = 2
        return threshold if threshold >= 2 else 10 ** 9

    @staticmethod
    def _to_voipms_dst(e164):
        """Numéro destinataire au format VOIP.ms (chiffres, NANP → 10)."""
        digits = re.sub(r"\D", "", e164 or "")
        if len(digits) == 11 and digits.startswith("1"):
            return digits[1:]
        return digits

    @staticmethod
    def _media_to_datauri(media):
        """{content_type, data_b64} → data URI base64 pour sendMMS."""
        ct = media.get("content_type") or "application/octet-stream"
        return f"data:{ct};base64,{media.get('data_b64') or ''}"

    @api.model
    def action_send(self, line_id, dst, body, media=None):
        """Envoie un SMS/MMS depuis ``line_id`` vers ``dst``.

        ``media`` : liste optionnelle de {filename, content_type, data_b64} → MMS.
        Crée un message sortant (avec son état de livraison) et retourne son id.
        """
        Line = self.env["sms.archive.line"].sudo()
        line = Line.browse(int(line_id))
        if not line.exists():
            raise UserError("Ligne d'envoi introuvable.")
        if line.owner_id.id != self.env.uid and not self.env.user.has_group(
            "bf_sms_archive.group_sms_manager"
        ):
            raise UserError("Cette ligne ne vous appartient pas.")

        Thread = self.env["sms.archive.thread"].sudo()
        Voipms = self.env["sms.archive.voipms"]
        dst_norm = Thread.normalize_phone(dst)
        if not dst_norm:
            raise UserError("Numéro destinataire invalide.")
        thread = Thread._get_or_create(dst_norm, line.owner_id.id, dst, None)

        now = fields.Datetime.now()
        date_ms = int(now.replace(tzinfo=timezone.utc).timestamp() * 1000)
        did = Line._voipms_did_value(line.did)
        dst_api = self._to_voipms_dst(dst_norm)
        is_mms = bool(media)
        # Corps réellement transmis. Sur le chemin SMS (aucun média), on
        # normalise vers GSM-7 pour qu'un message court reste UN seul segment
        # plutôt que de se fragmenter en plusieurs SMS distincts (un simple
        # emoji ou une apostrophe courbe suffirait à basculer en UCS-2, budget
        # 70 car.). Le vrai MMS (avec média) conserve l'Unicode tel quel.
        send_body = body or ""
        if not is_mms and self._gsm7_normalize_enabled():
            send_body = self._normalize_gsm7(
                send_body,
                flatten_accents=self._gsm7_flatten_accents_enabled(),
            )
        segments = self._split_segments(send_body)

        # Escalade « intelligente » : un texte qui ne tient pas dans un seul
        # SMS (long, ou riche en Unicode → enveloppe UCS-2 de 70 caractères)
        # part en un unique MMS plutôt qu'en plusieurs SMS fragmentés. Repli
        # automatique sur le découpage SMS si l'escalade échoue.
        want_mms_escalation = (
            not is_mms
            and line.mms_enabled
            and len(segments) >= self._mms_escalation_min_segments()
        )

        delivery_state = "sent"
        error = False
        voipms_id = ""
        try:
            if is_mms:
                if not line.mms_enabled:
                    raise UserError("Ligne non activée pour les MMS.")
                medias = [self._media_to_datauri(m) for m in media][:3]
                voipms_id = Voipms._voipms_send_mms(did, dst_api, body or "", medias)
            else:
                if not line.sms_enabled:
                    raise UserError("Ligne non activée pour les SMS.")
                sent_via_mms = False
                if want_mms_escalation:
                    try:
                        voipms_id = Voipms._voipms_send_mms(
                            did, dst_api, send_body, [])
                        is_mms = True
                        sent_via_mms = True
                    except Exception as e:  # noqa: BLE001 — repli SMS segmenté
                        _logger.info(
                            "Escalade MMS refusée (ligne=%s) → repli SMS "
                            "segmenté : %s",
                            line.id, Voipms._voipms_redact(str(e)),
                        )
                if not sent_via_mms:
                    ids = []
                    for seg in segments:
                        ids.append(Voipms._voipms_send_sms(did, dst_api, seg))
                    voipms_id = ",".join(x for x in ids if x)
        except Exception as e:  # noqa: BLE001 — on journalise l'état, on ne casse pas l'UI
            delivery_state = "failed"
            error = Voipms._voipms_redact(str(e))
            _logger.warning(
                "Envoi SMS/MMS échoué (ligne=%s dst=%s) : %s", line.id, dst_norm, error,
            )

        # On archive/hash le corps réellement transmis (normalisé le cas
        # échéant), pour que le fil « Messagerie » reflète ce que le
        # destinataire a reçu.
        msg_hash = hashlib.sha256(
            f"{dst_norm}|{date_ms}|{send_body}|out|{voipms_id}".encode("utf-8")
        ).hexdigest()
        rec = self.sudo().create({
            "thread_id": thread.id,
            "message_hash": msg_hash,
            "direction": "out",
            "body": send_body,
            "date_sent": now,
            "date_sent_ms": str(date_ms),
            "is_mms": is_mms,
            "is_read": True,
            "line_id": line.id,
            "voipms_id": voipms_id or False,
            "delivery_state": delivery_state,
            "error": error or False,
            "import_batch_id": "voipms-live",
        })
        if is_mms and media:
            parts = [{
                "content_type": m.get("content_type"),
                "filename": m.get("filename"),
                "data_b64": m.get("data_b64"),
            } for m in media]
            self.env["sms.archive.mms.part"].sudo()._ingest_parts(rec.id, parts)
        rec._notify_bus(kind="out")
        return rec.id

    def _messenger_dict(self):
        """Sérialisation d'un message pour la SPA « Messagerie »."""
        self.ensure_one()
        media = []
        if self.is_mms:
            for part in self.mms_part_ids:
                if part.attachment_id:
                    att = part.attachment_id
                    media.append({
                        "filename": part.filename or "fichier",
                        "content_type": part.content_type or "",
                        "is_image": part.is_image,
                        "url": (
                            "/web/image/%d" % att.id if part.is_image
                            else "/web/content/%d?download=true" % att.id
                        ),
                    })
                elif part.text_content:
                    media.append({
                        "filename": part.filename or "texte",
                        "content_type": part.content_type or "text/plain",
                        "is_image": False,
                        "text": part.text_content,
                    })
        return {
            "id": self.id,
            "direction": self.direction,
            "body": self.body or "",
            "date_ms": int(self.date_sent_ms) if (self.date_sent_ms or "").isdigit()
            else self.thread_id._dt_to_ms(self.date_sent),
            "is_mms": self.is_mms,
            "delivery_state": self.delivery_state or "",
            "error": self.error or "",
            "media": media,
        }

    def _notify_bus(self, kind="new"):
        """Pousse une notification temps réel au propriétaire (systray + SPA).

        Cible le canal du *partenaire* propriétaire (ACL par utilisateur côté bus),
        jamais un canal-chaîne devinable : aucune fuite inter-utilisateurs.
        """
        for msg in self:
            owner = msg.owner_id or msg.thread_id.owner_id
            if not owner or not owner.partner_id:
                continue
            thread = msg.thread_id
            payload = {
                "kind": kind,  # "new" (entrant) | "out" (sortant)
                "thread_id": thread.id,
                "message_id": msg.id,
                "direction": msg.direction,
                "delivery_state": msg.delivery_state or "",
                "contact": thread.contact_name or thread.phone_normalized or "",
                "preview": (msg.body or "")[:120],
                "date_ms": msg.date_sent_ms or "",
            }
            self.env["bus.bus"]._sendone(
                owner.partner_id, "sms.archive/new", payload,
            )
            # Web Push hors bus : réveille le téléphone même navigateur fermé.
            # Uniquement pour un entrant non lu ; jamais bloquant pour l'ingestion.
            if kind == "new" and msg.direction == "in":
                try:
                    self.env["sms.archive.push.subscription"]._notify_new_message(msg)
                except Exception:
                    _logger.warning(
                        "Web Push non envoyé (fil %s).", thread.id, exc_info=True,
                    )
                # Push UnifiedPush/ntfy vers l'app Android native (sans Google).
                try:
                    self.env["sms.archive.unifiedpush"]._notify_new_message(msg)
                except Exception:
                    _logger.warning(
                        "Push UnifiedPush non envoyé (fil %s).", thread.id, exc_info=True,
                    )
