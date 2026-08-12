import base64
import json
import logging

import requests

from odoo import api, models
from odoo.exceptions import UserError

from .privacy_url_guard import assert_safe_url

_logger = logging.getLogger(__name__)


class PrivacyDocusealInterface(models.AbstractModel):
    """Modèle abstrait fournissant les méthodes client de l'API DocuSeal."""

    _name = "privacy.docuseal.interface"
    _description = "Interface API DocuSeal"

    @api.model
    def _get_headers(self, config):
        """Get API headers with authentication."""
        api_key = config._decrypt_value(config.api_key_encrypted)
        if not api_key:
            raise UserError("La clé API DocuSeal n'est pas configurée.")
        return {
            "X-Auth-Token": api_key,
            "Content-Type": "application/json",
        }

    @api.model
    def _make_request(self, config, method, endpoint, data=None, timeout=30):
        """Make an API request to DocuSeal."""
        url = f"{config.api_url}{endpoint}"
        assert_safe_url(url, "DocuSeal")
        headers = self._get_headers(config)

        try:
            if method == "GET":
                response = requests.get(url, headers=headers, timeout=timeout)
            elif method == "POST":
                response = requests.post(url, headers=headers, json=data, timeout=timeout)
            elif method == "PUT":
                response = requests.put(url, headers=headers, json=data, timeout=timeout)
            elif method == "DELETE":
                response = requests.delete(url, headers=headers, timeout=timeout)
            else:
                raise UserError(f"Méthode HTTP non prise en charge : {method}")

            if response.status_code >= 400:
                error_msg = response.text or f"HTTP {response.status_code}"
                _logger.error("DocuSeal API error: %s", error_msg)
                raise UserError(f"Erreur API DocuSeal : {error_msg}")

            return response.json() if response.text else {}

        except requests.exceptions.Timeout:
            raise UserError("La requête à l'API DocuSeal a expiré.")
        except requests.exceptions.ConnectionError:
            raise UserError("Impossible de se connecter à l'API DocuSeal.")
        except json.JSONDecodeError:
            return {"raw_response": response.text}

    @api.model
    def test_connection(self, config):
        """Test the API connection."""
        try:
            result = self._make_request(config, "GET", "/templates")
            return {"success": True, "templates": result}
        except UserError as e:
            return {"success": False, "error": str(e)}
        except Exception:
            _logger.exception("DocuSeal connection test failed")
            return {
                "success": False,
                "error": (
                    "La connexion à DocuSeal a échoué. "
                    "Consultez les journaux du serveur pour les détails."
                ),
            }

    @api.model
    def get_templates(self, config):
        """Get all templates from DocuSeal."""
        return self._make_request(config, "GET", "/templates")

    @api.model
    def get_template(self, config, template_id):
        """Get a specific template."""
        return self._make_request(config, "GET", f"/templates/{template_id}")

    @api.model
    def create_submission(self, config, template_id, submitters, send_email=True, message=None):
        """Create a new submission for signature.

        Args:
            config: DocuSeal configuration record
            template_id: DocuSeal template ID
            submitters: List of submitter dicts with email, name, and field values
            send_email: Whether to send email notification
            message: Optional custom message for signers

        Returns:
            Submission response from DocuSeal
        """
        data = {
            "template_id": template_id,
            "send_email": send_email,
            "submitters": submitters,
        }
        if message:
            data["message"] = {"body": message}

        return self._make_request(config, "POST", "/submissions", data)

    @api.model
    def get_submission(self, config, submission_id):
        """Get submission status and details."""
        return self._make_request(config, "GET", f"/submissions/{submission_id}")

    @api.model
    def get_submission_documents(self, config, submission_id):
        """Get signed documents for a submission."""
        submission = self.get_submission(config, submission_id)
        documents = []

        if submission.get("documents"):
            for doc in submission["documents"]:
                if doc.get("url"):
                    try:
                        response = requests.get(doc["url"], timeout=30)
                        if response.status_code == 200:
                            documents.append({
                                "name": doc.get("name", "signed_document.pdf"),
                                "content": base64.b64encode(response.content),
                            })
                    except Exception as e:
                        _logger.error("Failed to download document: %s", e)

        return documents

    @api.model
    def archive_submission(self, config, submission_id):
        """Archive a submission."""
        return self._make_request(config, "DELETE", f"/submissions/{submission_id}")
