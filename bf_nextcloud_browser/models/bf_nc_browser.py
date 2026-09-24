"""RPC facade for the embedded Nextcloud file browser.

The OWL widget and the streaming controller talk to Nextcloud ONLY through this
model. Every entry point:
  * checks the caller is in group_nc_browser_user;
  * resolves the config + folder root from the *record* (project / task), never
    from a client-supplied path or config id (prevents IDOR);
  * forces every requested path to stay under that record root, which itself must
    stay under the config browser_root_prefix;
  * refuses a path or a name that would still decode (see _plain).

Two reads go wider than the root, on purpose, since 18.0.4.1.0: the files cited
in a chatter are resolved in the person's whole Nextcloud account, and a share
cited there can be revoked wherever its file lives (see _linked_files and
linked_revoke_share). Both speak as the person, so Nextcloud shows and allows
nothing it would not show or allow them for the same link.

All actual WebDAV/OCS calls reuse the hardened helpers on
nextcloud.document.config (PROPFIND/GET/PUT/MKCOL/DELETE/MOVE/share).

Since 18.0.4.0.0 those calls are made AS THE CALLER: the resolved configuration
is returned in sudo (so a member of the group who is not an administrator can
read the connection fields at all) and with `bf_nc_as_user` in its context (so
the helpers use the caller's own Nextcloud account, see nextcloud_document_config).
Record access is still checked as the caller, before any of this.
"""

import base64
import logging
import mimetypes
import posixpath
import functools
import re
import secrets
import string
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import unquote as url_unquote
from urllib.parse import urlparse

import pytz
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.bf_document_nextcloud_sync.models.nextcloud_document_config import (
    _sanitize_nc_path,
    _validate_path_under_prefix,
)

from .nextcloud_document_config import SHARE_TYPE_PUBLIC_LINK, like_literal

_logger = logging.getLogger(__name__)

GROUP = "bf_nextcloud_browser.group_nc_browser_user"
ALLOWED_MODELS = ("project.project", "project.task")
# Modification times are shown in the person's own time zone, never in UTC.
DEFAULT_TZ = "America/Montreal"
# Cap RPC uploads (base64 in a single call) to protect the worker's memory.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024  # 64 MiB

# Search: below two characters every name matches; a hundred answers came back
# in under a second on a real server, a thousand could take many seconds under load.
SEARCH_MIN_CHARS = 2
SEARCH_MAX_CHARS = 100
SEARCH_LIMIT = 100

# Linked files: how much of a chatter is read, and how many links resolved.
LINKED_MESSAGE_LIMIT = 1000
LINKED_FILE_LIMIT = 200
# Path segments that make an /f/ or /s/ something else than a Nextcloud link.
NOT_A_LINK_PREFIX = {"apps", "remote.php", "public.php", "dav", "files", "ocs", "webdav"}


@functools.lru_cache(maxsize=16)
def link_re(hosts):
    """A Nextcloud link to one of `hosts` (a sorted tuple) in a message body.

    Groups: host, path before the link (a sub-path install), kind (f or s),
    id or token. The scheme is optional: on a real database, a share of the
    internal links cited in chatters were written without it.

    Every repetition is bounded: an unbounded path group made the cost grow
    with the square of a hostile body ("host/a," repeated: 100 KB in 8 s,
    adversarial review). A sub-path install has one or two segments; a token
    may carry dashes (custom share tokens), never at its ends.
    """
    alternatives = "|".join(re.escape(h) for h in sorted(hosts, key=len, reverse=True))
    return re.compile(
        r"(?<![A-Za-z0-9.@/?&=-])(?:https?://)?(%s)(?::\d{1,5})?((?:/[^\s\"'<>/]{1,64}){0,2}?)"
        r"/(?:index\.php/)?(f|s)/([0-9]{1,19}|[A-Za-z0-9][A-Za-z0-9-]{6,62}[A-Za-z0-9])(?![A-Za-z0-9-])"
        % alternatives,
        re.IGNORECASE,
    )

# A message body longer than this is read up to it for links. Imported emails
# quote whole threads: on a real database, share links sat past 200 KB, in
# bodies several times that size. With the bounded pattern a hostile 200 KB body
# costs ~30 ms.
LINKED_BODY_MAX = 1_000_000
# And no more than this many characters read in one call, all messages together.
LINKED_SCAN_BUDGET = 20_000_000
# Shares read alone for their expiry, at most, per call: the list is used past it.
SHARE_REREAD_MAX = 20


