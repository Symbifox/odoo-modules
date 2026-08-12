import ipaddress
import logging
import os
import posixpath
import secrets
import string
from datetime import datetime, timedelta
from urllib.parse import quote as url_quote
from urllib.parse import unquote as url_unquote
from urllib.parse import urlparse
from xml.etree import ElementTree

from defusedxml import ElementTree as SafeET

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    raise ImportError(
        "The 'cryptography' package is required for bf_document_nextcloud_sync. "
        "Install it with: pip install cryptography"
    )

# DAV XML namespaces
DAV_NS = "DAV:"
OC_NS = "http://owncloud.org/ns"
NC_NS = "http://nextcloud.org/ns"

# Default share expiration in days
DEFAULT_SHARE_EXPIRY_DAYS = 30

# Maximum PROPFIND results to process
MAX_PROPFIND_ENTRIES = 500


def _is_private_ip(hostname):
    """Check if hostname resolves to a private/link-local IP (SSRF protection)."""
    try:
        addr = ipaddress.ip_address(hostname)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except ValueError:
        # Not an IP literal, hostname will be resolved by requests
        # We can't fully prevent DNS rebinding here, but we block obvious cases
        return False


def _validate_nc_url(url):
    """Validate that a Nextcloud URL is safe (HTTPS, no private IPs)."""
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValidationError(
            _("L'URL Nextcloud doit utiliser HTTPS. URL fournie : %s") % url
        )
    if not parsed.hostname:
        raise ValidationError(_("L'URL Nextcloud est invalide : pas de nom d'hote."))
    if _is_private_ip(parsed.hostname):
        raise ValidationError(
            _(
                "L'URL Nextcloud ne peut pas pointer vers une adresse IP "
                "privee ou locale : %s"
            )
            % parsed.hostname
        )


def _sanitize_nc_path(path):
    """Sanitize a Nextcloud file path against traversal attacks."""
    if not path:
        return path
    # URL-decode first to catch %2e%2e (%2F, etc.) bypass attempts
    path = url_unquote(path)
    # Reject null bytes and control characters
    if "\x00" in path or any(ord(c) < 32 for c in path):
        raise ValidationError(
            _("Le chemin contient des caracteres invalides : %s") % repr(path)
        )
    # Normalize and reject traversal
    normalized = posixpath.normpath(path)
    if ".." in normalized.split("/"):
        raise ValidationError(
            _("Le chemin ne peut pas contenir '..' : %s") % path
        )
    # Ensure path starts with /
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized


def _validate_path_under_prefix(path, prefix):
    """Ensure path is under the allowed prefix directory.

    Uses an exact-or-separator boundary so that prefix '/Blue Fox' does NOT
    also match a sibling like '/Blue Fox2'.
    """
    if not prefix:
        return
    norm_path = posixpath.normpath(path)
    norm_prefix = posixpath.normpath(prefix)
    if norm_prefix == "/":
        return
    if norm_path != norm_prefix and not norm_path.startswith(norm_prefix + "/"):
        raise ValidationError(
            _(
                "Le chemin '%s' est en dehors du dossier autorise '%s'."
            )
            % (path, prefix)
        )


