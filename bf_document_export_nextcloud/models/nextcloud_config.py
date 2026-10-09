from urllib.parse import quote as url_quote

import requests

from odoo import models
from odoo.exceptions import UserError

from odoo.addons.bf_document_nextcloud_sync.models.nextcloud_document_config import _sanitize_nc_path


class PreconditionFailed(UserError):
    """The file on Nextcloud is not the one the deposit expected (HTTP 412)."""


class NextcloudDocumentConfig(models.Model):
    _inherit = "nextcloud.document.config"

    def _bf_webdav_put(self, path, content, content_type, etag=None):
        """PUT a file only if it is still the one the caller saw.

        ``etag`` given: replace that version of the file and nothing else.
        ``etag`` None: create the file, never over one that appeared meanwhile.
        """
        self.ensure_one()
        path = _sanitize_nc_path(path)
        condition = {"If-Match": f'"{etag}"'} if etag else {"If-None-Match": "*"}
        try:
            resp = requests.put(
                self.webdav_url.rstrip("/") + url_quote(path),
                data=content,
                headers={"Content-Type": content_type, **condition},
                auth=self._get_auth(),
                timeout=120,
                verify=self._tls_verify,
            )
        except requests.RequestException as error:
            raise UserError(self.env._("Nextcloud upload failed: %s", error)) from error
        if resp.status_code == 412:
            raise PreconditionFailed(self.env._("changed on Nextcloud meanwhile"))
        if resp.status_code not in (200, 201, 204):
            raise UserError(self.env._("Nextcloud upload failed: HTTP %s", resp.status_code))
        return resp

    def _bf_webdav_move(self, source, destination):
        """Move a file on Nextcloud via WebDAV MOVE, never over an existing file."""
        self.ensure_one()
        source = _sanitize_nc_path(source)
        destination = _sanitize_nc_path(destination)
        base = self.webdav_url.rstrip("/")
        try:
            resp = requests.request(
                "MOVE",
                base + url_quote(source),
                headers={"Destination": base + url_quote(destination), "Overwrite": "F"},
                auth=self._get_auth(),
                timeout=60,
                verify=self._tls_verify,
            )
        except requests.RequestException as error:
            raise UserError(self.env._("Nextcloud move failed: %s", error)) from error
        if resp.status_code not in (201, 204):
            raise UserError(self.env._("Nextcloud move failed: HTTP %s", resp.status_code))
        return resp
