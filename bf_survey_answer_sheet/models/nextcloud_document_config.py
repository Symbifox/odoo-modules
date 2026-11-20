"""The Nextcloud side of the filing: look, make, copy, and deposit safely.

The helpers of `bf_document_nextcloud_sync` do the plain verbs (PROPFIND,
MKCOL, PUT). What this file adds is the care a deposit needs when nobody is
watching: a conditional write that never lands on somebody else's file, and a
way to recognise this very response's earlier copy inside an encrypted PDF.
"""

import io
import logging
import posixpath
from urllib.parse import quote as url_quote

import requests

from odoo import models
from odoo.exceptions import UserError

from odoo.addons.bf_document_nextcloud_sync.models.nextcloud_document_config import (
    MAX_PROPFIND_ENTRIES,
    _sanitize_nc_path,
    _validate_path_under_prefix,
)

# How many names a sheet will try before giving up: the same company answering
# the same day is ordinary, a tenth one is not.
MAX_NAMES = 9

_logger = logging.getLogger(__name__)


class StrangerFile(UserError):
    """A file is already there and it is not ours to replace."""


class NextcloudDocumentConfig(models.Model):
    _inherit = "nextcloud.document.config"

    # ------------------------------------------------------------------ #
    # Looking
    # ------------------------------------------------------------------ #
    def _bf_sheet_children(self, path):
        """The names of the folders directly under `path`.

        🔴 A truncated listing is refused, not used: the helper stops at
        MAX_PROPFIND_ENTRIES names with a log line nobody reads, and a client
        folder beyond the cut would look missing - the chain would then make a
        SECOND folder for a company that already has one, and file the sheet in
        the one nobody shared.
        """
        self.ensure_one()
        path = _sanitize_nc_path(path)
        own = posixpath.basename(path.rstrip("/"))
        entries = self._webdav_propfind(path, depth="1")
        if len(entries) >= MAX_PROPFIND_ENTRIES:
            raise UserError(self.env._(
                "%(path)s holds more entries than a listing can carry "
                "(%(max)s): the folder of a company could be missed.",
                path=path, max=MAX_PROPFIND_ENTRIES))
        return [entry["name"] for entry in entries
                if entry.get("is_dir") and entry.get("name") and entry["name"] != own]

    def _bf_sheet_etag(self, path):
        """The etag of one file, or False when nothing is there.

        ⚠️ A Nextcloud that answers neither "here it is" nor "404" - an expired
        application password, a proxy in the way - raises rather than passing
        for an empty path: an authentication failure read as "nothing there"
        would have the caller write where it must not.
        """
        self.ensure_one()
        try:
            entries = self._webdav_propfind(path, depth="0")
        except UserError as error:
            if "404" in str(error) or "not found" in str(error).lower():
                return False
            raise
        return entries[0].get("etag") if entries else False

    # ------------------------------------------------------------------ #
    # Making
    # ------------------------------------------------------------------ #
    def _bf_sheet_mkdirs(self, path, under):
        """Create `path` and the levels it is missing, never outside `under`."""
        self.ensure_one()
        path = _sanitize_nc_path(path)
        under = _sanitize_nc_path(under)
        _validate_path_under_prefix(path, under)
        missing = posixpath.relpath(path, under)
        if missing in (".", ""):
            return path
        current = under
        for part in missing.split("/"):
            current = posixpath.join(current, part)
            self._webdav_mkcol(current)
        return path

    def _bf_sheet_copy(self, source, destination, under):
        """Copy a folder (recursively, as Nextcloud does), never over a file."""
        self.ensure_one()
        source = _sanitize_nc_path(source)
        destination = _sanitize_nc_path(destination)
        _validate_path_under_prefix(source, under)
        _validate_path_under_prefix(destination, under)
        base = self.webdav_url.rstrip("/")
        try:
            resp = requests.request(
                "COPY",
                base + url_quote(source),
                headers={"Destination": base + url_quote(destination), "Overwrite": "F"},
                auth=self._get_auth(),
                timeout=300,
                verify=self._tls_verify,
            )
        except requests.RequestException as error:
            raise UserError(self.env._("Nextcloud copy failed: %s", error)) from error
        if resp.status_code not in (201, 204):
            raise UserError(self.env._(
                "Nextcloud copy of %(source)s failed: HTTP %(code)s",
                source=source, code=resp.status_code))
        return destination

    # ------------------------------------------------------------------ #
    # Depositing
    # ------------------------------------------------------------------ #
    def _bf_sheet_deposit_file(self, path, content, known_etag, password, marker):
        """Write the sheet, and say where it finally landed: (path, etag).

        The write is conditional, always:

        * with a `known_etag`, it replaces THAT version of the file and nothing
          else - a file edited on Nextcloud meanwhile is left alone, whoever
          wrote it first;
        * without one, it creates the file, and never lands on an existing one.

        A refused write is then looked at, and three things can be there. This
        response's own earlier copy - recognised by the marker inside its
        encryption - is replaced. Another response's sheet is left alone and
        this one is filed beside it, under a numbered name: two people of one
        company answering the same day is ordinary. Anything else is a file
        this module did not write, and nothing is touched.
        """
        self.ensure_one()
        try:
            return path, self._bf_sheet_put(path, content, etag=known_etag)
        except StrangerFile:
            pass
        if known_etag:
            raise StrangerFile(self.env._(
                "%s changed on Nextcloud since this response wrote it: nothing "
                "was overwritten.", path))
        return self._bf_sheet_deposit_beside(path, content, password, marker)

    def _bf_sheet_deposit_beside(self, path, content, password, marker):
        """Place a sheet whose path is already taken, without erasing anybody."""
        self.ensure_one()
        for number in range(1, MAX_NAMES + 1):
            candidate = path if number == 1 else self._bf_sheet_numbered(path, number)
            etag = self._bf_sheet_etag(candidate)
            if not etag:
                try:
                    return candidate, self._bf_sheet_put(candidate, content, etag=None)
                except StrangerFile:
                    # Someone wrote there between the look and the write.
                    continue
            found = self._bf_sheet_mark_of(candidate, password)
            if found == marker:
                return candidate, self._bf_sheet_put(candidate, content, etag=etag)
            if found is None:
                raise StrangerFile(self.env._(
                    "A file is already at %s and it is not an answer sheet this "
                    "module wrote: nothing was overwritten.", candidate))
            # Another response's sheet: this one goes beside it.
        raise StrangerFile(self.env._(
            "%(path)s and the %(count)s names beside it are all taken by other "
            "answer sheets.", path=path, count=MAX_NAMES - 1))

    @staticmethod
    def _bf_sheet_numbered(path, number):
        """"…/sheet.pdf" and 2 -> "…/sheet (2).pdf"."""
        stem, dot, extension = path.rpartition(".")
        return f"{stem} ({number}){dot}{extension}" if dot else f"{path} ({number})"

    def _bf_sheet_put(self, path, content, etag=None):
        """Conditional PUT. Returns the etag of the stored file."""
        self.ensure_one()
        path = _sanitize_nc_path(path)
        condition = {"If-Match": f'"{etag}"'} if etag else {"If-None-Match": "*"}
        try:
            resp = requests.put(
                self.webdav_url.rstrip("/") + url_quote(path),
                data=content,
                headers={"Content-Type": "application/pdf", **condition},
                auth=self._get_auth(),
                timeout=120,
                verify=self._tls_verify,
            )
        except requests.RequestException as error:
            raise UserError(self.env._("Nextcloud upload failed: %s", error)) from error
        if resp.status_code == 412:
            raise StrangerFile(self.env._("%s is not the file this deposit expected.", path))
        if resp.status_code not in (200, 201, 204):
            raise UserError(self.env._(
                "Nextcloud upload of %(path)s failed: HTTP %(code)s",
                path=path, code=resp.status_code))
        stored = (resp.headers.get("OC-ETag") or resp.headers.get("ETag") or "").strip('"')
        # 🔴 Nextcloud answers a 204 without an etag behind some proxies: the
        # etag is then read back, because a deposit with no etag on record can
        # no longer replace its own file.
        return stored or (self._bf_sheet_etag(path) or "")

    def _bf_sheet_mark_of(self, path, password):
        """The marker the file at `path` carries, or None when it has none.

        The marker lives in the PDF's own metadata, under the encryption: only
        the cohort's password reveals it. A file encrypted with another
        password, not encrypted at all, or not a PDF, has no marker to give -
        and that is the answer that protects it, since a file this module
        cannot recognise is a file it must not touch.
        """
        self.ensure_one()
        from pyhanko.pdf_utils.reader import PdfFileReader

        # 🔴 A Nextcloud that does not answer is NOT a verdict about the file.
        # Swallowed here, an outage would read as "somebody else's file", burn
        # the attempts of the very loop built to survive outages, and leave the
        # response waiting for a person.
        data = self._webdav_get(path)
        try:
            reader = PdfFileReader(io.BytesIO(data))
            if reader.security_handler is None:
                return None
            if reader.decrypt(password).status.name == "FAILED":
                return None
            return str(reader.trailer["/Info"]["/Keywords"])
        except Exception as error:  # noqa: BLE001 - unreadable means "not ours"
            _logger.info("Answer sheet at %s could not be identified: %s", path, error)
            return None
