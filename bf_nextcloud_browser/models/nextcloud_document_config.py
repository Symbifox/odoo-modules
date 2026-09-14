"""Extend nextcloud.document.config with the WebDAV verbs the browser needs.

The parent module (bf_document_nextcloud_sync) already implements PROPFIND / GET /
PUT / MKCOL and the OCS share API, plus path-sanitisation and SSRF guards. Here we
add the two missing verbs (DELETE, MOVE) and a per-config browser root prefix that
scopes what the embedded browser is ever allowed to reach.

Since 18.0.4.0.0 the browser also speaks AS THE PERSON. When the context carries
`bf_nc_as_user`, `webdav_url` and `_get_auth()` answer with that person's own
Nextcloud account instead of the configuration's account. Only the browser
facade sets that key: the synchronisation crons, the upload wizard and every
other caller of the parent helpers keep the configuration's account, because a
cron has no person to speak for.
"""

from urllib.parse import quote as url_quote

import requests

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.bf_document_nextcloud_sync.models.nextcloud_document_config import (
    _sanitize_nc_path,
)

from .bf_nc_user_credential import _server_key
from .nc_identity import NcNotConnected, NcPersonAuth, NcTokenRejected

# Fields whose change can make the browser appear or disappear for someone:
# the menu and the systray button are computed from them, and Odoo caches the
# menu tree per user.
BROWSER_VISIBILITY_FIELDS = {"active", "browser_root_prefix", "nextcloud_base_url"}


