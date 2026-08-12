import base64
import hashlib
import io
import logging
import os
import shutil
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import quote as url_quote
from xml.etree.ElementTree import tostring as _et_tostring

import requests as http_requests
from defusedxml.ElementTree import iterparse

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# SMS Backup & Restore type mapping
_SMS_TYPE_MAP = {
    "1": "in",
    "2": "out",
    "3": "draft",
}

# MMS msg_box mapping
_MMS_BOX_MAP = {
    "1": "in",     # Received
    "2": "out",    # Sent
    "3": "draft",  # Draft
}

# Call type mapping
_CALL_TYPE_MAP = {
    "1": "incoming",
    "2": "outgoing",
    "3": "missed",
    "4": "voicemail",
    "5": "rejected",
    "6": "blocked",
}

# Call presentation mapping
_PRESENTATION_MAP = {
    "1": "allowed",
    "2": "restricted",
    "3": "unknown",
    "4": "payphone",
}

# MIME types to skip (SMIL layout markup, not real content)
_SKIP_MIME = {"application/smil"}

# Max file size: 1 GB (SMS Backup & Restore exports can exceed 600 MB)
# Applies to the form upload path, which goes through fields.Binary
# (base64 + in-memory decode). For larger files, drop them in
# /mnt/sms-inbox on the host and use the disk import cron instead.
_MAX_FILE_SIZE = 1024 * 1024 * 1024
# Direct-import threshold for the Nextcloud watch cron (≤ this size, the file
# is downloaded fully in memory, base64-encoded, and passed to the wizard;
# 200 MB raw ≈ 270 MB b64 ≈ 470 MB total).
_NC_WATCH_DIRECT_MAX = 200 * 1024 * 1024
# Above _NC_WATCH_DIRECT_MAX, the file is streamed to disk in /mnt/sms-inbox
# and split into chunks of this size before per-chunk import.
_CHUNK_MAX_BYTES = 80 * 1024 * 1024
_BATCH_SIZE = 500

# Disk-based import folder (mounted via docker-compose, no size limit).
_DISK_INBOX = "/mnt/sms-inbox"
# Subfolder under _DISK_INBOX used as scratch space for stream-download + split.
_SPLIT_STAGING_SUBDIR = ".split-staging"

# XML wrapper templates for split chunks — the import wizard expects a
# complete XML document per chunk.
_SMS_CHUNK_HEADER = (
    b"<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n"
    b'<smses count="{count}" type="full">\n'
)
_SMS_CHUNK_FOOTER = b"</smses>\n"
_CALL_CHUNK_HEADER = (
    b"<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n"
    b'<calls count="{count}" type="full">\n'
)
_CALL_CHUNK_FOOTER = b"</calls>\n"


