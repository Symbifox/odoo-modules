import base64
import json
import logging

import requests

from odoo import api, models
from odoo.exceptions import UserError

from .privacy_url_guard import assert_safe_url

_logger = logging.getLogger(__name__)

LIBRESIGN_API_PATH = "/index.php/apps/libresign/api/v1"


class PrivacyLibresignInterface(models.AbstractModel):
    """Modèle abstrait fournissant les méthodes client de l'API LibreSign (Nextcloud)."""

    _name = "privacy.libresign.interface"
    _description = "Interface API LibreSign"

    @api.model
    def _get_headers(self, config):
        """Get API headers with Basic Auth."""
        password = config._decrypt_value(config.password_encrypted)
        if not password or not config.username:
            raise UserError("Les identifiants LibreSign ne sont pas configurés.")
        credentials = base64.b64encode(
            f"{config.username}:{password}".encode()
        ).decode()
        return {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
            "OCS-APIRequest": "true",
        }

    @api.model
    def _get_base_url(self, config):
        """Get the LibreSign API base URL."""
        url = config.nextcloud_url.rstrip("/")
        return f"{url}{LIBRESIGN_API_PATH}"

    @api.model
    def _make_request(self, config, method, endpoint, data=None, timeout=30):
        """Make an API request to LibreSign."""
        url = f"{self._get_base_url(config)}{endpoint}"
        assert_safe_url(url, "LibreSign")
        headers = self._get_headers(config)

        try:
            if method == "GET":
                response = requests.get(url, headers=headers, timeout=timeout)
            elif method == "POST":
                response = requests.post(url, headers=headers, json=data, timeout=timeout)
            elif method == "PATCH":
                response = requests.patch(url, headers=headers, json=data, timeout=timeout)
            elif method == "DELETE":
                response = requests.delete(url, headers=headers, timeout=timeout)
            else:
                raise UserError(f"Méthode HTTP non prise en charge : {method}")

            if response.status_code >= 400:
                error_msg = response.text or f"HTTP {response.status_code}"
                _logger.error("LibreSign API error: %s", error_msg)
                raise UserError(f"Erreur API LibreSign : {error_msg}")

            return response.json() if response.text else {}

        except requests.exceptions.Timeout:
            raise UserError("La requête à l'API LibreSign a expiré.")
        except requests.exceptions.ConnectionError:
            raise UserError("Impossible de se connecter à l'API LibreSign.")
        except json.JSONDecodeError:
            return {"raw_response": response.text}

    @api.model
    def test_connection(self, config):
        """Test the API connection by listing files."""
        try:
            result = self._make_request(config, "GET", "/file/list")
            return {"success": True, "data": result}
        except UserError as e:
            return {"success": False, "error": str(e)}
        except Exception:
            _logger.exception("LibreSign connection test failed")
            return {
                "success": False,
                "error": (
                    "La connexion à LibreSign a échoué. "
                    "Consultez les journaux du serveur pour les détails."
                ),
            }

    @api.model
    def request_signature(self, config, file_data, signers, name="Consent Document"):
        """Request a signature via LibreSign.

        Args:
            config: LibreSign configuration record
            file_data: Base64-encoded PDF content
            signers: List of dicts with 'email' and 'name' keys
            name: Document name

        Returns:
            Response from LibreSign API with file UUID
        """
        data = {
            "file": {
                "base64": file_data.decode() if isinstance(file_data, bytes) else file_data,
                "name": name,
            },
            "users": [
                {
                    "email": signer["email"],
                    "displayName": signer.get("name", ""),
                }
                for signer in signers
            ],
            "name": name,
        }
        return self._make_request(config, "POST", "/request-signature", data)

    @api.model
    def get_file(self, config, file_id):
        """Get file validation info."""
        return self._make_request(config, "GET", f"/file/validate/file_id/{file_id}")

    @api.model
    def get_file_content(self, config, uuid):
        """Download the signed PDF content.

        Args:
            config: LibreSign configuration record
            uuid: File UUID from LibreSign

        Returns:
            Dict with 'name' and 'content' (base64-encoded)
        """
        url = f"{self._get_base_url(config)}/file/download/{uuid}"
        headers = self._get_headers(config)
        try:
            response = requests.get(url, headers=headers, timeout=60)
            if response.status_code == 200:
                return {
                    "name": f"signed_{uuid}.pdf",
                    "content": base64.b64encode(response.content),
                }
        except Exception as e:
            _logger.error("Failed to download signed file: %s", e)
        return None

    @api.model
    def register_webhook(self, config, webhook_url):
        """Register a webhook endpoint in LibreSign.

        Args:
            config: LibreSign configuration record
            webhook_url: URL to receive webhook notifications
        """
        data = {
            "url": webhook_url,
            "events": ["file_signed"],
        }
        return self._make_request(config, "POST", "/webhook/register", data)
