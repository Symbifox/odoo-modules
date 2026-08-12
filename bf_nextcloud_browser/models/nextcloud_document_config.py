"""Extend nextcloud.document.config with the WebDAV verbs the browser needs.

The parent module (bf_document_nextcloud_sync) already implements PROPFIND / GET /
PUT / MKCOL and the OCS share API, plus path-sanitisation and SSRF guards. Here we
add the two missing verbs (DELETE, MOVE) and a per-config browser root prefix that
scopes what the embedded browser is ever allowed to reach.
"""

from urllib.parse import quote as url_quote

import requests

from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.bf_document_nextcloud_sync.models.nextcloud_document_config import (
    _sanitize_nc_path,
)


class NextcloudDocumentConfig(models.Model):
    _inherit = "nextcloud.document.config"

    browser_root_prefix = fields.Char(
        string="Prefixe racine (navigateur)",
        default="/",
        help="Tout chemin atteignable depuis le navigateur de fichiers embarque "
        "doit etre sous ce prefixe (ex: /Blue Fox/). Defense en profondeur en plus "
        "du dossier propre a chaque enregistrement.",
    )

    nc_open_extensions = fields.Char(
        string="Extensions ouvrant Nextcloud",
        default="xlsx,ods,csv,docx,odt,pptx,odp,xls,doc,ppt",
        help="Extensions (separees par des virgules) dont le clic ouvre le fichier "
        "directement dans Nextcloud (ex. Collabora) plutot que dans l'apercu integre.",
    )

    nc_folder_color = fields.Char(
        string="Couleur des dossiers",
        default="#2D3031",
        help="Couleur (hex) des icones de dossier dans le navigateur. "
        "Defaut: anthracite Blue Fox. Mettre la couleur d'accent (#29ABE1) pour du contraste.",
    )

    share_preset_ids = fields.One2many(
        "nextcloud.share.preset",
        "config_id",
        string="Prereglages de partage",
    )

    def _open_extensions_list(self):
        self.ensure_one()
        return [
            x.strip().lower()
            for x in (self.nc_open_extensions or "").split(",")
            if x.strip()
        ]

    def _ensure_share_presets(self):
        """Seed the two default presets (Interne / Externe) once per config."""
        Preset = self.env["nextcloud.share.preset"].sudo()
        for cfg in self:
            if cfg.share_preset_ids:
                continue
            Preset.create([
                {
                    "config_id": cfg.id,
                    "sequence": 10,
                    "name": "Interne",
                    "access": "read_write",
                    "expiry_days": 0,
                    "password_protected": False,
                },
                {
                    "config_id": cfg.id,
                    "sequence": 20,
                    "name": "Externe",
                    "access": "read",
                    "expiry_days": cfg.default_share_expiry_days or 30,
                    "password_protected": False,
                },
            ])

    def _webdav_delete(self, path):
        """Delete a file or folder via WebDAV DELETE (recursive on folders)."""
        self.ensure_one()
        path = _sanitize_nc_path(path)
        url = self.webdav_url.rstrip("/") + url_quote(path)

        try:
            resp = requests.request(
                "DELETE",
                url,
                auth=self._get_auth(),
                timeout=30,
                verify=self._tls_verify,
            )
        except requests.RequestException as e:
            raise UserError(_("Erreur lors de la suppression: %s") % str(e))

        # 404 = already gone, treat as success (idempotent).
        if resp.status_code not in (200, 204, 404):
            raise UserError(_("Erreur WebDAV DELETE: HTTP %s") % resp.status_code)
        return resp

    def _webdav_move(self, src_path, dst_path):
        """Move/rename a file or folder via WebDAV MOVE.

        Used both for rename (same parent) and move (different parent). The
        Destination header must be the full WebDAV URL of the target.
        """
        self.ensure_one()
        src_path = _sanitize_nc_path(src_path)
        dst_path = _sanitize_nc_path(dst_path)
        src_url = self.webdav_url.rstrip("/") + url_quote(src_path)
        dst_url = self.webdav_url.rstrip("/") + url_quote(dst_path)

        try:
            resp = requests.request(
                "MOVE",
                src_url,
                headers={"Destination": dst_url, "Overwrite": "F"},
                auth=self._get_auth(),
                timeout=30,
                verify=self._tls_verify,
            )
        except requests.RequestException as e:
            raise UserError(_("Erreur lors du deplacement: %s") % str(e))

        if resp.status_code == 412:
            raise UserError(
                _("Un fichier ou dossier portant ce nom existe deja a destination.")
            )
        if resp.status_code not in (201, 204):
            raise UserError(_("Erreur WebDAV MOVE: HTTP %s") % resp.status_code)
        return resp