class NextcloudDocumentConfig(models.Model):
    _inherit = "nextcloud.document.config"

    browser_root_prefix = fields.Char(
        string="Prefixe racine (navigateur)",
        default="/",
        help="Tout chemin atteignable depuis le navigateur de fichiers embarque "
        "doit etre sous ce prefixe (ex: /Entreprise/). Defense en profondeur en plus "
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
        default="#2E3132",
        help="Couleur (hex) des icones de dossier dans le navigateur. "
        "Defaut: anthracite. Mettre la couleur d'accent (#29ABE2) pour du contraste.",
    )

    nc_panel_width_pct = fields.Integer(
        string="Largeur du panneau (%)",
        default=80,
        help="Largeur, en pourcentage de la fenetre, du panneau embarque ouvert "
        "depuis la barre systeme. Valeur de depart seulement: chaque personne "
        "peut ensuite redimensionner le panneau, et sa preference a priorite.",
    )

    nc_panel_height_pct = fields.Integer(
        string="Hauteur du panneau (%)",
        default=80,
        help="Hauteur, en pourcentage de la fenetre, du panneau embarque ouvert "
        "depuis la barre systeme. Meme regle que la largeur: c'est une valeur de "
        "depart, que la preference de chacun remplace.",
    )

    # Named after the 4.0.2 check, which compared email addresses. The name stays
    # so that configurations keep the choice already made on them.
    nc_require_email_match = fields.Boolean(
        string="Exiger l'identifiant de la personne",
        default=True,
        help="Quand une personne connecte son compte Nextcloud, le compte approuve "
        "(son identifiant ou son adresse courriel Nextcloud) doit correspondre a "
        "l'identifiant de son utilisateur Odoo, que seul un administrateur peut "
        "changer. Sans cette verification, une page de connexion transmise a "
        "quelqu'un d'autre connecterait l'expediteur aux fichiers du destinataire.",
    )

    nc_user_credential_ids = fields.One2many(
        "bf.nc.user.credential",
        "config_id",
        string="Connexions des personnes",
        groups="base.group_system",
    )

    share_preset_ids = fields.One2many(
        "nextcloud.share.preset",
        "config_id",
        string="Prereglages de partage",
    )

    @api.constrains("nc_panel_width_pct", "nc_panel_height_pct")
    def _check_nc_panel_size(self):
        for cfg in self:
            for value, dim in (
                (cfg.nc_panel_width_pct, _("largeur")),
                (cfg.nc_panel_height_pct, _("hauteur")),
            ):
                if value and not 40 <= value <= 100:
                    raise ValidationError(
                        _(
                            "La %(dim)s du panneau doit etre comprise entre 40 et 100.",
                            dim=dim,
                        )
                    )

    # ------------------------------------------------------------------
    # Per-person identity
    # ------------------------------------------------------------------
    def _nc_person(self):
        """The connection of the person the browser speaks for, or None.

        None means "not a browser call": the caller gets the configuration's
        account, exactly as before. A browser call without a connection raises
        instead of falling back, because falling back is the defect this
        version removes.
        """
        uid = self.env.context.get("bf_nc_as_user")
        if not uid:
            return None
        # The key arrives in the context, and an RPC client chooses its own
        # context: it may only ever name the caller.
        if uid != self.env.uid:
            raise AccessError(_("Identite Nextcloud refusee."))
        self.ensure_one()
        cred = self.env["bf.nc.user.credential"].sudo().search(
            [
                ("user_id", "=", uid),
                ("config_id", "=", self.id),
                ("state", "=", "connected"),
            ],
            limit=1,
        )
        if not cred or cred.nc_server != _server_key(self.nextcloud_base_url):
            # No connection, or one issued by another server: its app password
            # is never sent to this one.
            raise NcNotConnected(
                _("Connectez votre compte Nextcloud pour parcourir ces fichiers.")
            )
        return cred

    @property
    def webdav_url(self):
        cred = self._nc_person()
        if cred is None:
            return super().webdav_url
        base = self.nextcloud_base_url.rstrip("/")
        path = self.webdav_path or "/remote.php/dav/files/"
        if not path.startswith("/"):
            path = "/" + path
        if not path.rstrip("/").endswith("/files"):
            # A path naming one account would send the person's requests to
            # that account's files.
            raise UserError(_(
                "Le chemin WebDAV de la configuration doit se terminer par /files "
                "pour que chaque personne utilise son propre compte."
            ))
        # An account id may carry '@' or a space: quote it as one segment.
        path = path.rstrip("/") + "/" + url_quote(cred.nc_user_id, safe="") + "/"
        return base + path

    def _get_auth(self):
        cred = self._nc_person()
        if cred is None:
            return super()._get_auth()
        password = self._decrypt_value(cred.app_password_encrypted)
        if not password:
            raise NcTokenRejected(
                _("Votre connexion Nextcloud est illisible. Reconnectez-vous.")
            )
        return NcPersonAuth(cred.nc_login, password, {
            "rejected": _(
                "Nextcloud a refuse votre connexion : elle a ete revoquee. "
                "Reconnectez votre compte."
            ),
            "unavailable": _(
                "Nextcloud refuse l'acces a votre compte pour le moment "
                "(compte desactive, ou serveur en maintenance)."
            ),
            "not_found": _(
                "Ce dossier est introuvable dans votre Nextcloud, "
                "ou il ne vous est pas partage."
            ),
        })

    def _browser_prefix_ok(self):
        """Whether the browser may run on this configuration at all."""
        self.ensure_one()
        return (self.browser_root_prefix or "").strip() not in ("", "/")

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env.registry.clear_cache()
        return records

    def write(self, vals):
        Cred = self.env["bf.nc.user.credential"].sudo()
        moved = Cred
        revocations = []
        if "nextcloud_base_url" in vals:
            # Access first: revocation is a network call no rollback undoes.
            self.check_access("write")
            for cfg in self.sudo():
                if _server_key(cfg.nextcloud_base_url) == _server_key(vals["nextcloud_base_url"]):
                    continue
                creds = Cred.search([("config_id", "=", cfg.id)])
                for cred in creds.filtered(lambda c: c.state == "connected" and c._issued_by(cfg)):
                    # Collected while the OLD address is still readable, sent
                    # only once the write below has accepted the new one. The
                    # same save may delete rows from the connection list: they
                    # are revoked here all the same, on the server that issued
                    # them (their own unlink() refuses to send them anywhere).
                    revocations.append((
                        cfg.nextcloud_base_url, cfg._tls_verify,
                        cred.nc_login, cfg._decrypt_value(cred.app_password_encrypted),
                    ))
                moved |= creds
        # Validates the new address first: a typo is refused before anyone is
        # disconnected.
        res = super().write(vals)
        if moved:
            moved.exists()._drop_without_revoking()
        for base, verify, login, password in revocations:
            Cred._revoke_at(base, verify, login, password)
        if BROWSER_VISIBILITY_FIELDS & set(vals):
            self.env.registry.clear_cache()
        return res

    def unlink(self):
        res = super().unlink()
        self.env.registry.clear_cache()
        return res

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
