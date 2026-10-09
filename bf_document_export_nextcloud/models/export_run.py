import hashlib
import io
import json
import logging
import mimetypes
import posixpath
import zipfile
from collections import Counter

from odoo import fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.bf_document_export.models.export_template import clean_component
from odoo.addons.bf_document_nextcloud_sync.models.nextcloud_document_config import (
    MAX_PROPFIND_ENTRIES,
    _sanitize_nc_path,
    _validate_path_under_prefix,
)

from .export_template import nc_normalize, nc_under
from .nextcloud_config import PreconditionFailed

_logger = logging.getLogger(__name__)


class BfDocumentExportRun(models.Model):
    _inherit = "bf.document.export.run"

    def _deliver(self, content, manifest):
        result = super()._deliver(content, manifest)
        if not self.template_id.nc_config_id:
            return result
        if self.user_id != self.template_id.user_id:
            # The published copy is the registry as the template's « Export as »
            # person reads it. Read with narrower rights, the documents that person
            # can see and the requester cannot would be moved to the archives.
            self.write({"delivery_log": _Deposit(self)._(
                "Not deposited on Nextcloud: only an export run as the template's « Export as » "
                "person (%s) replaces the published copy.", self.template_id.user_id.name or "-")})
            return result
        if self.document_ids:
            # A selection is not the registry: deposited, it would send every other
            # document of the published copy to the archives.
            self.write({"delivery_log": _Deposit(self)._(
                "A selection of documents is not deposited on Nextcloud: only a full "
                "export replaces the published copy.")})
            return result
        _Deposit(self).execute(content, manifest)
        return result


