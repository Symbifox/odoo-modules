"""RPC facade for the embedded Nextcloud file browser.

The OWL widget and the streaming controller talk to Nextcloud ONLY through this
model. Every entry point:
  * checks the caller is in group_nc_browser_user;
  * resolves the config + folder root from the *record* (project / task), never
    from a client-supplied path or config id (prevents IDOR);
  * forces every requested path to stay under that record root, which itself must
    stay under the config browser_root_prefix.

All actual WebDAV/OCS calls reuse the hardened helpers on
nextcloud.document.config (PROPFIND/GET/PUT/MKCOL/DELETE/MOVE/share).
"""

import base64
import logging
import mimetypes
import posixpath
import secrets
import string
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import unquote as url_unquote
from urllib.parse import urlparse

import pytz
from markupsafe import Markup

from odoo import _, api, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.bf_document_nextcloud_sync.models.nextcloud_document_config import (
    _sanitize_nc_path,
    _validate_path_under_prefix,
)

_logger = logging.getLogger(__name__)

GROUP = "bf_nextcloud_browser.group_nc_browser_user"
ALLOWED_MODELS = ("project.project", "project.task")
# Display tz is pinned to Montreal: the service-account user context has no tz,
# and Blue Fox always shows local Montreal time (never UTC).
MONTREAL_TZ = pytz.timezone("America/Montreal")
# Cap RPC uploads (base64 in a single call) to protect the worker's memory.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024  # 64 MiB


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
    def _check_access(self):
        if not self.env.user.has_group(GROUP):
            raise AccessError(_("Acces au navigateur Nextcloud non autorise."))

    def _default_config(self):
        Config = self.env["nextcloud.document.config"]
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
        record = self.env[model].browse(int(res_id))
        if not record.exists():
            raise UserError(_("Enregistrement introuvable."))
        record.check_access("read")
        return record

    def _resolve_root(self, model, res_id):
        """Return (config, root_path) for a record, or raise if not configured."""
        self._check_access()
        record = self._resolve_record(model, res_id)
        config = record.nc_documents_config_id or self._default_config()
        if not config:
            raise UserError(_("Aucune configuration Nextcloud disponible."))
        root = record.nc_documents_folder
        if not root:
            raise UserError(
                _("Aucun dossier Nextcloud n'est configure pour cet enregistrement.")
            )
        root = _sanitize_nc_path(root)
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
                "« Prefixe racine (navigateur) » (ex: /Blue Fox/) sur la "
                "configuration Nextcloud."
            ))
        _validate_path_under_prefix(root, _sanitize_nc_path(prefix))
        return config, root

    def _resolve_path(self, model, res_id, rel_path):
        """Resolve a client relative path to a sanitised absolute NC path."""
        config, root = self._resolve_root(model, res_id)
        rel = (rel_path or "").strip().lstrip("/")
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
                "« Prefixe racine (navigateur) » (ex: /Blue Fox/) sur la "
                "configuration Nextcloud avant d'utiliser cette application."
            ))
        return config, _sanitize_nc_path(prefix)

    def _resolve_path_standalone(self, rel_path):
        config, root = self._resolve_root_standalone()
        rel = (rel_path or "").strip().lstrip("/")
        abs_path = _sanitize_nc_path(posixpath.join(root, rel)) if rel else root
        _validate_path_under_prefix(abs_path, root)
        return config, abs_path, root

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _href_to_nc_path(self, config, href):
        """Convert a PROPFIND <href> back to a Nextcloud absolute path."""
        dav_root = urlparse(config.webdav_url).path.rstrip("/")
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
        config = (
            self.env["nextcloud.document.config"]
            .sudo()
            .search([("active", "=", True)], limit=1)
        )
        if not config:
            return {}
        return {
            "available": True,
            "width_pct": config.nc_panel_width_pct or 80,
            "height_pct": config.nc_panel_height_pct or 80,
        }

    # ------------------------------------------------------------------
    # Browse
    # ------------------------------------------------------------------
    @api.model
    def browse_dir(self, model, res_id, rel_path=""):
        config, abs_path, root = self._resolve_path(model, res_id, rel_path)
        return self._list_dir(config, abs_path, root)

    def _list_dir(self, config, abs_path, root):
        """Render one directory listing (shared by record + standalone scopes).
        Inputs are already-resolved, validated absolute paths."""
        raw = config._webdav_propfind(abs_path, depth="1")

        abs_norm = posixpath.normpath(abs_path)
        root_norm = posixpath.normpath(root)
        entries = []
        for e in raw:
            nc_path = self._href_to_nc_path(config, e.get("href", ""))
            if posixpath.normpath(nc_path) == abs_norm:
                continue  # the directory itself
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
                    iso = dt.astimezone(MONTREAL_TZ).strftime("%Y-%m-%d %H:%M")
                except (TypeError, ValueError):
                    iso = lastmod
            is_dir = bool(e.get("is_dir"))
            fid = e.get("file_id")
            internal_url = ""
            if fid and not is_dir and config.nextcloud_base_url:
                internal_url = config.nextcloud_base_url.rstrip("/") + "/f/" + str(fid)
            name = e.get("name") or posixpath.basename(nc_path)
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
                "rel": posixpath.relpath(nc_path, root),
                "file_id": fid or 0,
                "internal_url": internal_url,
            })
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
            "folder_color": config.nc_folder_color or "#2D3031",
            "presets": [
                {"id": p.id, "name": p.name, "access": p.access}
                for p in config.share_preset_ids
            ],
        }

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------
    @api.model
    def make_folder(self, model, res_id, rel_path, name):
        name = (name or "").strip().strip("/")
        if not name or "/" in name:
            raise UserError(_("Nom de dossier invalide."))
        config, parent_abs, root = self._resolve_path(model, res_id, rel_path)
        target = _sanitize_nc_path(posixpath.join(parent_abs, name))
        _validate_path_under_prefix(target, root)
        config._webdav_mkcol(target)
        return {"ok": True}

    @api.model
    def rename_entry(self, model, res_id, rel_path, new_name):
        new_name = (new_name or "").strip().strip("/")
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
        filename = (filename or "").strip().strip("/")
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
        preset = self.env["nextcloud.share.preset"].browse(int(preset_id))
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
    def link_to_knowledge_item(self, model, res_id, rel_path, item_id):
        config, abs_path, root = self._resolve_path(model, res_id, rel_path)
        item = self.env["project.knowledge.item"].browse(int(item_id))
        if not item.exists():
            raise UserError(_("Element de matrice introuvable."))
        item.check_access("write")

        url = config._ocs_create_share(abs_path).get("url")
        if not url:
            raise UserError(_("Impossible de creer le lien de partage."))
        name = posixpath.basename(abs_path) or "Fichier Nextcloud"
        att = self.env["ir.attachment"].create({
            "name": name,
            "type": "url",
            "url": url,
            "res_model": "project.knowledge.item",
            "res_id": item.id,
        })
        item.write({"attachment_ids": [(4, att.id)]})
        return {"ok": True, "label": item.display_name}

    @api.model
    def link_to_article(self, model, res_id, rel_path, article_id):
        Article = self.env.get("knowledge.article")
        if Article is None:
            raise UserError(
                _("L'application Odoo Knowledge n'est pas installee sur cette instance.")
            )
        config, abs_path, root = self._resolve_path(model, res_id, rel_path)
        article = Article.browse(int(article_id))
        if not article.exists():
            raise UserError(_("Article introuvable."))
        article.check_access("write")

        url = config._ocs_create_share(abs_path).get("url")
        if not url:
            raise UserError(_("Impossible de creer le lien de partage."))
        name = posixpath.basename(abs_path) or "Fichier Nextcloud"
        # Markup %-substitution HTML-escapes url + name (prevents stored XSS
        # from a crafted filename or share URL).
        snippet = Markup(
            '<p>&#128206; <a href="%s" target="_blank" rel="noopener">%s</a></p>'
        ) % (url, name)
        article.write({"body": Markup(article.body or "") + snippet})
        return {"ok": True, "label": article.display_name}

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
        name = (name or "").strip().strip("/")
        if not name or "/" in name:
            raise UserError(_("Nom de dossier invalide."))
        config, parent_abs, root = self._resolve_path_standalone(rel_path)
        target = _sanitize_nc_path(posixpath.join(parent_abs, name))
        _validate_path_under_prefix(target, root)
        config._webdav_mkcol(target)
        return {"ok": True}

    @api.model
    def root_rename_entry(self, rel_path, new_name):
        new_name = (new_name or "").strip().strip("/")
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
        filename = (filename or "").strip().strip("/")
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
