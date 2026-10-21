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

import logging
import posixpath
from urllib.parse import quote as url_quote
from urllib.parse import unquote as url_unquote
from urllib.parse import urlparse
from xml.sax.saxutils import escape as xml_escape

import requests

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.bf_document_nextcloud_sync.models.nextcloud_document_config import (
    _sanitize_nc_path,
    _validate_path_under_prefix,
)

from . import nc_names
from .bf_nc_user_credential import _server_key
from .nc_identity import NcNotConnected, NcPersonAuth, NcTokenRejected

_logger = logging.getLogger(__name__)

# Fields whose change can make the browser appear or disappear for someone:
# the menu and the systray button are computed from them, and Odoo caches the
# menu tree per user.
BROWSER_VISIBILITY_FIELDS = {"active", "browser_root_prefix", "nextcloud_base_url"}

# Properties asked of every SEARCH hit: the same ones a listing reads, so the
# parent module's PROPFIND parser turns a search answer into the same entries.
SEARCH_PROPS = (
    "<d:displayname/><d:getlastmodified/><d:getcontentlength/><d:getcontenttype/>"
    "<d:resourcetype/><d:getetag/><oc:fileid/><oc:size/>"
)
SEARCH_MAX_RESULTS = 500

OCS_SHARES = "/ocs/v2.php/apps/files_sharing/api/v1/shares"
SHARE_TYPE_PUBLIC_LINK = 3