class _Deposit:
    """Lays one export out on Nextcloud, writing only what changed.

    Per file, against what the template last deposited:
    - same content, untouched on Nextcloud: left alone;
    - new or changed content: written, unless someone changed the file on
      Nextcloud since the last deposit, in which case it is left untouched and
      reported;
    - already on Nextcloud but never written by the export: adopted when the
      content is the same, left untouched and reported otherwise;
    - no longer produced by the registry: moved to the archives, never deleted.
    """

    def __init__(self, run):
        self.run = run
        self.env = run.env
        self.template = run.template_id
        self.config = self.template.nc_config_id.sudo()
        self.target = _sanitize_nc_path((self.template.nc_target_path or "").rstrip("/"))
        self.State = self.env["bf.document.export.nc.file"].sudo()
        self.counts = Counter()
        self.lines = []
        self.listings = {}
        self.truncated = set()
        self.made = set()
        self.today = fields.Datetime.context_timestamp(
            run.with_context(tz=run.user_id.tz or self.env.user.tz or "UTC"), fields.Datetime.now()
        ).strftime("%Y-%m-%d")

    def _(self, text, *args, **kwargs):
        """Translate into the template's language (named ``_`` for the term extractor)."""
        env = self.env(context=dict(self.env.context, lang=self.template.lang or self.env.lang))
        return env._(text, *args, **kwargs)

    # ------------------------------------------------------------------ paths

    def remote(self, path):
        remote = _sanitize_nc_path(f"{self.target}/{path}")
        _validate_path_under_prefix(remote, self.target)
        return remote

    def check_sources(self):
        """Refuse a deposit folder that holds, or sits among, the registry's sources."""
        if not self.target or self.target == "/":
            raise UserError(self._("Choose a deposit folder: the export never deposits at the root of Nextcloud."))
        for folder in self.template._bf_nc_source_folders():
            if nc_under(self.target, folder) or nc_under(folder, self.target):
                raise UserError(self._(
                    "The deposit folder %(target)s and the source folder %(folder)s overlap: "
                    "the published copy must never be written among its sources.",
                    target=self.target, folder=folder))

    # ------------------------------------------------------------------ webdav

    def ensure_folder(self, folder):
        parts = [p for p in folder.split("/") if p]
        current = ""
        for part in parts:
            current += "/" + part
            if current not in self.made:
                self.config._webdav_mkcol(current)
                self.made.add(current)

    def listing(self, folder):
        if folder not in self.listings:
            entries = self.config._webdav_propfind(folder, depth="1")
            self.listings[folder] = {e["name"]: e.get("etag") for e in entries if not e.get("is_dir")}
            if len(entries) >= MAX_PROPFIND_ENTRIES:
                self.truncated.add(folder)
        return self.listings[folder]

    def etag_of(self, remote):
        """The file's tag on Nextcloud, or None when it is not there."""
        folder, name = posixpath.split(remote)
        etag = self.listing(folder).get(name)
        if etag is None and folder in self.truncated:
            # A listing stops at 500 entries: ask for the file itself before
            # concluding it is gone.
            try:
                entries = self.config._webdav_propfind(remote, depth="0")
            except UserError:
                return None
            etag = next((e.get("etag") for e in entries if not e.get("is_dir")), None)
        return etag

    def put(self, path, remote, data, sha, state, fingerprint=None, etag=None):
        """Write the file, if it is still the one seen on Nextcloud (``etag``, or absent)."""
        content_type = mimetypes.guess_type(remote)[0] or "application/octet-stream"
        response = self.config._bf_webdav_put(remote, data, content_type, etag=etag)
        headers = getattr(response, "headers", {}) or {}
        etag = (headers.get("OC-ETag") or headers.get("ETag") or "").strip('"')
        if not etag:
            folder, name = posixpath.split(remote)
            self.listings.pop(folder, None)
            etag = self.etag_of(remote)
        self.listings.setdefault(posixpath.dirname(remote), {})[posixpath.basename(remote)] = etag
        vals = {
            "template_id": self.template.id, "path": path, "remote_path": remote,
            "sha256": sha, "etag": etag, "size": len(data), "state": "present", "run_id": self.run.id,
            "fingerprint": fingerprint,
        }
        if state:
            state.write(vals)
        else:
            self.State.create(vals)

    # ------------------------------------------------------------------ files

    def deposit(self, path, data, state, fingerprint=None):
        remote = self.remote(path)
        sha = hashlib.sha256(data).hexdigest()
        etag = self.etag_of(remote)
        if state and etag and etag == state.etag:
            if (fingerprint and state.fingerprint == fingerprint) or state.sha256 == sha:
                self.counts["unchanged"] += 1
                return
            try:
                self.put(path, remote, data, sha, state, fingerprint, etag=etag)
            except PreconditionFailed:
                self.counts["untouched"] += 1
                self.lines.append(self._("%s: changed on Nextcloud during the deposit, left untouched", remote))
                return
            self.counts["written"] += 1
        elif state and etag:
            self.counts["untouched"] += 1
            self.lines.append(self._("%s: changed on Nextcloud since the last deposit, left untouched", remote))
        elif state:
            try:
                self.put(path, remote, data, sha, state, fingerprint)
            except PreconditionFailed:
                self.counts["untouched"] += 1
                self.lines.append(self._("%s: created on Nextcloud during the deposit, left untouched", remote))
                return
            self.counts["restored"] += 1
            self.lines.append(self._("%s: missing on Nextcloud, written again", remote))
        elif etag:
            if hashlib.sha256(self.config._webdav_get(remote)).hexdigest() == sha:
                self.State.create({
                    "template_id": self.template.id, "path": path, "remote_path": remote,
                    "sha256": sha, "etag": etag, "size": len(data), "state": "present",
                    "run_id": self.run.id, "fingerprint": fingerprint,
                })
                self.counts["unchanged"] += 1
            else:
                self.counts["untouched"] += 1
                self.lines.append(self._(
                    "%s: already on Nextcloud and not written by the export, left untouched", remote))
        else:
            try:
                self.put(path, remote, data, sha, state, fingerprint)
            except PreconditionFailed:
                self.counts["untouched"] += 1
                self.lines.append(self._("%s: created on Nextcloud during the deposit, left untouched", remote))
                return
            self.counts["written"] += 1

    def retire(self, state):
        """Move a file the registry no longer produces to the archives."""
        root = state.path.split("/")[0]
        archive = clean_component(self.template.archive_folder)  # as in the ZIP
        archive_folder = self.remote(f"{root}/{archive}")
        stem, extension = posixpath.splitext(posixpath.basename(state.path))
        if self.etag_of(state.remote_path) is None:
            state.write({"state": "archived", "run_id": self.run.id})
            self.lines.append(self._("%s: no longer in the registry, already gone from Nextcloud", state.remote_path))
            self.counts["archived"] += 1
            return
        self.ensure_folder(archive_folder)
        destination = self.remote(
            f"{root}/{archive}/{stem} - {self._('withdrawn on %s', self.today)}{extension}")
        counter = 2
        while self.etag_of(destination) is not None:
            destination = self.remote(
                f"{root}/{archive}/{stem} - "
                f"{self._('withdrawn on %s', self.today)} ({counter}){extension}")
            counter += 1
        source = state.remote_path
        self.config._bf_webdav_move(source, destination)
        state.write({"state": "archived", "remote_path": destination, "run_id": self.run.id})
        self.counts["archived"] += 1
        # Not « source »: env._() takes its text under that name.
        self.lines.append(self._("%(file)s: no longer in the registry, moved to %(destination)s",
                                 file=source, destination=destination))

    # ------------------------------------------------------------------ pass

    def published_manifest(self, data):
        """The manifest as deposited: the fingerprints of the files on Nextcloud.

        A document left as it was keeps the copy deposited earlier, so the
        manifest lists that copy's SHA-256, not the one of today's render.
        """
        manifest = json.loads(data)
        present = {
            s.path: s for s in self.State.search(
                [("template_id", "=", self.template.id), ("state", "=", "present")])
        }
        for entry in manifest.get("documents", []):
            state = present.get(entry.get("path"))
            if state and state.sha256:
                entry["sha256"], entry["size"] = state.sha256, state.size
        return json.dumps(manifest, ensure_ascii=False, indent=2, default=str).encode()

    def execute(self, content, manifest=None):
        self.config._get_auth()  # an unusable configuration stops here, before any write
        self.check_sources()
        archive = zipfile.ZipFile(io.BytesIO(content))
        files = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
        fingerprints = (manifest or {}).get("fingerprints") or {}
        manifest_path = next((p for p in files if p.count("/") == 1 and p.endswith("/manifest.json")), None)
        states = {
            s.path: s for s in self.State.search(
                [("template_id", "=", self.template.id), ("state", "=", "present")], order="id")
        }
        for folder in sorted({posixpath.dirname(f"{self.target}/{path}") for path in files}):
            self.ensure_folder(folder)
        # Any failure stays with its file. What was already written on Nextcloud
        # cannot be rolled back, so its record must not be either.
        # The manifest goes last, once the files it describes are known.
        for path in sorted(files, key=lambda p: (p == manifest_path, p)):
            if path == manifest_path:
                files[path] = self.published_manifest(files[path])
            try:
                self.deposit(path, files[path], states.get(path), fingerprints.get(path))
            except Exception as error:
                if not isinstance(error, (UserError, ValidationError)):
                    _logger.exception("Deposit of %s on Nextcloud failed", path)
                self.counts["errors"] += 1
                self.lines.append(f"{path}: {error}")
        unread = (manifest or {}).get("unread_documents") or []
        for path, state in states.items():
            if path in files:
                continue
            if any(part == code or part.startswith(f"{code} - ") for code in unread
                   for part in path.split("/")):
                # Its document could not be read this time: no withdrawal, its files stay.
                self.counts["kept"] += 1
                continue
            try:
                self.retire(state)
            except Exception as error:
                if not isinstance(error, (UserError, ValidationError)):
                    _logger.exception("Archiving %s on Nextcloud failed", state.remote_path)
                self.counts["errors"] += 1
                self.lines.append(f"{state.remote_path}: {error}")
        summary = self._(
            "Nextcloud %(folder)s: %(written)s written, %(unchanged)s unchanged, %(restored)s restored, "
            "%(untouched)s left untouched, %(archived)s moved to the archives, %(errors)s errors.",
            folder=self.target, written=self.counts["written"], unchanged=self.counts["unchanged"],
            restored=self.counts["restored"], untouched=self.counts["untouched"],
            archived=self.counts["archived"], errors=self.counts["errors"])
        if self.counts["kept"]:
            self.lines.append(self._(
                "%(count)s files kept as they were: their documents could not be read this time (%(codes)s).",
                count=self.counts["kept"], codes=", ".join(unread[:10]) + (" …" if len(unread) > 10 else "")))
        self.run.write({
            "delivery_state": "partial" if self.counts["untouched"] or self.counts["errors"] or self.counts["kept"]
            else "done",
            "delivery_log": "\n".join([summary] + self.lines),
        })