def _text(value, label):
    """A client argument as text, or a clean refusal instead of a trace."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise UserError(_("%s invalide.") % label)
    return value


def _plain(value):
    """`value` unchanged, or a refusal if it still decodes.

    `_sanitize_nc_path` url-decodes a path before validating it, and every
    WebDAV/OCS helper of bf_document_nextcloud_sync calls it AGAIN on the
    validated path. A name like `%252e%252e` therefore passes the check as
    `%2e%2e` and reaches Nextcloud as `..`, one folder up, outside the root.
    Found by the adversarial review of 18.0.4.1.0; the flaw dates from 3.x, and
    a colleague can create such a folder name in a shared folder. A real
    Nextcloud name holding a percent escape is refused, with a message, instead
    of being addressed to another file.
    """
    if isinstance(value, str) and value and url_unquote(value) != value:
        raise UserError(
            _("« %s » contient une séquence %%XX : le navigateur ne prend pas ce nom en charge.")
            % value
        )
    return value


def _xml_text(value):
    """Only the characters XML 1.0 accepts (a lone surrogate or a control character
    would make the request unencodable, or Nextcloud answer 400)."""
    return "".join(
        ch for ch in value
        if ch in "\t\n\r" or (
            ord(ch) >= 0x20 and not 0xD800 <= ord(ch) <= 0xDFFF and ord(ch) not in (0xFFFE, 0xFFFF)
        )
    )


SHARE_KINDS = {
    0: "Personne",
    1: "Groupe",
    3: "Lien public",
    4: "Courriel",
    6: "Fédéré",
    7: "Cercle",
    10: "Salon Talk",
    12: "Équipe",
}


def _human_size(num):
    num = num or 0
    if num < 1024:
        return "%d o" % num
    val = float(num)
    for unit in ("Ko", "Mo", "Go", "To"):
        val /= 1024.0
        if abs(val) < 1024.0:
            return "%.1f %s" % (val, unit)
    return "%.1f Po" % val


class BfNcBrowser(models.TransientModel):
    """Stateless facade — no records are ever stored; methods are @api.model."""

    _name = "bf.nc.browser"
    _description = "Facade RPC navigateur de fichiers Nextcloud"

    # ------------------------------------------------------------------
    # Access & resolution
    # ------------------------------------------------------------------
    @staticmethod
    def _as_id(value):
        """An RPC id argument as an int, or a clean refusal instead of a trace."""
        try:
            return int(value)
        except (TypeError, ValueError):
            raise UserError(_("Identifiant invalide."))

    def _check_access(self):
        if not self.env.user.has_group(GROUP):
            raise AccessError(_("Acces au navigateur Nextcloud non autorise."))

    def _default_config(self):
        # sudo: the connection fields are system-only and the model is not
        # readable by every browser user; access is decided by the group.
        Config = self.env["nextcloud.document.config"].sudo()
        cfg_id = self.env["ir.config_parameter"].sudo().get_param(
            "bf_document_nextcloud_sync.default_config_id"
        )
        if cfg_id:
            cfg = Config.browse(int(cfg_id)).exists()
            if cfg:
                return cfg
        configs = Config.search([], limit=2)
        return configs if len(configs) == 1 else Config.browse()

    def _resolve_record(self, model, res_id):
        if model not in ALLOWED_MODELS:
            raise ValidationError(_("Modele non supporte: %s") % model)
        record = self.env[model].browse(self._as_id(res_id))
        if not record.exists():
            raise UserError(_("Enregistrement introuvable."))
        record.check_access("read")
        return record

    def _as_person(self, config):
        """The configuration, speaking as the caller (see module docstring)."""
        return config.sudo().with_context(bf_nc_as_user=self.env.uid)

    def _record_config(self, model, res_id):
        record = self._resolve_record(model, res_id)
        config = record.nc_documents_config_id or self._default_config()
        return record, config.sudo()

    def _resolve_root(self, model, res_id):
        """Return (config, root_path) for a record, or raise if not configured."""
        self._check_access()
        record, config = self._record_config(model, res_id)
        if not config:
            raise UserError(_("Aucune configuration Nextcloud disponible."))
        root = record.nc_documents_folder
        if not root:
            raise UserError(
                _("Aucun dossier Nextcloud n'est configure pour cet enregistrement.")
            )
        root = _sanitize_nc_path(_plain(root))
        if posixpath.normpath(root) == "/":
            raise UserError(
                _("Le dossier Nextcloud de l'enregistrement ne peut pas etre la racine '/'.")
            )
        # The browser prefix is a mandatory backstop (never the bare '/' default):
        # every record root must sit under an explicit, non-root prefix so a
        # misconfigured folder can never expose the whole service account.
        prefix = (config.browser_root_prefix or "").strip()
        if prefix in ("", "/"):
            raise UserError(_(
                "Le navigateur requiert un prefixe racine. Definissez "
                "« Prefixe racine (navigateur) » (ex: /Entreprise/) sur la "
                "configuration Nextcloud."
            ))
        _validate_path_under_prefix(root, _sanitize_nc_path(_plain(prefix)))
        return self._as_person(config), root

    def _resolve_path(self, model, res_id, rel_path):
        """Resolve a client relative path to a sanitised absolute NC path."""
        config, root = self._resolve_root(model, res_id)
        rel = _plain(_text(rel_path, _("Chemin")).strip().lstrip("/"))
        abs_path = _sanitize_nc_path(posixpath.join(root, rel)) if rel else root
        _validate_path_under_prefix(abs_path, root)
        return config, abs_path, root

    # ------------------------------------------------------------------
    # Standalone scope (no record) — used by the client-action app.
    # The security boundary is the config browser_root_prefix; there is no
    # record root, so we refuse to operate unless a meaningful prefix is set
    # (defence against accidentally exposing the whole service-account NC).
    # ------------------------------------------------------------------
    def _resolve_root_standalone(self):
        self._check_access()
        config = self._default_config()
        if not config:
            raise UserError(_("Aucune configuration Nextcloud disponible."))
        prefix = (config.browser_root_prefix or "").strip()
        if prefix in ("", "/"):
            raise UserError(_(
                "Le navigateur autonome requiert un prefixe racine. Definissez "
                "« Prefixe racine (navigateur) » (ex: /Entreprise/) sur la "
                "configuration Nextcloud avant d'utiliser cette application."
            ))
        return self._as_person(config), _sanitize_nc_path(_plain(prefix))

    def _resolve_path_standalone(self, rel_path):
        config, root = self._resolve_root_standalone()
        rel = _plain(_text(rel_path, _("Chemin")).strip().lstrip("/"))
        abs_path = _sanitize_nc_path(posixpath.join(root, rel)) if rel else root
        _validate_path_under_prefix(abs_path, root)
        return config, abs_path, root

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _dav_root(self, config):
        # Both sides unquoted: a per-person account id is quoted in webdav_url
        # (an '@' becomes %40) and Nextcloud may or may not quote it in <href>.
        return url_unquote(urlparse(config.webdav_url).path).rstrip("/")

    def _href_to_nc_path(self, config, href, dav_root=None):
        """Convert a PROPFIND <href> back to a Nextcloud absolute path.

        Pass `dav_root` when converting a whole listing: webdav_url looks the
        person's connection up, and a listing has up to 500 entries.
        """
        if dav_root is None:
            dav_root = self._dav_root(config)
        p = url_unquote(urlparse(href).path)
        if p.startswith(dav_root):
            p = p[len(dav_root):]
        if not p.startswith("/"):
            p = "/" + p
        return posixpath.normpath(p)

    @api.model
    def get_panel_config(self):
        """Whether the systray panel opens, and at what size (group members only).

        Returns an empty dict when the caller has no business seeing the button:
        no group, or no active storage config to browse. The systray keeps
        itself hidden on anything falsy, so a failure here is never a broken
        button.
        """
        if not self.env.user.has_group(GROUP):
            return {}
        # The same configuration the panel will open, held to the same rule:
        # the button used to test "any active configuration", so it showed on
        # an instance whose browser refuses to run (a root prefix of "/").
        config = self._browsable_config()
        if not config:
            return {}
        return {
            "available": True,
            "width_pct": config.nc_panel_width_pct or 80,
            "height_pct": config.nc_panel_height_pct or 80,
        }

    @api.model
    def _browsable_config(self):
        """The default configuration if the standalone browser can run on it."""
        config = self._default_config()
        if config and config.active and config._browser_prefix_ok():
            return config
        return self.env["nextcloud.document.config"].sudo().browse()

    # ------------------------------------------------------------------
    # Per-person Nextcloud connection
    # ------------------------------------------------------------------
    def _connection_payload(self, config, extra=None):
        cred = self.env["bf.nc.user.credential"]._for(config)
        return {
            **cred._status(),
            "server": config.nextcloud_base_url or "",
            **(extra or {}),
        }

    def _scope_config(self, model=None, res_id=None):
        self._check_access()
        if model:
            _record, config = self._record_config(model, res_id)
        else:
            config = self._default_config()
        if not config:
            raise UserError(_("Aucune configuration Nextcloud disponible."))
        return config

    @api.model
    def nc_status(self, model, res_id):
        return self._connection_payload(self._scope_config(model, res_id))

    @api.model
    def nc_connect_start(self, model, res_id):
        config = self._scope_config(model, res_id)
        return self.env["bf.nc.user.credential"]._flow_start(config)

    @api.model
    def nc_connect_poll(self, model, res_id):
        config = self._scope_config(model, res_id)
        res = self.env["bf.nc.user.credential"]._flow_poll(config)
        return {**res, "server": config.nextcloud_base_url or ""}

    @api.model
    def nc_connect_cancel(self, model, res_id):
        config = self._scope_config(model, res_id)
        cred = self.env["bf.nc.user.credential"]._for(config)
        if cred:
            cred._flow_clear()
            if cred.state == "pending":
                cred.unlink()
        return self._connection_payload(config)

    @api.model
    def nc_disconnect(self, model, res_id):
        config = self._scope_config(model, res_id)
        # unlink() deletes the app password on Nextcloud too.
        self.env["bf.nc.user.credential"]._for(config).unlink()
        return self._connection_payload(config)

    @api.model
    def root_nc_status(self):
        return self._connection_payload(self._scope_config())

    @api.model
    def root_nc_connect_start(self):
        return self.env["bf.nc.user.credential"]._flow_start(self._scope_config())

    @api.model
    def root_nc_connect_poll(self):
        config = self._scope_config()
        res = self.env["bf.nc.user.credential"]._flow_poll(config)
        return {**res, "server": config.nextcloud_base_url or ""}

    @api.model
    def root_nc_connect_cancel(self):
        return self.nc_connect_cancel(None, None)

    @api.model
    def root_nc_disconnect(self):
        return self.nc_disconnect(None, None)

    # ------------------------------------------------------------------
    # Browse
    # ------------------------------------------------------------------
    @api.model
    def browse_dir(self, model, res_id, rel_path=""):
        config, abs_path, root = self._resolve_path(model, res_id, rel_path)
        return self._list_dir(config, abs_path, root)

    def _tz(self):
        try:
            return pytz.timezone(self.env.user.tz or DEFAULT_TZ)
        except pytz.UnknownTimeZoneError:
            return pytz.timezone(DEFAULT_TZ)

    def _entries(self, config, raw, root, skip=()):
        """PROPFIND or SEARCH hits as browser entries, kept strictly under `root`.

        `skip` lists absolute paths not to return (a listing's own folder).
        """
        dav_root = self._dav_root(config)
        tz = self._tz()
        skip = {posixpath.normpath(p) for p in skip}
        base = (config.nextcloud_base_url or "").rstrip("/")
        entries = []
        for e in raw:
            nc_path = self._href_to_nc_path(config, e.get("href", ""), dav_root)
            if posixpath.normpath(nc_path) in skip:
                continue
            try:
                _validate_path_under_prefix(nc_path, root)
            except ValidationError:
                continue
            iso = ""
            lastmod = e.get("last_modified")
            if lastmod:
                try:
                    dt = parsedate_to_datetime(lastmod)
                    if dt.tzinfo is None:
                        dt = pytz.utc.localize(dt)
                    iso = dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")
                except (TypeError, ValueError):
                    iso = lastmod
            is_dir = bool(e.get("is_dir"))
            fid = e.get("file_id")
            internal_url = ""
            if fid and not is_dir and base:
                internal_url = base + "/f/" + str(fid)
            name = e.get("name") or posixpath.basename(nc_path)
            rel = posixpath.relpath(nc_path, root)
            entries.append({
                "name": name,
                # Nextcloud masque les fichiers points par defaut dans sa propre
                # interface. On les renvoie quand meme, marques : c'est le
                # client qui choisit de les afficher, sans second aller-retour.
                "is_hidden": name.startswith("."),
                "is_dir": is_dir,
                "size": e.get("size", 0),
                "size_display": "" if is_dir else _human_size(e.get("size", 0)),
                "mtime": iso,
                "content_type": e.get("content_type", ""),
                "rel": rel,
                "parent_rel": "" if posixpath.dirname(rel) in ("", ".") else posixpath.dirname(rel),
                "file_id": fid or 0,
                "internal_url": internal_url,
                # The internal link of a folder opens it in Nextcloud too; the
                # listing keeps `internal_url` for files, which drives the
                # Collabora click.
                "link_url": (base + "/f/" + str(fid)) if (fid and base) else "",
            })
        return entries

    def _list_dir(self, config, abs_path, root):
        """Render one directory listing (shared by record + standalone scopes).
        Inputs are already-resolved, validated absolute paths."""
        raw = config._webdav_propfind(abs_path, depth="1")
        abs_norm = posixpath.normpath(abs_path)
        root_norm = posixpath.normpath(root)
        entries = self._entries(config, raw, root, skip=(abs_path,))
        entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

        cur_rel = "" if abs_norm == root_norm else posixpath.relpath(abs_norm, root)
        crumbs = [{"name": "Racine", "rel": ""}]
        if cur_rel and cur_rel != ".":
            acc = ""
            for part in cur_rel.split("/"):
                acc = posixpath.join(acc, part) if acc else part
                crumbs.append({"name": part, "rel": acc})

        config._ensure_share_presets()
        return {
            "ok": True,
            # Le client s'en sert pour refuser un fichier trop gros avant de le
            # lire en memoire; le serveur revalide de toute facon.
            "max_upload_bytes": MAX_UPLOAD_BYTES,
            "rel_path": "" if cur_rel in ("", ".") else cur_rel,
            "breadcrumb": crumbs,
            "entries": entries,
            "open_extensions": config._open_extensions_list(),
            "folder_color": config.nc_folder_color or "#2E3132",
            "presets": self._preset_payload(config),
        }

    def _preset_payload(self, config):
        """Presets as the screen describes them: the rules a share will REALLY get.

        A preset without expiry falls back to the configuration's default (at
        least one day, enforced), and the configuration's password switch adds a
        password to every share. When both apply, an « Interne » preset (0 day,
        no password) gives a link that expires in 30 days, with a password.
        """
        return [
            {
                "id": p.id,
                "name": p.name,
                "access": p.access,
                "expiry_days": p.expiry_days if p.expiry_days and p.expiry_days > 0
                else (config.default_share_expiry_days or 0),
                "password_protected": bool(p.password_protected or config.share_password_enabled),
            }
            for p in config.share_preset_ids
        ]

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------
    @api.model
    def make_folder(self, model, res_id, rel_path, name):
        name = _plain(_text(name, _("Nom de dossier")).strip().strip("/"))
        if not name or "/" in name:
            raise UserError(_("Nom de dossier invalide."))
        config, parent_abs, root = self._resolve_path(model, res_id, rel_path)
        target = _sanitize_nc_path(posixpath.join(parent_abs, name))
        _validate_path_under_prefix(target, root)
        config._webdav_mkcol(target)
        return {"ok": True}

    @api.model
    def rename_entry(self, model, res_id, rel_path, new_name):
        new_name = _plain(_text(new_name, _("Nom")).strip().strip("/"))
        if not new_name or "/" in new_name:
            raise UserError(_("Nom invalide."))
        config, abs_path, root = self._resolve_path(model, res_id, rel_path)
        if posixpath.normpath(abs_path) == posixpath.normpath(root):
            raise UserError(_("Impossible de renommer le dossier racine."))
        dst = _sanitize_nc_path(posixpath.join(posixpath.dirname(abs_path), new_name))
        _validate_path_under_prefix(dst, root)
        config._webdav_move(abs_path, dst)
        return {"ok": True}

    @api.model
    def move_entry(self, model, res_id, src_rel, dst_dir_rel):
        config, src_abs, root = self._resolve_path(model, res_id, src_rel)
        _, dst_dir_abs, _ = self._resolve_path(model, res_id, dst_dir_rel)
        dst = _sanitize_nc_path(posixpath.join(dst_dir_abs, posixpath.basename(src_abs)))
        _validate_path_under_prefix(dst, root)
        if posixpath.normpath(dst) == posixpath.normpath(src_abs):
            return {"ok": True}
        config._webdav_move(src_abs, dst)
        return {"ok": True}

    @api.model
    def delete_entry(self, model, res_id, rel_path):
        config, abs_path, root = self._resolve_path(model, res_id, rel_path)
        if posixpath.normpath(abs_path) == posixpath.normpath(root):
            raise UserError(_("Impossible de supprimer le dossier racine."))
        config._webdav_delete(abs_path)
        return {"ok": True}

    @api.model
    def upload_file(self, model, res_id, rel_path, filename, data_b64):
        filename = _plain(_text(filename, _("Nom de fichier")).strip().strip("/"))
        if not filename or "/" in filename:
            raise UserError(_("Nom de fichier invalide."))
        config, dir_abs, root = self._resolve_path(model, res_id, rel_path)
        target = _sanitize_nc_path(posixpath.join(dir_abs, filename))
        _validate_path_under_prefix(target, root)
        try:
            content = base64.b64decode(data_b64 or "")
        except (ValueError, TypeError):
            raise UserError(_("Contenu de fichier invalide."))
        if len(content) > MAX_UPLOAD_BYTES:
            raise UserError(
                _("Fichier trop volumineux (maximum %s Mo par televersement).")
                % (MAX_UPLOAD_BYTES // (1024 * 1024))
            )
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        config._webdav_put(target, content, content_type=ctype)
        return {"ok": True}

    def _path_is_dir(self, config, abs_path):
        """Server-side check whether a path is a collection (folder), via a
        depth-0 PROPFIND. Used so share permissions never trust a client flag."""
        try:
            items = config._webdav_propfind(abs_path, depth="0")
        except Exception:  # pragma: no cover - defensive
            return False
        for it in items:
            return bool(it.get("is_dir"))
        return False

    def _share_kwargs(self, config, preset_id, abs_path):
        """Map a share preset to _ocs_create_share kwargs (permissions /
        expire_date / password). Falls back to the config defaults when no
        preset is given. is_dir is derived server-side, never from the client."""
        if not preset_id:
            return {}
        preset = self.env["nextcloud.share.preset"].browse(self._as_id(preset_id))
        if not preset.exists() or preset.config_id != config:
            return {}
        is_dir = self._path_is_dir(config, abs_path)
        kwargs = {"permissions": preset.file_permissions(is_dir=is_dir)}
        if preset.expiry_days and preset.expiry_days > 0:
            expire = datetime.now() + timedelta(days=preset.expiry_days)
            kwargs["expire_date"] = expire.strftime("%Y-%m-%d")
        if preset.password_protected:
            alphabet = string.ascii_letters + string.digits
            kwargs["password"] = "".join(secrets.choice(alphabet) for _ in range(16))
        return kwargs

    @api.model
    def create_share(self, model, res_id, rel_path, preset_id=None, is_dir=False):
        # is_dir from the client is ignored; _share_kwargs derives it server-side.
        config, abs_path, root = self._resolve_path(model, res_id, rel_path)
        result = config._ocs_create_share(
            abs_path, **self._share_kwargs(config, preset_id, abs_path)
        )
        return {
            "ok": True,
            "url": result.get("url"),
            "password": result.get("password"),
            "expire_date": result.get("expire_date"),
        }

    # ------------------------------------------------------------------
    # Knowledge / others integration
    # ------------------------------------------------------------------
    @api.model
    def knowledge_targets(self, model, res_id):
        """List the Knowledge Matrix items (and Knowledge articles if available)
        a file can be linked to for the current record's project."""
        self._resolve_root(model, res_id)  # access + configured guard
        record = self._resolve_record(model, res_id)
        project = record if model == "project.project" else record.project_id

        items = []
        if project:
            kitems = self.env["project.knowledge.item"].search(
                [("project_id", "=", project.id)], limit=200
            )
            items = [{"id": i.id, "name": i.display_name} for i in kitems]

        articles = []
        Article = self.env.get("knowledge.article")
        if Article is not None:
            try:
                articles = [
                    {"id": a.id, "name": a.display_name}
                    for a in Article.search([], limit=200)
                ]
            except Exception:  # pragma: no cover - defensive
                articles = []

        return {
            "ok": True,
            "items": items,
            "articles": articles,
            "has_articles": Article is not None,
        }

    @api.model
    def link_to_knowledge_item(self, model, res_id, rel_path, item_id, mode="share", preset_id=None):
        config, abs_path, root = self._resolve_path(model, res_id, rel_path)
        item = self.env["project.knowledge.item"].browse(self._as_id(item_id))
        if not item.exists():
            raise UserError(_("Element de matrice introuvable."))
        item.check_access("write")

        link = self._make_link(config, abs_path, root, mode, preset_id)
        name = posixpath.basename(abs_path) or "Fichier Nextcloud"
        att = self.env["ir.attachment"].create({
            "name": name,
            "type": "url",
            "url": link["url"],
            "res_model": "project.knowledge.item",
            "res_id": item.id,
        })
        item.write({"attachment_ids": [(4, att.id)]})
        return {**link, "label": item.display_name}

    @api.model
    def link_to_article(self, model, res_id, rel_path, article_id, mode="share", preset_id=None):
        Article = self.env.get("knowledge.article")
        if Article is None:
            raise UserError(
                _("L'application Odoo Knowledge n'est pas installee sur cette instance.")
            )
        config, abs_path, root = self._resolve_path(model, res_id, rel_path)
        article = Article.browse(self._as_id(article_id))
        if not article.exists():
            raise UserError(_("Article introuvable."))
        article.check_access("write")

        link = self._make_link(config, abs_path, root, mode, preset_id)
        url = link["url"]
        name = posixpath.basename(abs_path) or "Fichier Nextcloud"
        # Markup %-substitution HTML-escapes url + name (prevents stored XSS
        # from a crafted filename or share URL).
        snippet = Markup(
            '<p>&#128206; <a href="%s" target="_blank" rel="noopener">%s</a></p>'
        ) % (url, name)
        article.write({"body": Markup(article.body or "") + snippet})
        return {**link, "label": article.display_name}

    # ------------------------------------------------------------------
    # Search (18.0.4.1.0)
    # ------------------------------------------------------------------
    def _search(self, config, root, term):
        """Names containing `term`, anywhere under `root`, as the person.

        Nextcloud compares without case or accents ("ecole" finds "École").
        Names only: the servers measured have no full-text search app.
        """
        term = _xml_text(_text(term, _("Terme de recherche"))).strip()[:SEARCH_MAX_CHARS]
        if len(term) < SEARCH_MIN_CHARS:
            return {"ok": True, "term": term, "too_short": True, "entries": [], "truncated": False}
        where = (
            "<d:like><d:prop><d:displayname/></d:prop>"
            "<d:literal>%s</d:literal></d:like>" % like_literal(term)
        )
        # One more than shown, to know whether there were more.
        raw = config._webdav_search(root, where, limit=SEARCH_LIMIT + 1)
        entries = self._entries(config, raw, root, skip=(root,))
        return {
            "ok": True,
            "term": term,
            "too_short": False,
            "entries": entries[:SEARCH_LIMIT],
            "truncated": len(raw) > SEARCH_LIMIT,
        }

    @api.model
    def search_entries(self, model, res_id, term):
        config, root = self._resolve_root(model, res_id)
        return self._search(config, root, term)

    @api.model
    def root_search_entries(self, term):
        config, root = self._resolve_root_standalone()
        return self._search(config, root, term)

    # ------------------------------------------------------------------
    # Links to paste in a message (18.0.4.1.0)
    # ------------------------------------------------------------------
    def _make_link(self, config, abs_path, root, mode, preset_id=None):
        """An internal `/f/` link, or a share made with a preset.

        The person chooses each time. An internal link opens only for someone
        who can already see the file in Nextcloud; a share opens for anyone who
        has the link, until it expires.
        """
        name = posixpath.basename(abs_path.rstrip("/")) or abs_path
        if mode == "internal":
            items = config._webdav_propfind(abs_path, depth="0")
            fid = items[0].get("file_id") if items else None
            if not fid:
                raise UserError(_("Nextcloud n'a pas donné l'identifiant de « %s ».") % name)
            return {"ok": True, "kind": "internal", "name": name, "url": config._internal_link(fid)}
        if mode != "share":
            raise UserError(_("Type de lien inconnu."))
        if posixpath.normpath(abs_path) == posixpath.normpath(root):
            # The root of a record, or of the whole browser: never public.
            raise UserError(_("Le dossier racine ne se partage pas par lien public."))
        result = config._ocs_create_share(
            abs_path, **self._share_kwargs(config, preset_id, abs_path)
        )
        if not result.get("url"):
            raise UserError(_("Impossible de creer le lien de partage."))
        return {
            "ok": True,
            "kind": "share",
            "name": name,
            "url": result["url"],
            "password": result.get("password"),
            "expire_date": result.get("expire_date"),
        }

    @api.model
    def make_link(self, model, res_id, rel_path, mode, preset_id=None):
        config, abs_path, root = self._resolve_path(model, res_id, rel_path)
        return self._make_link(config, abs_path, root, mode, preset_id)

    @api.model
    def root_make_link(self, rel_path, mode, preset_id=None):
        config, abs_path, root = self._resolve_path_standalone(rel_path)
        return self._make_link(config, abs_path, root, mode, preset_id)

    # ------------------------------------------------------------------
    # Shares: state and revocation (18.0.4.1.0)
    # ------------------------------------------------------------------
    def _rel_under(self, path, root):
        """`path` relative to `root`, or None when it is outside."""
        try:
            _validate_path_under_prefix(path, root)
        except ValidationError:
            return None
        rel = posixpath.relpath(posixpath.normpath(path), posixpath.normpath(root))
        return "" if rel == "." else rel

    def _share_view(self, share, root, me=None, expiry_checked=True):
        rel = self._rel_under(share["path"], root) if share["path"] else None
        is_public = share["share_type"] == SHARE_TYPE_PUBLIC_LINK
        return {
            **share,
            # Nextcloud names a public link's recipient "(Shared link)": nobody.
            "share_with": "" if is_public else share["share_with"],
            "kind": SHARE_KINDS.get(share["share_type"], _("Autre")),
            "is_public": is_public,
            "writable": bool(share["permissions"] & (2 | 4 | 8)),
            "rel": rel,
            # Nextcloud's answer, narrowed to the person's own shares (made by
            # them, or of their files): Nextcloud also lets anyone with the
            # reshare right on a team folder delete a colleague's link, and
            # Odoo does not offer that. The server checks it again (_mine).
            "can_revoke": share["can_delete"] and (me is None or self._mine(share, me)),
            # The share list does not always carry the expiry; past the reread
            # cap the screen must not claim "no expiry" for a share that has one.
            "expiry_checked": expiry_checked,
        }

    @staticmethod
    def _mine(share, me):
        return bool(me) and me in (share.get("uid_owner"), share.get("uid_file_owner"))

    def _me(self, config):
        cred = config._nc_person()
        return cred.nc_user_id if cred else ""

    def _share_states(self, config, abs_dir, root):
        """Per child of a folder: how many public links, how many other shares."""
        states = {}
        for share in config._ocs_shares(path=abs_dir, subfiles=True):
            rel = self._rel_under(share["path"], root)
            if not rel:
                continue
            state = states.setdefault(rel, {"public": 0, "other": 0})
            state["public" if share["share_type"] == SHARE_TYPE_PUBLIC_LINK else "other"] += 1
        return {"ok": True, "states": states}

    @api.model
    def share_states(self, model, res_id, rel_path=""):
        config, abs_path, root = self._resolve_path(model, res_id, rel_path)
        return self._share_states(config, abs_path, root)

    @api.model
    def root_share_states(self, rel_path=""):
        config, abs_path, root = self._resolve_path_standalone(rel_path)
        return self._share_states(config, abs_path, root)

    def _entry_shares(self, config, abs_path, root):
        target = posixpath.normpath(abs_path)
        me = self._me(config)
        shares = []
        for share in config._ocs_shares(path=abs_path):
            if posixpath.normpath(share["path"] or "/") != target:
                continue
            # Read alone: the list is not trusted with expirations (a cap keeps
            # a much-shared file from costing one call per share without end).
            checked = len(shares) < SHARE_REREAD_MAX
            if checked:
                share = config._ocs_share(share["id"]) or share
            shares.append(self._share_view(share, root, me, checked))
        return {"ok": True, "shares": shares}

    @api.model
    def entry_shares(self, model, res_id, rel_path):
        config, abs_path, root = self._resolve_path(model, res_id, rel_path)
        return self._entry_shares(config, abs_path, root)

    @api.model
    def root_entry_shares(self, rel_path):
        config, abs_path, root = self._resolve_path_standalone(rel_path)
        return self._entry_shares(config, abs_path, root)

    def _revoke(self, config, root, share_id):
        share = config._ocs_share(self._as_id(share_id))
        if not share:
            # Deleted already, or not visible to this person: Nextcloud answers
            # the same 404 for both, and nothing is sent. The screen says so
            # instead of announcing a removal (seen on the bench: Bob "revoked"
            # Alice's share, which was still there).
            return {"ok": True, "gone": True}
        if self._rel_under(share["path"], root) is None:
            raise UserError(_("Ce partage est hors du dossier du navigateur."))
        if not self._mine(share, self._me(config)):
            raise UserError(_("Ce partage n'est pas le vôtre : retirez-le dans Nextcloud si vous en avez le droit."))
        config._ocs_delete_share(share["id"])
        return {"ok": True, "gone": False}

    @api.model
    def revoke_share(self, model, res_id, share_id):
        config, root = self._resolve_root(model, res_id)
        return self._revoke(config, root, share_id)

    @api.model
    def root_revoke_share(self, share_id):
        config, root = self._resolve_root_standalone()
        return self._revoke(config, root, share_id)

    # ------------------------------------------------------------------
    # Files linked from a chatter (18.0.4.1.0)
    # ------------------------------------------------------------------
    def _chatter_record(self, model, res_id):
        """The record whose chatter is read, checked as the caller."""
        if not isinstance(model, str) or model not in self.env:
            raise ValidationError(_("Modele non supporte: %s") % model)
        Model = self.env[model]
        if Model._abstract or Model._transient or not hasattr(Model, "message_ids"):
            raise ValidationError(_("Modele non supporte: %s") % model)
        record = Model.browse(self._as_id(res_id))
        if not record.exists():
            raise UserError(_("Enregistrement introuvable."))
        record.check_access("read")
        return record

    def _links_in_chatter(self, config, record):
        """Every Nextcloud link of the record's messages, newest mention first."""
        hosts = config._link_hosts()
        pattern = link_re(tuple(sorted(hosts)))
        budget = LINKED_SCAN_BUDGET
        messages = self.env["mail.message"].search(
            [("model", "=", record._name), ("res_id", "=", record.id)],
            order="date desc, id desc",
            limit=LINKED_MESSAGE_LIMIT,
        )
        links = {}
        for message in messages:
            if budget <= 0:
                break
            body = (message.body or "")[:LINKED_BODY_MAX]
            budget -= len(body)
            # One lowercase copy per body, not one per known host.
            low = body.lower()
            if not any(host in low for host in hosts):
                continue
            seen = set()
            for _host, prefix, kind, key in pattern.findall(body):
                kind = kind.lower()
                # `/apps/polls/s/<token>` is a poll, and `/remote.php/dav/files/x/s/y`
                # a folder named "s": neither is a link to a file or a share.
                if set(prefix.lower().split("/")) & NOT_A_LINK_PREFIX or (kind == "f" and not key.isdigit()):
                    continue
                ident = (kind, key)
                if ident in seen:
                    continue  # the same link as href and as text
                seen.add(ident)
                link = links.get(ident)
                if link is None:
                    link = links[ident] = {
                        "kind": "internal" if kind == "f" else "share",
                        "key": key,
                        "mentions": 0,
                        "last_date": fields.Datetime.to_string(message.date),
                        "last_author": message.author_id.display_name or "",
                        "message_id": message.id,
                    }
                link["mentions"] += 1
        return list(links.values())

    def _linked_files(self, config, root, record):
        """Resolved in the person's WHOLE account, not only under the browser root.

        On a real database, most files cited in chatters that Nextcloud still
        knows sit outside the browser root (Documents, Recordings, Notes).
        Bounded to the root, the list would call most of them "not found".
        Nothing is revealed that Nextcloud would not show this person for the
        same link: the calls speak as them. `in_browser` says whether the file
        can also be opened in the browser.
        """
        links = self._links_in_chatter(config, record)
        truncated = len(links) > LINKED_FILE_LIMIT
        links = links[:LINKED_FILE_LIMIT]
        base = config.nextcloud_base_url.rstrip("/")

        file_ids = [int(l["key"]) for l in links if l["kind"] == "internal"]
        by_id = {}
        if file_ids:
            where = "<d:or>%s</d:or>" % "".join(
                "<d:eq><d:prop><oc:fileid/></d:prop><d:literal>%d</d:literal></d:eq>" % fid
                for fid in file_ids
            ) if len(file_ids) > 1 else (
                "<d:eq><d:prop><oc:fileid/></d:prop><d:literal>%d</d:literal></d:eq>" % file_ids[0]
            )
            raw = config._webdav_search("/", where, limit=len(file_ids))
            by_id = {e["file_id"]: e for e in self._entries(config, raw, "/") if e["file_id"]}

        shares = {}
        me = self._me(config)
        rereads = 0
        if any(l["kind"] == "share" for l in links):
            shares = {s["token"]: s for s in config._ocs_shares() if s["token"]}

        out = []
        for link in links:
            if link["kind"] == "internal":
                entry = by_id.get(int(link["key"]))
                out.append({
                    **link,
                    "url": base + "/f/" + link["key"],
                    "found": bool(entry),
                    "in_browser": bool(entry) and self._rel_under("/" + entry["rel"], root) is not None,
                    "entry": entry or False,
                    "share": False,
                })
                continue
            share = shares.get(link["key"])
            if share:
                checked = rereads < SHARE_REREAD_MAX
                if checked:
                    rereads += 1
                    share = config._ocs_share(share["id"]) or share
                share = self._share_view(share, "/", me, checked)
            out.append({
                **link,
                "url": (share and share["url"]) or base + "/s/" + link["key"],
                "found": bool(share),
                "in_browser": bool(share) and self._rel_under(share["path"], root) is not None,
                "entry": False,
                "share": share or False,
            })
        return {"ok": True, "links": out, "truncated": truncated}

    @api.model
    def linked_files(self, model, res_id):
        """Nextcloud files already linked in a record's chatter, with their state.

        Nothing is stored: the links are read again from the messages each time,
        so a link removed from a message disappears, and a file deleted in
        Nextcloud shows as not found.
        """
        config, root = self._resolve_root_standalone()
        record = self._chatter_record(model, res_id)
        return self._linked_files(config, root, record)

    @api.model
    def linked_revoke_share(self, model, res_id, share_id):
        """Revoke a share cited in this record's chatter, wherever the file lives.

        Two conditions instead of the browser's root: the caller reads the
        record and its messages cite this share's token, and Nextcloud lets the
        caller delete it (the call speaks as them).
        """
        config, _root = self._resolve_root_standalone()
        record = self._chatter_record(model, res_id)
        share = config._ocs_share(self._as_id(share_id))
        if not share:
            return {"ok": True, "gone": True}
        cited = {l["key"] for l in self._links_in_chatter(config, record) if l["kind"] == "share"}
        if share["token"] not in cited:
            raise UserError(_("Ce partage n'est pas cité dans ce fil."))
        if not self._mine(share, self._me(config)):
            raise UserError(_("Ce partage n'est pas le vôtre : retirez-le dans Nextcloud si vous en avez le droit."))
        config._ocs_delete_share(share["id"])
        return {"ok": True, "gone": False}

    # ------------------------------------------------------------------
    # Standalone (root-scoped) operations — same semantics as the record
    # methods above, but path resolution comes from the config prefix instead
    # of a record. No Knowledge linking here (no project context).
    # ------------------------------------------------------------------
    @api.model
    def root_browse_dir(self, rel_path=""):
        config, abs_path, root = self._resolve_path_standalone(rel_path)
        return self._list_dir(config, abs_path, root)

    @api.model
    def root_make_folder(self, rel_path, name):
        name = _plain(_text(name, _("Nom de dossier")).strip().strip("/"))
        if not name or "/" in name:
            raise UserError(_("Nom de dossier invalide."))
        config, parent_abs, root = self._resolve_path_standalone(rel_path)
        target = _sanitize_nc_path(posixpath.join(parent_abs, name))
        _validate_path_under_prefix(target, root)
        config._webdav_mkcol(target)
        return {"ok": True}

    @api.model
    def root_rename_entry(self, rel_path, new_name):
        new_name = _plain(_text(new_name, _("Nom")).strip().strip("/"))
        if not new_name or "/" in new_name:
            raise UserError(_("Nom invalide."))
        config, abs_path, root = self._resolve_path_standalone(rel_path)
        if posixpath.normpath(abs_path) == posixpath.normpath(root):
            raise UserError(_("Impossible de renommer le dossier racine."))
        dst = _sanitize_nc_path(posixpath.join(posixpath.dirname(abs_path), new_name))
        _validate_path_under_prefix(dst, root)
        config._webdav_move(abs_path, dst)
        return {"ok": True}

    @api.model
    def root_move_entry(self, src_rel, dst_dir_rel):
        config, src_abs, root = self._resolve_path_standalone(src_rel)
        _, dst_dir_abs, _ = self._resolve_path_standalone(dst_dir_rel)
        dst = _sanitize_nc_path(posixpath.join(dst_dir_abs, posixpath.basename(src_abs)))
        _validate_path_under_prefix(dst, root)
        if posixpath.normpath(dst) == posixpath.normpath(src_abs):
            return {"ok": True}
        config._webdav_move(src_abs, dst)
        return {"ok": True}

    @api.model
    def root_delete_entry(self, rel_path):
        config, abs_path, root = self._resolve_path_standalone(rel_path)
        if posixpath.normpath(abs_path) == posixpath.normpath(root):
            raise UserError(_("Impossible de supprimer le dossier racine."))
        config._webdav_delete(abs_path)
        return {"ok": True}

    @api.model
    def root_upload_file(self, rel_path, filename, data_b64):
        filename = _plain(_text(filename, _("Nom de fichier")).strip().strip("/"))
        if not filename or "/" in filename:
            raise UserError(_("Nom de fichier invalide."))
        config, dir_abs, root = self._resolve_path_standalone(rel_path)
        target = _sanitize_nc_path(posixpath.join(dir_abs, filename))
        _validate_path_under_prefix(target, root)
        try:
            content = base64.b64decode(data_b64 or "")
        except (ValueError, TypeError):
            raise UserError(_("Contenu de fichier invalide."))
        if len(content) > MAX_UPLOAD_BYTES:
            raise UserError(
                _("Fichier trop volumineux (maximum %s Mo par televersement).")
                % (MAX_UPLOAD_BYTES // (1024 * 1024))
            )
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        config._webdav_put(target, content, content_type=ctype)
        return {"ok": True}

    @api.model
    def root_create_share(self, rel_path, preset_id=None, is_dir=False):
        # is_dir from the client is ignored; _share_kwargs derives it server-side.
        config, abs_path, root = self._resolve_path_standalone(rel_path)
        result = config._ocs_create_share(
            abs_path, **self._share_kwargs(config, preset_id, abs_path)
        )
        return {
            "ok": True,
            "url": result.get("url"),
            "password": result.get("password"),
            "expire_date": result.get("expire_date"),
        }