class SmsArchiveImportWizard(models.TransientModel):
    _name = "sms.archive.import.wizard"
    _description = "Import de sauvegarde SMS"

    file_data = fields.Binary(
        string="Fichier XML ou ZIP",
        required=True,
    )
    file_name = fields.Char(
        string="Nom du fichier",
    )
    result_message = fields.Text(
        string="Résultat",
        readonly=True,
    )

    @staticmethod
    def _detect_file_type(stream):
        """Detect whether XML is SMS (<smses>) or call log (<calls>).

        Peeks at the first 500 bytes of the stream then rewinds.
        Returns 'sms' or 'calls'.
        """
        head = stream.read(500).lower()
        stream.seek(0)
        if b"<calls" in head:
            return "calls"
        return "sms"

    def action_import(self):
        """Import SMS or call logs from uploaded XML or ZIP file."""
        self.ensure_one()
        if not self.file_data:
            raise UserError(_("Veuillez sélectionner un fichier."))

        raw = base64.b64decode(self.file_data)
        if len(raw) > _MAX_FILE_SIZE:
            raise UserError(_(
                "Le fichier dépasse la limite de %d Mo pour l'import par "
                "formulaire. Pour les fichiers plus volumineux, déposez-les "
                "dans /mnt/sms-inbox côté serveur (voir README)."
            ) % (_MAX_FILE_SIZE // (1024 * 1024)))

        fname = (self.file_name or "").lower()
        if fname.endswith(".zip"):
            xml_files = self._extract_xmls_from_zip(raw)
        elif fname.endswith(".xml"):
            xml_files = [(self.file_name, raw)]
        else:
            raise UserError(_("Format non supporté. Utilisez un fichier XML ou ZIP."))

        all_sms_stats = []
        all_call_stats = []

        for xml_name, xml_data in xml_files:
            stream = io.BytesIO(xml_data)
            file_type = self._detect_file_type(stream)
            if file_type == "calls":
                stats = self._parse_and_import_calls(stream)
                all_call_stats.append(stats)
            else:
                stats = self._parse_and_import(stream)
                all_sms_stats.append(stats)

        lines = ["Import terminé :"]
        if all_sms_stats:
            sms = self._merge_stats(all_sms_stats)
            lines.append(f"\n  --- SMS/MMS ---")
            lines.append(f"  Messages traités : {sms['processed']}")
            lines.append(f"  Nouveaux messages : {sms['created']}")
            lines.append(f"  Doublons ignorés : {sms['duplicates']}")
            lines.append(f"  Fils de conversation : {sms['threads']}")
            lines.append(f"  Contacts Odoo liés : {sms['partners_matched']}")
            lines.append(f"  Pièces jointes MMS : {sms['attachments']}")
        if all_call_stats:
            calls = self._merge_stats(all_call_stats)
            lines.append(f"\n  --- Appels ---")
            lines.append(f"  Appels traités : {calls['processed']}")
            lines.append(f"  Nouveaux appels : {calls['created']}")
            lines.append(f"  Doublons ignorés : {calls['duplicates']}")
            lines.append(f"  Fils de conversation : {calls['threads']}")
            lines.append(f"  Contacts Odoo liés : {calls['partners_matched']}")

        self.result_message = "\n".join(lines)

        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    @staticmethod
    def _merge_stats(stats_list):
        """Merge multiple stats dicts by summing values."""
        merged = {}
        for stats in stats_list:
            for key, val in stats.items():
                merged[key] = merged.get(key, 0) + val
        return merged

    def _extract_xmls_from_zip(self, zip_bytes):
        """Extract all XML files from a ZIP archive.

        Returns a list of (filename, xml_bytes) tuples.
        """
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
                if not xml_names:
                    raise UserError(_("Aucun fichier XML trouvé dans le ZIP."))
                results = []
                for name in xml_names:
                    uncompressed_size = zf.getinfo(name).file_size
                    if uncompressed_size > _MAX_FILE_SIZE:
                        _logger.warning(
                            "ZIP entry %s too large (%d bytes), skipping",
                            name, uncompressed_size,
                        )
                        continue
                    results.append((name, zf.read(name)))
                if not results:
                    raise UserError(_("Tous les fichiers XML du ZIP dépassent la limite de taille."))
                return results
        except zipfile.BadZipFile:
            raise UserError(_("Le fichier ZIP est corrompu."))

    @staticmethod
    def _extract_mms_parts(elem):
        """Extract text body and binary parts from MMS <parts>."""
        parts_elem = elem.find("parts")
        if parts_elem is None:
            return "", []

        texts = []
        binary_parts = []

        for part in parts_elem.findall("part"):
            ct = part.get("ct", "")
            seq = int(part.get("seq", "0") or "0")

            if ct in _SKIP_MIME:
                continue

            if ct == "text/plain":
                text = part.get("text", "")
                if text:
                    texts.append(text)
            elif part.get("data"):
                # Binary attachment (image, video, etc.)
                filename = part.get("cl") or part.get("fn") or part.get("name")
                if filename in (None, "null", ""):
                    # Generate filename from content type
                    ext = ct.split("/")[-1] if "/" in ct else "bin"
                    ext = ext.replace("jpeg", "jpg")
                    filename = f"mms_part.{ext}"

                binary_parts.append({
                    "content_type": ct,
                    "filename": filename,
                    "data_b64": part.get("data", ""),
                    "sequence": seq,
                })

        return "\n".join(texts), binary_parts

    def _parse_and_import(self, stream):
        """Stream-parse XML and import SMS + MMS messages.

        `stream` is any file-like object (BytesIO for in-memory paths,
        or an open file handle for disk-based imports of huge XMLs).
        """
        Thread = self.env["sms.archive.thread"]
        Message = self.env["sms.archive.message"]
        normalize = Thread.normalize_phone
        owner_id = self.env.uid

        # Pre-load existing hashes for dedup
        existing_hashes = set(
            Message.search([("owner_id", "=", owner_id)]).mapped("message_hash")
        )

        # Pre-load existing threads
        existing_threads = {}
        for t in Thread.search([("owner_id", "=", owner_id)]):
            existing_threads[t.phone_normalized] = t.id

        stats = {
            "processed": 0,
            "created": 0,
            "duplicates": 0,
            "threads": 0,
            "partners_matched": 0,
            "attachments": 0,
        }

        batch = []
        new_threads = {}  # phone_norm -> {contact_name, phone_raw}
        backup_set = ""

        for event, elem in iterparse(stream, events=("end",)):
            # Capture backup_set from root <smses> tag
            if elem.tag == "smses":
                backup_set = elem.get("backup_set", "")
                continue

            if elem.tag == "sms":
                address = elem.get("address", "")
                body = elem.get("body", "")
                date_ms = elem.get("date", "0")
                sms_type = elem.get("type", "1")
                contact = elem.get("contact_name", "")
                direction = _SMS_TYPE_MAP.get(sms_type, "in")
                is_mms = False
                mms_parts = []

            elif elem.tag == "mms":
                address = elem.get("address", "")
                body, mms_parts = self._extract_mms_parts(elem)
                date_ms = elem.get("date", "0")
                msg_box = elem.get("msg_box", "1")
                contact = elem.get("contact_name", "")
                direction = _MMS_BOX_MAP.get(msg_box, "in")
                is_mms = True

            else:
                continue

            stats["processed"] += 1

            phone_norm = normalize(address)
            if not phone_norm:
                elem.clear()
                continue

            # Dedup hash
            msg_hash = hashlib.sha256(
                f"{phone_norm}|{date_ms}|{body}".encode()
            ).hexdigest()

            if msg_hash in existing_hashes:
                stats["duplicates"] += 1
                elem.clear()
                continue

            existing_hashes.add(msg_hash)

            # Track thread info
            if phone_norm not in existing_threads and phone_norm not in new_threads:
                new_threads[phone_norm] = {
                    "contact_name": contact,
                    "phone_raw": address,
                }
            elif phone_norm in new_threads and contact and contact != "(Unknown)":
                new_threads[phone_norm]["contact_name"] = contact

            # Convert timestamp (Odoo expects naive UTC datetimes)
            try:
                ts = int(date_ms) / 1000
                dt = datetime.utcfromtimestamp(ts)
            except (ValueError, OSError):
                dt = fields.Datetime.now()

            batch.append({
                "phone_norm": phone_norm,
                "msg_hash": msg_hash,
                "direction": direction,
                "body": body,
                "date_sent": dt,
                "date_sent_ms": date_ms,
                "contact_name": contact,
                "import_batch_id": backup_set,
                "is_mms": is_mms,
                "mms_parts": mms_parts,
            })

            if len(batch) >= _BATCH_SIZE:
                created, atts = self._flush_batch(
                    batch, new_threads, existing_threads, owner_id,
                )
                stats["created"] += created
                stats["attachments"] += atts
                batch = []

            elem.clear()

        # Flush remaining
        if batch:
            created, atts = self._flush_batch(
                batch, new_threads, existing_threads, owner_id,
            )
            stats["created"] += created
            stats["attachments"] += atts

        stats["threads"] = len(existing_threads)

        # Match partners
        matched = self._match_partners(existing_threads, owner_id)
        stats["partners_matched"] = matched

        return stats

    def _flush_batch(self, batch, new_threads, existing_threads, owner_id):
        """Create threads and messages for a batch. Returns (created, attachments)."""
        Thread = self.env["sms.archive.thread"]
        Message = self.env["sms.archive.message"]
        MmsPart = self.env["sms.archive.mms.part"]
        Attachment = self.env["ir.attachment"]

        # Create any new threads needed by this batch
        phones_needed = {
            m["phone_norm"] for m in batch
        } - set(existing_threads.keys())

        for phone in phones_needed:
            info = new_threads.get(phone, {})
            thread = Thread.create({
                "phone_normalized": phone,
                "phone_raw": info.get("phone_raw", phone),
                "contact_name": info.get("contact_name", ""),
                "owner_id": owner_id,
            })
            existing_threads[phone] = thread.id

        # Create messages one by one (need IDs for MMS parts)
        created = 0
        att_count = 0
        for m in batch:
            thread_id = existing_threads.get(m["phone_norm"])
            if not thread_id:
                continue
            msg = Message.create({
                "thread_id": thread_id,
                "message_hash": m["msg_hash"],
                "direction": m["direction"],
                "body": m["body"],
                "date_sent": m["date_sent"],
                "date_sent_ms": m["date_sent_ms"],
                "contact_name": m["contact_name"],
                "import_batch_id": m["import_batch_id"],
                "is_mms": m.get("is_mms", False),
            })
            created += 1

            # Create MMS parts (attachments)
            for part_data in m.get("mms_parts", []):
                try:
                    att = Attachment.create({
                        "name": part_data["filename"],
                        "type": "binary",
                        "datas": part_data["data_b64"],
                        "mimetype": part_data["content_type"],
                        "res_model": "sms.archive.mms.part",
                        "res_id": 0,  # Updated after part creation
                    })
                    mms_part = MmsPart.create({
                        "message_id": msg.id,
                        "content_type": part_data["content_type"],
                        "filename": part_data["filename"],
                        "attachment_id": att.id,
                        "sequence": part_data.get("sequence", 0),
                    })
                    att.write({"res_id": mms_part.id})
                    att_count += 1
                except Exception:
                    _logger.warning(
                        "Failed to import MMS part %s for message %s",
                        part_data.get("filename"), msg.id,
                        exc_info=True,
                    )

        return created, att_count

    def _match_partners(self, thread_map, owner_id):
        """Match threads to Odoo contacts by phone number."""
        Thread = self.env["sms.archive.thread"]
        Partner = self.env["res.partner"]
        matched = 0

        unmatched = Thread.search([
            ("owner_id", "=", owner_id),
            ("partner_id", "=", False),
        ])

        for thread in unmatched:
            phone = thread.phone_normalized
            if not phone or len(phone) < 7:
                continue
            suffix = phone[-10:]
            partner = Partner.search(
                ["|", ("phone", "ilike", suffix), ("mobile", "ilike", suffix)],
                limit=1,
            )
            if partner:
                thread.partner_id = partner
                matched += 1

        return matched

    # ------------------------------------------------------------------
    # Call log import
    # ------------------------------------------------------------------

    def _parse_and_import_calls(self, stream):
        """Stream-parse call log XML and import calls.

        `stream` is any file-like object.
        """
        Thread = self.env["sms.archive.thread"]
        Call = self.env["call.archive.call"]
        normalize = Thread.normalize_phone
        owner_id = self.env.uid

        # Pre-load existing hashes for dedup
        existing_hashes = set(
            Call.search([("owner_id", "=", owner_id)]).mapped("call_hash")
        )

        # Pre-load existing threads
        existing_threads = {}
        for t in Thread.search([("owner_id", "=", owner_id)]):
            existing_threads[t.phone_normalized] = t.id

        stats = {
            "processed": 0,
            "created": 0,
            "duplicates": 0,
            "threads": 0,
            "partners_matched": 0,
        }

        batch = []
        new_threads = {}
        backup_set = ""

        for event, elem in iterparse(stream, events=("end",)):
            if elem.tag == "calls":
                backup_set = elem.get("backup_set", "")
                continue

            if elem.tag != "call":
                continue

            stats["processed"] += 1

            number = elem.get("number", "")
            date_ms = elem.get("date", "0")
            duration = elem.get("duration", "0")
            call_type_raw = elem.get("type", "1")
            contact = elem.get("contact_name", "")
            presentation_raw = elem.get("presentation", "")

            call_type = _CALL_TYPE_MAP.get(call_type_raw, "incoming")
            presentation = _PRESENTATION_MAP.get(presentation_raw)

            phone_norm = normalize(number)
            if not phone_norm:
                elem.clear()
                continue

            # Dedup hash
            call_hash = hashlib.sha256(
                f"{phone_norm}|{date_ms}|{duration}|{call_type}".encode()
            ).hexdigest()

            if call_hash in existing_hashes:
                stats["duplicates"] += 1
                elem.clear()
                continue

            existing_hashes.add(call_hash)

            # Track thread info
            if phone_norm not in existing_threads and phone_norm not in new_threads:
                new_threads[phone_norm] = {
                    "contact_name": contact,
                    "phone_raw": number,
                }
            elif phone_norm in new_threads and contact and contact != "(Unknown)":
                new_threads[phone_norm]["contact_name"] = contact

            # Convert timestamp
            try:
                ts = int(date_ms) / 1000
                dt = datetime.utcfromtimestamp(ts)
            except (ValueError, OSError):
                dt = fields.Datetime.now()

            try:
                dur = int(duration)
            except (ValueError, TypeError):
                dur = 0

            batch.append({
                "phone_norm": phone_norm,
                "call_hash": call_hash,
                "call_type": call_type,
                "date": dt,
                "date_ms": date_ms,
                "duration": dur,
                "contact_name": contact,
                "import_batch_id": backup_set,
                "presentation": presentation,
            })

            if len(batch) >= _BATCH_SIZE:
                created = self._flush_call_batch(
                    batch, new_threads, existing_threads, owner_id,
                )
                stats["created"] += created
                batch = []

            elem.clear()

        # Flush remaining
        if batch:
            created = self._flush_call_batch(
                batch, new_threads, existing_threads, owner_id,
            )
            stats["created"] += created

        stats["threads"] = len(existing_threads)

        # Match partners
        matched = self._match_partners(existing_threads, owner_id)
        stats["partners_matched"] = matched

        return stats

    def _flush_call_batch(self, batch, new_threads, existing_threads, owner_id):
        """Create threads and calls for a batch. Returns created count."""
        Thread = self.env["sms.archive.thread"]
        Call = self.env["call.archive.call"]

        # Create any new threads needed by this batch
        phones_needed = {
            c["phone_norm"] for c in batch
        } - set(existing_threads.keys())

        for phone in phones_needed:
            info = new_threads.get(phone, {})
            thread = Thread.create({
                "phone_normalized": phone,
                "phone_raw": info.get("phone_raw", phone),
                "contact_name": info.get("contact_name", ""),
                "owner_id": owner_id,
            })
            existing_threads[phone] = thread.id

        # Batch-create calls
        created = 0
        for c in batch:
            thread_id = existing_threads.get(c["phone_norm"])
            if not thread_id:
                continue
            vals = {
                "thread_id": thread_id,
                "call_hash": c["call_hash"],
                "call_type": c["call_type"],
                "date": c["date"],
                "date_ms": c["date_ms"],
                "duration": c["duration"],
                "contact_name": c["contact_name"],
                "import_batch_id": c["import_batch_id"],
            }
            if c.get("presentation"):
                vals["presentation"] = c["presentation"]
            Call.create(vals)
            created += 1

        return created

    # ------------------------------------------------------------------
    # Disk-based import (for multi-GB XMLs that can't fit in Binary/HTTP)
    # ------------------------------------------------------------------

    @api.model
    def _cron_disk_import(self):
        """Scan /mnt/sms-inbox for XML files and import them via iterparse
        directly from disk — no HTTP upload, no base64, no Binary field.

        Files are moved to done/ on success or failed/ on error.
        Suitable for files of any size (limited only by disk / parser time).

        Config via ir.config_parameter:
          - bf_sms_archive.disk_watch_user_id : Odoo user ID to own imports (default 2)
        """
        ICP = self.env["ir.config_parameter"].sudo()
        owner_uid = int(ICP.get_param("bf_sms_archive.disk_watch_user_id", "2"))

        if not os.path.isdir(_DISK_INBOX):
            _logger.debug("SMS disk import: %s not mounted, skipping", _DISK_INBOX)
            return

        done_dir = os.path.join(_DISK_INBOX, "done")
        failed_dir = os.path.join(_DISK_INBOX, "failed")
        os.makedirs(done_dir, exist_ok=True)
        os.makedirs(failed_dir, exist_ok=True)

        # List XML files in the inbox root (not subdirs)
        try:
            entries = sorted(os.listdir(_DISK_INBOX))
        except OSError:
            _logger.exception("SMS disk import: cannot list %s", _DISK_INBOX)
            return

        for name in entries:
            src = os.path.join(_DISK_INBOX, name)
            if not os.path.isfile(src):
                continue
            if not name.lower().endswith(".xml"):
                continue

            _logger.info("SMS disk import: processing %s", src)
            try:
                # Run as the configured owner so threads/messages get
                # the right ownership.
                env_as_owner = self.env(user=owner_uid)
                wiz = env_as_owner["sms.archive.import.wizard"]

                with open(src, "rb") as fh:
                    file_type = wiz._detect_file_type(fh)
                    if file_type == "calls":
                        stats = wiz._parse_and_import_calls(fh)
                        kind = "Appels"
                    else:
                        stats = wiz._parse_and_import(fh)
                        kind = "SMS/MMS"

                _logger.info(
                    "SMS disk import: %s (%s) — traités=%d, créés=%d, "
                    "doublons=%d, fils=%d, contacts=%d",
                    name, kind,
                    stats.get("processed", 0),
                    stats.get("created", 0),
                    stats.get("duplicates", 0),
                    stats.get("threads", 0),
                    stats.get("partners_matched", 0),
                )

                os.rename(src, os.path.join(done_dir, name))
                self.env.cr.commit()

            except Exception:
                _logger.exception("SMS disk import: failed on %s", name)
                self.env.cr.rollback()
                try:
                    os.rename(src, os.path.join(failed_dir, name))
                except OSError:
                    _logger.exception(
                        "SMS disk import: could not move %s to failed/", name,
                    )

    def action_disk_import_now(self):
        """Manual trigger for the disk import (button on the wizard)."""
        self.ensure_one()
        self._cron_disk_import()
        self.result_message = _(
            "Import depuis /mnt/sms-inbox déclenché.\n"
            "Consultez le journal Odoo pour les détails, ou le dossier "
            "sms-inbox/done (succès) / sms-inbox/failed (erreurs)."
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    # ------------------------------------------------------------------
    # Nextcloud folder watcher (cron)
    # ------------------------------------------------------------------

    @api.model
    def _cron_nc_watch_import(self):
        """Poll a Nextcloud folder for new XML/ZIP files and import them.

        Config via ir.config_parameter:
          - bf_sms_archive.nc_watch_path : folder to watch (e.g. /Backups/SMS/Live)
          - bf_sms_archive.nc_watch_user_id : Odoo user ID to own imported messages
        NC credentials from environment: NC_SMS_WATCH_URL, NC_SMS_WATCH_USER, NC_SMS_WATCH_PASSWORD
        Falls back to NC_URL / NC_USER / NC_PASSWORD if specific vars not set.
        """
        ICP = self.env["ir.config_parameter"].sudo()
        watch_path = ICP.get_param("bf_sms_archive.nc_watch_path", "")
        if not watch_path:
            return

        owner_uid = int(ICP.get_param("bf_sms_archive.nc_watch_user_id", "2"))

        # NC credentials from env
        nc_url = (
            os.environ.get("NC_SMS_WATCH_URL")
            or os.environ.get("NC_URL", "")
        ).rstrip("/")
        nc_user = (
            os.environ.get("NC_SMS_WATCH_USER")
            or os.environ.get("NC_USER", "")
        )
        nc_pass = (
            os.environ.get("NC_SMS_WATCH_PASSWORD")
            or os.environ.get("NC_PASSWORD", "")
        )

        if not all([nc_url, nc_user, nc_pass]):
            _logger.error("SMS watch: NC credentials not configured in environment")
            return

        webdav_base = f"{nc_url}/remote.php/dav/files/{nc_user}"

        # List files in watch folder
        files = self._nc_list_folder(webdav_base, watch_path, nc_user, nc_pass)
        if not files:
            return

        # Filter XML and ZIP files
        importable = [
            f for f in files
            if f["name"].lower().endswith((".xml", ".zip")) and not f["is_dir"]
        ]

        if not importable:
            return

        # Ensure done/, too_large/ and failed/ subfolders exist
        done_path = watch_path.rstrip("/") + "/done"
        too_large_path = watch_path.rstrip("/") + "/too_large"
        failed_path = watch_path.rstrip("/") + "/failed"
        self._nc_mkcol(webdav_base, done_path, nc_user, nc_pass)
        self._nc_mkcol(webdav_base, too_large_path, nc_user, nc_pass)
        self._nc_mkcol(webdav_base, failed_path, nc_user, nc_pass)

        for file_info in importable:
            fname = file_info["name"]
            file_path = watch_path.rstrip("/") + "/" + fname
            _logger.info("SMS watch: processing %s", file_path)

            try:
                file_size = self._nc_get_size(
                    webdav_base, file_path, nc_user, nc_pass,
                )
                if not file_size:
                    _logger.warning(
                        "SMS watch: %s — cannot determine size, skipping",
                        fname,
                    )
                    continue

                is_xml = fname.lower().endswith(".xml")
                env_as_owner = self.env(user=owner_uid)

                if file_size <= _NC_WATCH_DIRECT_MAX:
                    # Direct path: download in memory and pass to wizard.
                    raw = self._nc_download(
                        webdav_base, file_path, nc_user, nc_pass,
                    )
                    if not raw:
                        continue
                    wizard = env_as_owner["sms.archive.import.wizard"].create({
                        "file_data": base64.b64encode(raw),
                        "file_name": fname,
                    })
                    del raw
                    wizard.action_import()
                    result = wizard.result_message or ""
                    _logger.info(
                        "SMS watch: %s — %s",
                        fname, result.replace("\n", " | "),
                    )
                    self._nc_move(
                        webdav_base, file_path,
                        done_path + "/" + fname, nc_user, nc_pass,
                    )
                    _logger.info("SMS watch: moved %s to done/", fname)
                    self.env.cr.commit()
                    continue

                # Big-file path: only XML can be split. ZIPs over the
                # threshold land in too_large/ for manual handling.
                if not is_xml:
                    _logger.warning(
                        "SMS watch: %s is %d MB > %d MB and not XML"
                        " — moving to too_large/",
                        fname,
                        file_size // (1024 * 1024),
                        _NC_WATCH_DIRECT_MAX // (1024 * 1024),
                    )
                    self._nc_move(
                        webdav_base, file_path,
                        too_large_path + "/" + fname,
                        nc_user, nc_pass,
                    )
                    continue

                # Stream-download to staging, split, import each chunk.
                staging = (
                    Path(_DISK_INBOX) / _SPLIT_STAGING_SUBDIR
                    / Path(fname).stem
                )
                # Clean any leftover from a prior aborted run.
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
                staging.mkdir(parents=True, exist_ok=True)
                src_local = staging / fname
                _logger.info(
                    "SMS watch: %s is %d MB — streaming to %s for split",
                    fname, file_size // (1024 * 1024), staging,
                )
                try:
                    if not self._nc_stream_to_path(
                        webdav_base, file_path, nc_user, nc_pass, src_local,
                    ):
                        raise IOError(f"stream download failed for {fname}")

                    chunks = self._split_xml_to_chunks(
                        src_local, staging, _CHUNK_MAX_BYTES,
                    )
                    if not chunks:
                        raise ValueError(f"split produced 0 chunks for {fname}")

                    _logger.info(
                        "SMS watch: importing %d chunks for %s",
                        len(chunks), fname,
                    )
                    for idx, chunk_path in enumerate(chunks, 1):
                        chunk_size_mb = chunk_path.stat().st_size // (1024 * 1024)
                        _logger.info(
                            "SMS watch: %s chunk %d/%d (%d MB) — importing",
                            fname, idx, len(chunks), chunk_size_mb,
                        )
                        wizard = env_as_owner["sms.archive.import.wizard"].create({
                            "file_data": base64.b64encode(chunk_path.read_bytes()),
                            "file_name": chunk_path.name,
                        })
                        wizard.action_import()
                        result = wizard.result_message or ""
                        _logger.info(
                            "SMS watch: %s chunk %d — %s",
                            fname, idx, result.replace("\n", " | "),
                        )
                        # Commit per chunk: resume-safe if a later chunk fails.
                        self.env.cr.commit()

                    self._nc_move(
                        webdav_base, file_path,
                        done_path + "/" + fname, nc_user, nc_pass,
                    )
                    _logger.info(
                        "SMS watch: moved %s to done/ after split-import",
                        fname,
                    )
                except Exception:
                    # Split path-specific failure: route to too_large/ so it
                    # does not get retried on next cron tick. failed/ remains
                    # reserved for direct-path errors handled below.
                    _logger.exception(
                        "SMS watch: split-import failed for %s"
                        " — moving to too_large/",
                        fname,
                    )
                    self.env.cr.rollback()
                    try:
                        self._nc_move(
                            webdav_base, file_path,
                            too_large_path + "/" + fname,
                            nc_user, nc_pass,
                        )
                    except Exception:
                        _logger.exception(
                            "SMS watch: could not move %s to too_large/", fname,
                        )
                finally:
                    shutil.rmtree(staging, ignore_errors=True)

            except Exception:
                _logger.exception("SMS watch: failed to process %s", fname)
                self.env.cr.rollback()
                try:
                    dest = failed_path + "/" + fname
                    self._nc_move(
                        webdav_base, file_path, dest, nc_user, nc_pass,
                    )
                    _logger.info("SMS watch: moved %s to failed/", fname)
                except Exception:
                    _logger.exception(
                        "SMS watch: could not move %s to failed/", fname,
                    )

    @staticmethod
    def _nc_list_folder(webdav_base, path, user, password):
        """List files in a Nextcloud WebDAV folder."""
        url = webdav_base + url_quote(path) + "/"
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:">'
            "<d:prop><d:displayname/><d:resourcetype/></d:prop>"
            "</d:propfind>"
        )
        try:
            resp = http_requests.request(
                "PROPFIND", url,
                data=body.encode("utf-8"),
                headers={"Content-Type": "application/xml", "Depth": "1"},
                auth=(user, password),
                timeout=30,
            )
        except Exception:
            _logger.exception("SMS watch: PROPFIND failed for %s", path)
            return []

        if resp.status_code not in (200, 207):
            _logger.error("SMS watch: PROPFIND %s returned %s", path, resp.status_code)
            return []

        from defusedxml import ElementTree as SafeET
        try:
            root = SafeET.fromstring(resp.text)
        except Exception:
            _logger.exception("SMS watch: failed to parse PROPFIND response")
            return []

        results = []
        ns = "DAV:"
        for response in root.findall(f"{{{ns}}}response"):
            href_el = response.find(f"{{{ns}}}href")
            if href_el is None or not href_el.text:
                continue
            href = href_el.text.rstrip("/")
            name = href.split("/")[-1]
            if not name:
                continue
            # Skip the folder itself
            from urllib.parse import unquote
            if unquote(href).rstrip("/") == path.rstrip("/"):
                continue

            propstat = response.find(f"{{{ns}}}propstat")
            prop = propstat.find(f"{{{ns}}}prop") if propstat is not None else None
            is_dir = False
            if prop is not None:
                rt = prop.find(f"{{{ns}}}resourcetype")
                if rt is not None and rt.find(f"{{{ns}}}collection") is not None:
                    is_dir = True

            results.append({"name": unquote(name), "href": href, "is_dir": is_dir})

        return results

    @staticmethod
    def _nc_download(webdav_base, path, user, password):
        """Download a file from Nextcloud via WebDAV GET."""
        url = webdav_base + url_quote(path)
        try:
            resp = http_requests.get(url, auth=(user, password), timeout=300)
            if resp.status_code == 200:
                return resp.content
            _logger.error("SMS watch: GET %s returned %s", path, resp.status_code)
        except Exception:
            _logger.exception("SMS watch: download failed for %s", path)
        return None

    @staticmethod
    def _nc_stream_to_path(webdav_base, path, user, password, dest_path):
        """Stream-download a file from Nextcloud to a local filesystem path.

        Used for files too large to materialize in worker memory. Writes 8 MB
        chunks to disk. Returns the destination path on success, None on failure.
        """
        url = webdav_base + url_quote(path)
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with http_requests.get(
                url, auth=(user, password), stream=True, timeout=(30, 600),
            ) as resp:
                if resp.status_code != 200:
                    _logger.error(
                        "SMS watch: stream GET %s returned %s",
                        path, resp.status_code,
                    )
                    return None
                with open(dest, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                        if chunk:
                            f.write(chunk)
            return dest
        except Exception:
            _logger.exception("SMS watch: stream download failed for %s", path)
            try:
                dest.unlink(missing_ok=True)
            except Exception:
                pass
            return None

    @staticmethod
    def _nc_get_size(webdav_base, path, user, password):
        """Get the size in bytes of a file on Nextcloud via PROPFIND.

        Returns the size as int, or None if the request fails.
        """
        url = webdav_base + url_quote(path)
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:">'
            "<d:prop><d:getcontentlength/></d:prop>"
            "</d:propfind>"
        )
        try:
            resp = http_requests.request(
                "PROPFIND", url,
                data=body.encode("utf-8"),
                headers={"Content-Type": "application/xml", "Depth": "0"},
                auth=(user, password),
                timeout=15,
            )
            if resp.status_code not in (200, 207):
                return None
            from defusedxml import ElementTree as SafeET
            root = SafeET.fromstring(resp.text)
            ns = "DAV:"
            for response in root.findall(f"{{{ns}}}response"):
                propstat = response.find(f"{{{ns}}}propstat")
                if propstat is None:
                    continue
                prop = propstat.find(f"{{{ns}}}prop")
                if prop is None:
                    continue
                cl = prop.find(f"{{{ns}}}getcontentlength")
                if cl is not None and cl.text:
                    return int(cl.text)
        except Exception:
            _logger.warning("SMS watch: failed to get size for %s", path, exc_info=True)
        return None

    @staticmethod
    def _nc_mkcol(webdav_base, path, user, password):
        """Create a directory on Nextcloud (ignore 405 = already exists)."""
        url = webdav_base + url_quote(path) + "/"
        try:
            resp = http_requests.request("MKCOL", url, auth=(user, password), timeout=15)
            if resp.status_code not in (200, 201, 204, 405):
                _logger.warning("SMS watch: MKCOL %s returned %s", path, resp.status_code)
        except Exception:
            _logger.warning("SMS watch: MKCOL failed for %s", path, exc_info=True)

    @staticmethod
    def _nc_move(webdav_base, src_path, dest_path, user, password):
        """Move a file on Nextcloud via WebDAV MOVE."""
        src_url = webdav_base + url_quote(src_path)
        dest_url = webdav_base + url_quote(dest_path)
        try:
            resp = http_requests.request(
                "MOVE", src_url,
                headers={"Destination": dest_url, "Overwrite": "T"},
                auth=(user, password),
                timeout=30,
            )
            if resp.status_code not in (200, 201, 204):
                _logger.error("SMS watch: MOVE %s -> %s returned %s", src_path, dest_path, resp.status_code)
        except Exception:
            _logger.exception("SMS watch: MOVE failed %s -> %s", src_path, dest_path)

    @staticmethod
    def _split_xml_to_chunks(src_path, out_dir, chunk_max_bytes):
        """Split a SMS Backup & Restore XML into chunk files <= chunk_max_bytes.

        Streams via iterparse to avoid loading the whole file into memory.
        Auto-detects SMS (<smses>, with <sms>/<mms> children) vs call log
        (<calls>, with <call> children). Each chunk is a complete, parseable
        XML document with the appropriate root wrapper.

        Returns a list of pathlib.Path to chunk files (out_dir / chunk_NNN.xml).
        """
        src = Path(src_path)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        with open(src, "rb") as f:
            head = f.read(500).lower()
        if b"<calls" in head:
            valid_tags = ("call",)
            header_tpl = _CALL_CHUNK_HEADER
            footer = _CALL_CHUNK_FOOTER
        else:
            valid_tags = ("sms", "mms")
            header_tpl = _SMS_CHUNK_HEADER
            footer = _SMS_CHUNK_FOOTER

        chunks = []
        buf = BytesIO()
        count = 0
        total = 0

        def _flush():
            nonlocal buf, count
            if count == 0:
                return
            chunk_path = out_dir / f"chunk_{len(chunks):03d}.xml"
            header = header_tpl.replace(b"{count}", str(count).encode())
            with open(chunk_path, "wb") as out:
                out.write(header)
                out.write(buf.getvalue())
                out.write(footer)
            chunks.append(chunk_path)
            buf = BytesIO()
            count = 0

        # iterparse(path) streams from disk without loading the full file.
        for event, elem in iterparse(str(src), events=("end",)):
            if elem.tag not in valid_tags:
                continue
            raw = _et_tostring(elem, encoding="unicode", short_empty_elements=True)
            raw_bytes = ("  " + raw + "\n").encode("utf-8")
            # Flush before append if this element would push us past the cap.
            if buf.tell() + len(raw_bytes) + len(footer) + 200 > chunk_max_bytes and count > 0:
                _flush()
            buf.write(raw_bytes)
            count += 1
            total += 1
            elem.clear()

        _flush()
        _logger.info(
            "SMS watch: split %s into %d chunks (%d elements)",
            src.name, len(chunks), total,
        )
        return chunks