def like_literal(term):
    """A search term as the literal of a DAV `d:like`, matching it anywhere.

    `%` and `_` are wildcards there, and Nextcloud honours a backslash escape
    (measured on 34.0.4: `%100\\%%` finds the one name containing "100%").
    Unescaped, they would only widen the match inside the person's own files,
    so this is about correct results, not access. The XML escape is about the
    request itself: the term goes into a request body.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return xml_escape("%" + escaped + "%")


def _share_payload(data):
    """The fields of an OCS share the browser uses, with stable types."""
    try:
        permissions = int(data.get("permissions") or 0)
    except (TypeError, ValueError):
        permissions = 0
    try:
        share_type = int(data.get("share_type"))
    except (TypeError, ValueError):
        share_type = -1
    expiration = data.get("expiration") or ""
    return {
        "id": int(data["id"]),
        "share_type": share_type,
        "token": data.get("token") or "",
        "url": data.get("url") or "",
        "path": data.get("path") or "",
        "item_type": data.get("item_type") or "",
        "permissions": permissions,
        "expiration": expiration[:10],
        "has_password": bool(data.get("password")),
        "share_with": data.get("share_with_displayname") or data.get("share_with") or "",
        "owner": data.get("displayname_owner") or data.get("uid_owner") or "",
        "uid_owner": data.get("uid_owner") or "",
        "uid_file_owner": data.get("uid_file_owner") or "",
        "stime": int(data.get("stime") or 0),
        # Nextcloud says whether the caller may delete it; older servers do not.
        "can_delete": data.get("can_delete", True) is not False,
    }


class NextcloudDocumentConfig(models.Model):
    _inherit = "nextcloud.document.config"

    browser_root_prefix = fields.Char(
        string="Root prefix (browser)",
        default="/",
        help="Every path reachable from the embedded file browser must be "
             "under this prefix (e.g. /Company/). Defense in depth on top "
             "of each record's own folder.",
    )

    # Client folders (18.0.4.2.0). For example, one folder per client under
    # /Company/(Clients)/, one subfolder of (PROJETS) per project.
    nc_clients_root = fields.Char(
        string="Client folders root",
        help="Nextcloud folder holding one folder per client (e.g. "
             "/Company/(Clients)/). A company's folder is chosen there. "
             "Must be under the browser's root prefix.",
    )
    nc_projects_subfolder = fields.Char(
        string="Projects subfolder",
        default="(PROJETS)",
        help="Name of the subfolder, in each client folder, holding one "
             "folder per project. A project's folder is chosen there. "
             "Empty: projects are chosen directly in the client's folder.",
    )

    # Creating a client folder from the record (18.0.4.3.0).
    nc_client_template = fields.Char(
        string="Client folder template",
        help="Nextcloud folder copied to create a new client's folder "
             "(e.g. /Company/(Clients)/(TEMPLATE FOLDER)). Empty: no "
             "creation from Odoo.",
    )
    nc_client_name_pattern = fields.Char(
        string="New client folder name",
        default="{NNN} - {client}",
        help="{NNN}: next number, on at least as many digits as there are "
             "Ns. {client}: the company name, cleaned of the characters "
             "Windows refuses.",
    )

    nc_open_extensions = fields.Char(
        string="Extensions opening Nextcloud",
        default="xlsx,ods,csv,docx,odt,pptx,odp,xls,doc,ppt",
        help="Extensions (comma-separated) whose click opens the file "
             "directly in Nextcloud (e.g. Collabora) rather than in the "
             "built-in preview.",
    )

    nc_folder_color = fields.Char(
        string="Folder color",
        default="#2E3132",
        help="Color (hex) of the folder icons in the browser. Default: "
             "charcoal. Set the accent color (#29ABE2) for contrast.",
    )

    nc_panel_width_pct = fields.Integer(
        string="Panel width (%)",
        default=80,
        help="Width, as a percentage of the window, of the embedded panel "
             "opened from the systray. Starting value only: each person "
             "can then resize the panel, and their preference wins.",
    )

    nc_panel_height_pct = fields.Integer(
        string="Panel height (%)",
        default=80,
        help="Height, as a percentage of the window, of the embedded "
             "panel opened from the systray. Same rule as the width: it "
             "is a starting value, replaced by each person's preference.",
    )

    # Named after the 4.0.2 check, which compared email addresses. The name stays
    # so that configurations keep the choice already made on them.
    nc_require_email_match = fields.Boolean(
        string="Require the person's login",
        default=True,
        help="When a person connects their Nextcloud account, the "
             "approved account (their Nextcloud login or email address) "
             "must match the login of their Odoo user, which only an "
             "administrator can change. Without this check, a login page "
             "forwarded to someone else would connect the sender to the "
             "recipient's files.",
    )

    nc_link_hosts = fields.Char(
        string="Other addresses of this Nextcloud",
        help="Additional hostnames, comma-separated, under which this "
             "same Nextcloud is reached (for example a second domain). "
             "Chatter links that use them are recognized in \"Linked "
             "Nextcloud files\". The configuration's address is always "
             "recognized.",
    )

    nc_user_credential_ids = fields.One2many(
        "bf.nc.user.credential",
        "config_id",
        string="People's connections",
        groups="base.group_system",
    )

    share_preset_ids = fields.One2many(
        "nextcloud.share.preset",
        "config_id",
        string="Share presets",
    )

    @api.constrains("nc_panel_width_pct", "nc_panel_height_pct")
    def _check_nc_panel_size(self):
        for cfg in self:
            for value, dim in (
                (cfg.nc_panel_width_pct, _("width")),
                (cfg.nc_panel_height_pct, _("height")),
            ):
                if value and not 40 <= value <= 100:
                    raise ValidationError(
                        _(
                            "The panel %(dim)s must be between 40 and 100.",
                            dim=dim,
                        )
                    )

    @api.constrains("nc_clients_root", "nc_projects_subfolder", "browser_root_prefix")
    def _check_nc_clients_root(self):
        for cfg in self:
            root = (cfg.nc_clients_root or "").strip()
            if root:
                clean = posixpath.normpath(_sanitize_nc_path(root))
                if clean == "/":
                    raise ValidationError(_("The client folders root "
                                            "cannot be '/'."))
                prefix = (cfg.browser_root_prefix or "").strip()
                if prefix not in ("", "/"):
                    _validate_path_under_prefix(clean, _sanitize_nc_path(prefix))
            sub = (cfg.nc_projects_subfolder or "").strip()
            if sub:
                _sanitize_nc_path("/" + sub)  # %XX and control characters
            if sub and ("/" in sub or "\\" in sub or sub in (".", "..")):
                raise ValidationError(
                    _("The projects subfolder is a folder name, not a path: %s") % sub
                )

    @api.constrains("nc_client_template", "nc_client_name_pattern", "nc_clients_root", "browser_root_prefix")
    def _check_nc_client_template(self):
        for cfg in self:
            tpl = (cfg.nc_client_template or "").strip()
            if tpl:
                clean = posixpath.normpath(_sanitize_nc_path(tpl))
                root = cfg._nc_clients_root_path()
                if not root:
                    raise ValidationError(_("The template requires a "
                                            "client folders root."))
                prefix = (cfg.browser_root_prefix or "").strip()
                if prefix not in ("", "/"):
                    _validate_path_under_prefix(clean, _sanitize_nc_path(prefix))
            pattern = cfg.nc_client_name_pattern or ""
            if pattern and "{client}" not in pattern:
                raise ValidationError(_("A new client folder name must "
                                        "contain {client}."))
            if "/" in pattern or "\\" in pattern:
                raise ValidationError(_("A new client folder name cannot "
                                        "contain \"/\"."))
            if pattern and not nc_names.readable(pattern):
                raise ValidationError(_(
                    "This name could not be read back (a character "
                    "Windows refuses?): numbering would always restart at "
                    "1."
                ))

    def _nc_client_template_path(self):
        """The template folder as an absolute, normalised path, or ""."""
        self.ensure_one()
        tpl = (self.nc_client_template or "").strip()
        return posixpath.normpath(_sanitize_nc_path(tpl)) if tpl else ""

    def _nc_clients_root_path(self):
        """The clients root as an absolute, normalised path, or "" if not set."""
        self.ensure_one()
        root = (self.nc_clients_root or "").strip()
        return posixpath.normpath(_sanitize_nc_path(root)) if root else ""

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
            raise AccessError(_("Nextcloud identity refused."))
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
                _("Connect your Nextcloud account to browse these files.")
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
                "The configuration's WebDAV path must end with /files so "
                "that each person uses their own account."
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
                _("Your Nextcloud connection is unreadable. Reconnect.")
            )
        return NcPersonAuth(cred.nc_login, password, {
            "rejected": _(
                "Nextcloud refused your connection: it was revoked. "
                "Reconnect your account."
            ),
            "unavailable": _(
                "Nextcloud refuses access to your account for now "
                "(account disabled, or server under maintenance)."
            ),
            "not_found": _(
                "This folder cannot be found in your Nextcloud, or it is "
                "not shared with you."
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
        """Seed the two default presets (Internal / External) once per config.

        Their names are stored and shared by everyone who browses with this
        configuration: written in the language of its company, not in the
        language of whoever happens to open the browser first.
        """
        Preset = self.env["nextcloud.share.preset"].sudo()
        for cfg in self:
            if cfg.share_preset_ids:
                continue
            # Only an installed language: a company may keep the code of one
            # deactivated since.
            installed = [code for code, _name in self.env["res.lang"].get_installed()]
            lang = next(
                (code for code in (cfg.company_id.partner_id.lang, self.env.lang, self.env.user.lang)
                 if code and code in installed),
                "en_US" if "en_US" in installed or not installed else installed[0],
            )
            env_lang = cfg.with_context(lang=lang).env
            Preset.create([
                {
                    "config_id": cfg.id,
                    "sequence": 10,
                    "name": env_lang._("Internal"),
                    "access": "read_write",
                    "expiry_days": 0,
                    "password_protected": False,
                },
                {
                    "config_id": cfg.id,
                    "sequence": 20,
                    "name": env_lang._("External"),
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
            raise UserError(_("Error while deleting: %s") % str(e))

        # 404 = already gone, treat as success (idempotent).
        if resp.status_code not in (200, 204, 404):
            raise UserError(_("WebDAV DELETE error: HTTP %s") % resp.status_code)
        return resp

    def _webdav_put_new(self, path, content, content_type="application/octet-stream"):
        """PUT a file only if nothing is there yet (`If-None-Match: *`).

        Returns False when Nextcloud answers 412 (the name is taken), so the
        caller tries the next free name: a listing may be cut short (500
        entries) and two people may file the same name at once, the server is
        the only one that knows. Used to file chatter attachments (18.0.4.3.0).
        """
        self.ensure_one()
        path = _sanitize_nc_path(path)
        url = self.webdav_url.rstrip("/") + url_quote(path)
        try:
            resp = requests.put(
                url,
                data=content,
                headers={"Content-Type": content_type, "If-None-Match": "*"},
                auth=self._get_auth(),
                timeout=120,
                verify=self._tls_verify,
            )
        except requests.RequestException as e:
            raise UserError(_("Error while storing the file: %s") % str(e))
        if resp.status_code == 412:
            return False
        if resp.status_code not in (200, 201, 204):
            raise UserError(_("Nextcloud refused the file: HTTP %s") % resp.status_code)
        return True

    def _webdav_copy(self, src_path, dst_path):
        """Copy a folder (recursively) or a file via WebDAV COPY, never over
        something that exists (Overwrite: F). Used to create a client folder
        from the template (18.0.4.3.0)."""
        self.ensure_one()
        src_path = _sanitize_nc_path(src_path)
        dst_path = _sanitize_nc_path(dst_path)
        src_url = self.webdav_url.rstrip("/") + url_quote(src_path)
        dst_url = self.webdav_url.rstrip("/") + url_quote(dst_path)
        try:
            resp = requests.request(
                "COPY",
                src_url,
                headers={"Destination": dst_url, "Overwrite": "F", "Depth": "infinity"},
                auth=self._get_auth(),
                timeout=120,
                verify=self._tls_verify,
            )
        except requests.RequestException as e:
            raise UserError(_("Error while copying: %s") % str(e))
        if resp.status_code == 412:
            raise UserError(_("A folder with this name already exists at "
                              "the destination."))
        if resp.status_code == 404:
            raise UserError(_("The template cannot be found in your "
                              "Nextcloud: %s") % src_path)
        if resp.status_code not in (201, 204):
            raise UserError(_("WebDAV COPY error: HTTP %s") % resp.status_code)
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
            raise UserError(_("Error while moving: %s") % str(e))

        if resp.status_code == 412:
            raise UserError(
                _("A file or folder with this name already exists at the "
                  "destination.")
            )
        if resp.status_code not in (201, 204):
            raise UserError(_("WebDAV MOVE error: HTTP %s") % resp.status_code)
        return resp

    # ------------------------------------------------------------------
    # Links (18.0.4.1.0)
    # ------------------------------------------------------------------
    def _internal_link(self, file_id):
        """The `/f/<id>` link: it opens only for someone who can already see the file."""
        self.ensure_one()
        return "%s/f/%d" % (self.nextcloud_base_url.rstrip("/"), int(file_id))

    def _link_hosts(self):
        """Lower-case host names under which links point at this Nextcloud."""
        self.ensure_one()
        hosts = set()
        own = urlparse(self.nextcloud_base_url or "").hostname
        if own:
            hosts.add(own.lower())
        for raw in (self.nc_link_hosts or "").replace(";", ",").split(","):
            raw = raw.strip()
            if not raw:
                continue
            host = urlparse(raw if "://" in raw else "https://" + raw).hostname
            if host:
                hosts.add(host.lower())
        return hosts

    # ------------------------------------------------------------------
    # Search (18.0.4.1.0)
    # ------------------------------------------------------------------
    def _dav_search_target(self):
        """(SEARCH endpoint, scope of the account) for whoever the call speaks as.

        `webdav_url` already names the right account (the person's, or the
        configuration's), as `<dav root>/files/<account>/`. SEARCH is sent to
        the DAV root, and its scope is `/files/<account>/<path>`.
        """
        url = self.webdav_url.rstrip("/")
        cut = url.rfind("/files/")
        if cut < 0:
            raise UserError(_(
                "The configuration's WebDAV path must end with /files so "
                "that the search knows which files to search."
            ))
        account = url_unquote(url[cut + len("/files/"):])
        return url[: cut + 1], "/files/" + account

    def _webdav_search(self, scope_path, where_xml, limit=100):
        """Run a DAV SEARCH under `scope_path` and parse it like a listing.

        `where_xml` is trusted: callers build it from escaped literals only.

        ⚠️ The scope is sent as a plain path, NOT percent-encoded: Nextcloud
        34.0.4 answers 404 to `/files/x/Blue%20Fox` and 207 to `/files/x/Blue Fox`.
        It still goes through the XML escape, like any text in the body.
        """
        self.ensure_one()
        scope_path = _sanitize_nc_path(scope_path or "/")
        endpoint, account = self._dav_search_target()
        scope = account + ("" if scope_path.rstrip("/") == "" else scope_path.rstrip("/"))
        limit = max(1, min(int(limit), SEARCH_MAX_RESULTS))
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<d:searchrequest xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
            "<d:basicsearch>"
            f"<d:select><d:prop>{SEARCH_PROPS}</d:prop></d:select>"
            "<d:from><d:scope>"
            f"<d:href>{xml_escape(scope)}</d:href><d:depth>infinity</d:depth>"
            "</d:scope></d:from>"
            f"<d:where>{where_xml}</d:where>"
            "<d:orderby><d:order><d:prop><d:getlastmodified/></d:prop>"
            "<d:descending/></d:order></d:orderby>"
            f"<d:limit><d:nresults>{limit}</d:nresults></d:limit>"
            "</d:basicsearch>"
            "</d:searchrequest>"
        )
        try:
            resp = requests.request(
                "SEARCH",
                endpoint,
                data=body.encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=utf-8"},
                auth=self._get_auth(),
                timeout=30,
                verify=self._tls_verify,
            )
        except requests.Timeout:
            raise UserError(_("The Nextcloud search took too long (30 s)."))
        except requests.RequestException as e:
            raise UserError(_("Error during the Nextcloud search: %s") % str(e))
        if resp.status_code == 404:
            # The scope itself does not exist in this account.
            return []
        if resp.status_code != 207:
            _logger.warning("SEARCH %s returned %s", scope_path, resp.status_code)
            raise UserError(_("Nextcloud search error: HTTP %s") % resp.status_code)
        return self._parse_propfind_response(resp.text, scope_path)

    # ------------------------------------------------------------------
    # Shares (18.0.4.1.0)
    # ------------------------------------------------------------------
    def _ocs_request(self, method, path, params=None):
        self.ensure_one()
        url = self.nextcloud_base_url.rstrip("/") + OCS_SHARES + path
        try:
            return requests.request(
                method,
                url,
                params={**(params or {}), "format": "json"},
                headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                auth=self._get_auth(),
                timeout=30,
                verify=self._tls_verify,
            )
        except requests.RequestException as e:
            raise UserError(_("Nextcloud OCS error: %s") % str(e))

    @staticmethod
    def _ocs_data(resp):
        try:
            return resp.json()["ocs"]["data"]
        except (ValueError, KeyError, TypeError):
            raise UserError(_("Unreadable answer from Nextcloud (shares)."))

    def _ocs_shares(self, path=None, subfiles=False):
        """Shares on the caller's files: every one, or those of `path`.

        `subfiles` asks for the shares of a folder's CHILDREN and leaves out the
        folder's own share, which is
        what a listing needs and nothing else does. `reshares` adds the shares
        others made of the caller's files.
        """
        params = {"reshares": "true"}
        if path:
            params["path"] = _sanitize_nc_path(path)
        if subfiles:
            params["subfiles"] = "true"
        resp = self._ocs_request("GET", "", params)
        if resp.status_code == 404:
            return []  # the path is not in this account
        if resp.status_code != 200:
            raise UserError(_("OCS error (share list): HTTP %s") % resp.status_code)
        data = self._ocs_data(resp)
        return [_share_payload(d) for d in data if isinstance(d, dict) and d.get("id")]

    def _ocs_share(self, share_id):
        """One share, read alone: the list has already reported no expiration
        for shares that had one (reference-nc-share-list-endpoint-omits-expiration)."""
        resp = self._ocs_request("GET", "/%d" % int(share_id))
        if resp.status_code in (403, 404):
            return None
        if resp.status_code != 200:
            raise UserError(_("OCS error (share): HTTP %s") % resp.status_code)
        data = self._ocs_data(resp)
        if isinstance(data, list):
            data = data[0] if data else None
        return _share_payload(data) if isinstance(data, dict) and data.get("id") else None

    def _ocs_delete_share(self, share_id):
        resp = self._ocs_request("DELETE", "/%d" % int(share_id))
        if resp.status_code == 404:
            return False  # already gone
        if resp.status_code == 403:
            raise UserError(_("Nextcloud does not let you remove this share."))
        if resp.status_code != 200:
            raise UserError(_("OCS error (share removal): HTTP %s") % resp.status_code)
        return True