class NextcloudDocumentConfig(models.Model):
    """Configuration for Nextcloud document storage integration."""

    _name = "nextcloud.document.config"
    _description = "Configuration de synchronisation documents Nextcloud"
    _order = "sequence, name"

    name = fields.Char(
        string="Nom",
        required=True,
        help="Nom de cette configuration Nextcloud",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        string="Societe",
        required=True,
        default=lambda self: self.env.company,
    )

    # Nextcloud WebDAV configuration
    nextcloud_base_url = fields.Char(
        string="URL Nextcloud",
        required=True,
        help="URL de base de l'instance Nextcloud (ex: https://nextcloud.example.com)",
    )
    webdav_path = fields.Char(
        string="Chemin WebDAV",
        required=True,
        default="/remote.php/dav/files/",
        help="Chemin WebDAV de base (ex: /remote.php/dav/files/)",
    )
    nextcloud_user = fields.Char(
        string="Utilisateur Nextcloud",
        required=True,
        help="Nom d'utilisateur pour l'authentification WebDAV",
    )
    nextcloud_app_password = fields.Char(
        string="Mot de passe d'application",
        compute="_compute_app_password",
        inverse="_inverse_app_password",
        store=False,
        groups="base.group_system",
        help="Mot de passe d'application Nextcloud pour l'authentification WebDAV",
    )
    nextcloud_app_password_encrypted = fields.Char(
        string="Mot de passe (chiffre)",
        groups="base.group_system",
    )

    # TLS configuration
    allow_insecure = fields.Boolean(
        string="Autoriser connexion non securisee",
        default=False,
        groups="base.group_system",
        help="ATTENTION: desactive la verification TLS. "
        "A utiliser uniquement en developpement/test.",
    )
    ca_bundle_path = fields.Char(
        string="Chemin du certificat CA",
        groups="base.group_system",
        help="Chemin vers un bundle CA personnalise (optionnel)",
    )

    # Share defaults
    default_share_expiry_days = fields.Integer(
        string="Expiration des partages (jours)",
        default=DEFAULT_SHARE_EXPIRY_DAYS,
        help="Duree par defaut des liens de partage publics en jours",
    )
    share_password_enabled = fields.Boolean(
        string="Mot de passe sur les partages",
        default=True,
        help="Generer un mot de passe aleatoire pour les liens de partage publics",
    )

    # Statistics
    last_sync = fields.Datetime(
        string="Derniere synchronisation",
        readonly=True,
    )
    last_sync_status = fields.Selection(
        [
            ("success", "Succes"),
            ("error", "Erreur"),
            ("partial", "Partiel"),
        ],
        string="Statut derniere sync",
        readonly=True,
    )
    last_sync_message = fields.Text(
        string="Message derniere sync",
        readonly=True,
    )

    # === Constraints ===

    @api.constrains("nextcloud_base_url")
    def _check_nextcloud_url(self):
        for record in self:
            _validate_nc_url(record.nextcloud_base_url)

    @api.constrains("default_share_expiry_days")
    def _check_share_expiry(self):
        for record in self:
            if record.default_share_expiry_days < 1:
                raise ValidationError(
                    _("L'expiration des partages doit etre d'au moins 1 jour.")
                )

    # === Encryption Methods ===

    def _get_encryption_key(self):
        """Get encryption key from environment or odoo.conf, NOT from database.

        Security: storing the key in ir.config_parameter alongside the
        encrypted data provides no real protection if the DB is compromised.
        The key should be in the environment or odoo.conf.
        """
        # Priority 1: environment variable
        key = os.environ.get("NC_DOC_SYNC_FERNET_KEY")
        if key:
            return key.encode()

        # Priority 2: odoo.conf
        try:
            from odoo.tools import config
            key = config.get("nc_doc_sync_fernet_key")
            if key:
                return key.encode()
        except Exception:
            pass

        _logger.error(
            "No Fernet key configured for bf_document_nextcloud_sync. "
            "Set NC_DOC_SYNC_FERNET_KEY env var or "
            "nc_doc_sync_fernet_key in odoo.conf."
        )
        return None

    def _encrypt_value(self, value):
        """Encrypt a string value using Fernet symmetric encryption."""
        if not value:
            return False
        key = self._get_encryption_key()
        if not key:
            raise UserError(
                _("Cle de chiffrement manquante. Configurez "
                  "NC_DOC_SYNC_FERNET_KEY dans l'environnement "
                  "ou nc_doc_sync_fernet_key dans odoo.conf.")
            )
        try:
            f = Fernet(key)
            return f.encrypt(value.encode()).decode()
        except Exception as e:
            _logger.error("Encryption failed: %s", type(e).__name__)
            raise UserError(
                _("Le chiffrement a echoue. Verifiez la cle de chiffrement.")
            ) from e

    def _decrypt_value(self, encrypted_value):
        """Decrypt a Fernet-encrypted value."""
        if not encrypted_value:
            return False
        key = self._get_encryption_key()
        if not key:
            _logger.error("Cannot decrypt: no Fernet key configured")
            return False
        try:
            f = Fernet(key)
            return f.decrypt(encrypted_value.encode()).decode()
        except InvalidToken:
            _logger.error("Decryption failed: key mismatch or corrupted data")
            return False
        except Exception as e:
            _logger.error("Decryption failed: %s", type(e).__name__)
            return False

    # === Computed Fields ===

    @api.depends("nextcloud_app_password_encrypted")
    def _compute_app_password(self):
        for record in self:
            record.nextcloud_app_password = record._decrypt_value(
                record.nextcloud_app_password_encrypted
            )

    def _inverse_app_password(self):
        for record in self:
            if record.nextcloud_app_password:
                record.nextcloud_app_password_encrypted = record._encrypt_value(
                    record.nextcloud_app_password
                )
            else:
                record.nextcloud_app_password_encrypted = False

    # === URL Helpers ===

    @property
    def webdav_url(self):
        """Return full WebDAV URL for this configuration."""
        base = self.nextcloud_base_url.rstrip("/")
        path = self.webdav_path
        if not path.startswith("/"):
            path = "/" + path
        # Append username if path ends with files/
        if path.rstrip("/").endswith("/files"):
            path = path.rstrip("/") + "/" + self.nextcloud_user + "/"
        return base + path

    @property
    def _tls_verify(self):
        """Return TLS verification setting for requests."""
        if self.allow_insecure:
            _logger.warning(
                "TLS verification disabled for config %s — insecure!", self.name
            )
            return False
        if self.ca_bundle_path:
            return self.ca_bundle_path
        return True

    def _get_auth(self):
        """Return (user, password) tuple for HTTP auth."""
        password = self._decrypt_value(self.nextcloud_app_password_encrypted)
        if not password:
            raise UserError(
                _("Aucun mot de passe configure pour '%s'.") % self.name
            )
        return (self.nextcloud_user, password)

    # === WebDAV Methods ===

    def _webdav_propfind(self, path, depth="1", props=None):
        """Execute a PROPFIND request on the given WebDAV path.

        Returns a list of dicts with file metadata.
        """
        self.ensure_one()
        path = _sanitize_nc_path(path)

        if props is None:
            props = [
                "d:getlastmodified",
                "d:getcontentlength",
                "d:getcontenttype",
                "d:resourcetype",
                "d:getetag",
                "oc:fileid",
                "oc:size",
            ]

        prop_xml = "".join(f"<{p}/>" for p in props)
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" '
            'xmlns:nc="http://nextcloud.org/ns">'
            f"<d:prop>{prop_xml}</d:prop>"
            "</d:propfind>"
        )

        url = self.webdav_url.rstrip("/") + url_quote(path)

        try:
            resp = requests.request(
                "PROPFIND",
                url,
                data=body.encode("utf-8"),
                headers={
                    "Content-Type": "application/xml; charset=utf-8",
                    "Depth": str(depth),
                },
                auth=self._get_auth(),
                timeout=30,
                verify=self._tls_verify,
            )
        except requests.ConnectionError:
            raise UserError(
                _("Impossible de joindre %s. Verifiez l'URL et le reseau.")
                % self.nextcloud_base_url
            )
        except requests.Timeout:
            raise UserError(
                _("Connexion a %s expirée (30s).") % self.nextcloud_base_url
            )

        if resp.status_code == 401:
            raise UserError(
                _("Authentification refusee. Verifiez l'utilisateur et le mot de passe.")
            )
        if resp.status_code not in (207, 200):
            _logger.error(
                "PROPFIND %s returned %s", path, resp.status_code
            )
            raise UserError(
                _("Erreur WebDAV PROPFIND: HTTP %s") % resp.status_code
            )

        return self._parse_propfind_response(resp.text, path)

    def _parse_propfind_response(self, xml_text, base_path):
        """Parse a PROPFIND multistatus response into a list of file dicts."""
        results = []
        try:
            root = SafeET.fromstring(xml_text)
        except (ElementTree.ParseError, SafeET.DTDForbidden,
                SafeET.EntitiesForbidden, SafeET.ExternalReferenceForbidden) as e:
            _logger.error("Failed to parse PROPFIND XML: %s", e)
            return results

        for response in root.findall(f"{{{DAV_NS}}}response"):
            href_el = response.find(f"{{{DAV_NS}}}href")
            if href_el is None or href_el.text is None:
                continue

            href = href_el.text
            propstat = response.find(f"{{{DAV_NS}}}propstat")
            if propstat is None:
                continue
            prop = propstat.find(f"{{{DAV_NS}}}prop")
            if prop is None:
                continue

            # Determine if collection (directory)
            resourcetype = prop.find(f"{{{DAV_NS}}}resourcetype")
            is_dir = False
            if resourcetype is not None:
                is_dir = resourcetype.find(f"{{{DAV_NS}}}collection") is not None

            # Extract properties
            entry = {
                "href": href,
                "is_dir": is_dir,
                "name": url_unquote(posixpath.basename(href.rstrip("/"))),
            }

            lastmod = prop.find(f"{{{DAV_NS}}}getlastmodified")
            if lastmod is not None and lastmod.text:
                entry["last_modified"] = lastmod.text

            size_el = prop.find(f"{{{OC_NS}}}size")
            if size_el is None:
                size_el = prop.find(f"{{{DAV_NS}}}getcontentlength")
            if size_el is not None and size_el.text:
                try:
                    entry["size"] = int(size_el.text)
                except ValueError:
                    entry["size"] = 0
            else:
                entry["size"] = 0

            content_type = prop.find(f"{{{DAV_NS}}}getcontenttype")
            if content_type is not None and content_type.text:
                entry["content_type"] = content_type.text

            etag = prop.find(f"{{{DAV_NS}}}getetag")
            if etag is not None and etag.text:
                entry["etag"] = etag.text.strip('"')

            fileid = prop.find(f"{{{OC_NS}}}fileid")
            if fileid is not None and fileid.text:
                try:
                    entry["file_id"] = int(fileid.text)
                except ValueError:
                    pass

            results.append(entry)

            if len(results) >= MAX_PROPFIND_ENTRIES:
                _logger.warning(
                    "PROPFIND results truncated at %d entries for %s",
                    MAX_PROPFIND_ENTRIES,
                    base_path,
                )
                break

        return results

    def _webdav_get(self, path):
        """Download a file from Nextcloud via WebDAV GET.

        Returns the response content (bytes).
        """
        self.ensure_one()
        path = _sanitize_nc_path(path)
        url = self.webdav_url.rstrip("/") + url_quote(path)

        try:
            resp = requests.get(
                url,
                auth=self._get_auth(),
                timeout=60,
                verify=self._tls_verify,
            )
        except requests.RequestException as e:
            raise UserError(
                _("Erreur lors du telechargement: %s") % str(e)
            )

        if resp.status_code == 404:
            raise UserError(
                _("Fichier introuvable sur Nextcloud: %s") % path
            )
        if resp.status_code != 200:
            raise UserError(
                _("Erreur WebDAV GET: HTTP %s") % resp.status_code
            )
        return resp.content

    def _webdav_put(self, path, content, content_type="application/octet-stream"):
        """Upload a file to Nextcloud via WebDAV PUT.

        Args:
            path: Destination path on Nextcloud
            content: File content (bytes)
            content_type: MIME type

        Returns the HTTP response.
        """
        self.ensure_one()
        path = _sanitize_nc_path(path)
        url = self.webdav_url.rstrip("/") + url_quote(path)

        try:
            resp = requests.put(
                url,
                data=content,
                headers={"Content-Type": content_type},
                auth=self._get_auth(),
                timeout=120,
                verify=self._tls_verify,
            )
        except requests.RequestException as e:
            raise UserError(
                _("Erreur lors du televersement: %s") % str(e)
            )

        if resp.status_code not in (200, 201, 204):
            raise UserError(
                _("Erreur WebDAV PUT: HTTP %s") % resp.status_code
            )
        return resp

    def _webdav_mkcol(self, path):
        """Create a directory on Nextcloud via WebDAV MKCOL."""
        self.ensure_one()
        path = _sanitize_nc_path(path)
        url = self.webdav_url.rstrip("/") + url_quote(path) + "/"

        try:
            resp = requests.request(
                "MKCOL",
                url,
                auth=self._get_auth(),
                timeout=30,
                verify=self._tls_verify,
            )
        except requests.RequestException as e:
            raise UserError(
                _("Erreur lors de la creation du dossier: %s") % str(e)
            )

        # 405 = already exists, that's fine
        if resp.status_code not in (200, 201, 204, 405):
            raise UserError(
                _("Erreur WebDAV MKCOL: HTTP %s") % resp.status_code
            )
        return resp

    # === OCS Share API ===

    def _ocs_create_share(self, path, share_type=3, permissions=1,
                          expire_date=None, password=None):
        """Create a public share link via OCS API.

        Args:
            path: File path on Nextcloud
            share_type: 3 = public link (default)
            permissions: 1 = read-only (default)
            expire_date: Expiration date string (YYYY-MM-DD)
            password: Optional password for the share

        Returns dict with share info (url, token, id).
        """
        self.ensure_one()
        path = _sanitize_nc_path(path)

        # Apply defaults from config
        if expire_date is None:
            expire_dt = datetime.now() + timedelta(
                days=self.default_share_expiry_days
            )
            expire_date = expire_dt.strftime("%Y-%m-%d")

        if password is None and self.share_password_enabled:
            # Generate a random password
            alphabet = string.ascii_letters + string.digits
            password = "".join(secrets.choice(alphabet) for _ in range(16))

        url = (
            self.nextcloud_base_url.rstrip("/")
            + "/ocs/v2.php/apps/files_sharing/api/v1/shares"
        )

        data = {
            "path": path,
            "shareType": share_type,
            "permissions": permissions,
        }
        if expire_date:
            data["expireDate"] = expire_date
        if password:
            data["password"] = password

        try:
            resp = requests.post(
                url,
                data=data,
                headers={"OCS-APIRequest": "true"},
                auth=self._get_auth(),
                timeout=30,
                verify=self._tls_verify,
            )
        except requests.RequestException as e:
            raise UserError(
                _("Erreur OCS lors de la creation du partage: %s") % str(e)
            )

        if resp.status_code not in (200, 201):
            _logger.error("OCS share creation failed: %s %s", resp.status_code, resp.text[:200])
            raise UserError(
                _("Erreur OCS creation de partage: HTTP %s") % resp.status_code
            )

        # Parse OCS XML response
        try:
            root = SafeET.fromstring(resp.text)
            data_el = root.find(".//data")
            result = {}
            if data_el is not None:
                url_el = data_el.find("url")
                if url_el is not None and url_el.text:
                    result["url"] = url_el.text
                token_el = data_el.find("token")
                if token_el is not None and token_el.text:
                    result["token"] = token_el.text
                id_el = data_el.find("id")
                if id_el is not None and id_el.text:
                    result["share_id"] = int(id_el.text)
            if password:
                result["password"] = password
            if expire_date:
                result["expire_date"] = expire_date
            return result
        except (ElementTree.ParseError, SafeET.DTDForbidden,
                SafeET.EntitiesForbidden, SafeET.ExternalReferenceForbidden) as e:
            _logger.error("Failed to parse OCS response: %s", e)
            raise UserError(
                _("Impossible de lire la reponse OCS pour le partage.")
            )

    # === Actions ===

    def action_test_connection(self):
        """Test connection to Nextcloud WebDAV endpoint."""
        self.ensure_one()

        try:
            results = self._webdav_propfind("/", depth="0")
            msg = _("Connexion reussie! Serveur Nextcloud accessible.")
            self.write({
                "last_sync": fields.Datetime.now(),
                "last_sync_status": "success",
                "last_sync_message": msg,
            })
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Test de connexion"),
                    "message": msg,
                    "type": "success",
                    "sticky": False,
                },
            }
        except UserError as e:
            msg = str(e)
            self.write({
                "last_sync": fields.Datetime.now(),
                "last_sync_status": "error",
                "last_sync_message": msg,
            })
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Test de connexion"),
                    "message": msg,
                    "type": "danger",
                    "sticky": True,
                },
            }
