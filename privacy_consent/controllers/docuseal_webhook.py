import hashlib
import hmac
import logging
import time

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

# Tolérance sur l'horodatage porté par la signature, en secondes. Au-delà, la
# requête est refusée : sans cette borne, une requête signée interceptée reste
# rejouable indéfiniment.
SIGNATURE_TOLERANCE = 300


class DocuSealWebhookController(http.Controller):
    """Contrôleur pour les notifications webhook DocuSeal."""

    @http.route(
        "/privacy/docuseal/webhook",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def docuseal_webhook(self, **kw):
        """Traiter les notifications webhook DocuSeal.

        DocuSeal envoie des webhooks pour :
        - submission.created
        - submission.started
        - submission.completed
        - submission.expired
        - form.started
        - form.viewed
        - form.completed

        ⚠ La route était en ``type="json"`` et lisait ``request.jsonrequest``,
        retiré d'Odoo depuis la 17.0 : l'attribut n'existait plus, l'exception
        était avalée et la route répondait invariablement « Invalid JSON ».
        DocuSeal poste un corps JSON simple, pas une enveloppe JSON-RPC, donc
        la route est en ``type="http"`` et le corps est lu par
        ``request.get_json_data()``.
        """
        # Les octets bruts, avant toute analyse : c'est sur eux que porte la
        # signature (voir _verify_webhook_signature).
        raw_body = request.httprequest.get_data()
        try:
            data = request.get_json_data()
        except Exception:
            _logger.error("JSON invalide dans le webhook DocuSeal")
            return self._response(400, "error", "Invalid JSON")
        if not isinstance(data, dict):
            return self._response(400, "error", "Invalid JSON")

        Config = request.env["privacy.docuseal.config"].sudo()
        config = Config.search([("active", "=", True)], limit=1)
        if not self._verify_webhook_signature(raw_body, config):
            return self._response(401, "error", "Invalid signature")

        event_type = data.get("event_type", "")
        _logger.info("Webhook DocuSeal reçu : %s", event_type)
        submission_data = data.get("data", {}) or {}
        submission_id = str(submission_data.get("id")
                            or submission_data.get("submission_id", ""))

        if not submission_id:
            _logger.warning("ID de soumission manquant dans le webhook DocuSeal")
            return self._response(400, "error", "Missing submission ID")

        # Trouver le consentement associé
        Consent = request.env["privacy.consent"].sudo()
        consent = Consent.search([
            ("docuseal_submission_id", "=", submission_id),
        ], limit=1)

        if not consent:
            _logger.info("Aucun consentement trouvé pour la soumission DocuSeal %s",
                         submission_id)
            return self._response(200, "ok", "Submission not tracked")

        # Traiter selon le type d'événement
        if event_type == "submission.completed":
            self._handle_submission_completed(consent, submission_data)
        elif event_type == "submission.expired":
            self._handle_submission_expired(consent, submission_data)
        elif event_type == "form.completed":
            self._handle_form_completed(consent, submission_data)

        return self._response(200, "ok")

    @staticmethod
    def _response(status, state, message=None):
        payload = {"status": state}
        if message:
            payload["message"] = message
        return request.make_json_response(payload, status=status)

    def _verify_webhook_signature(self, raw_body, config):
        """Vérifier la signature DocuSeal — HMAC-SHA256 sur les octets bruts.

        Format documenté par DocuSeal : l'en-tête ``X-Docuseal-Signature`` vaut
        ``<horodatage>.<signature>`` et le contenu signé est
        ``<horodatage>.<corps brut>``, la clé étant le secret ``whsec_…`` de
        l'onglet HMAC de la page webhook.

        ⚠ Deux défauts corrigés ici. L'implémentation précédente hachait une
        **re-sérialisation** du JSON analysé (`json.dumps` du dict), ce qui ne
        peut pas correspondre aux octets émis dès que l'ordre des clés ou
        l'échappement diffère — la vérification n'aurait donc jamais pu réussir
        contre un vrai envoi. Et elle **acceptait** les requêtes non signées
        quand aucun secret n'était configuré : quiconque connaissait un
        identifiant de soumission pouvait faire passer un consentement pour
        complété. Sans secret, on refuse désormais.
        """
        secret = ""
        if config and config.webhook_secret_encrypted:
            secret = config._decrypt_value(config.webhook_secret_encrypted) or ""
        if not secret:
            _logger.warning(
                "Webhook DocuSeal refusé : aucun secret de webhook n'est "
                "configuré. Renseignez-le sur la configuration DocuSeal active "
                "(onglet HMAC de la page webhook DocuSeal, valeur whsec_…).")
            return False

        header = request.httprequest.headers.get("X-Docuseal-Signature", "")
        timestamp, _sep, signature = header.partition(".")
        if not timestamp or not signature:
            _logger.warning("Webhook DocuSeal refusé : en-tête de signature "
                            "absent ou mal formé.")
            return False
        try:
            age = abs(time.time() - int(timestamp))
        except (TypeError, ValueError):
            _logger.warning("Webhook DocuSeal refusé : horodatage de signature "
                            "illisible.")
            return False
        if age > SIGNATURE_TOLERANCE:
            _logger.warning("Webhook DocuSeal refusé : signature datée de %ss.",
                            int(age))
            return False

        expected = hmac.new(
            secret.encode(),
            timestamp.encode() + b"." + raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            _logger.warning("Webhook DocuSeal refusé : signature invalide.")
            return False
        return True

    def _handle_submission_completed(self, consent, submission_data):
        """Traiter l'événement submission.completed."""
        _logger.info("Traitement de submission.completed pour le consentement %s",
                     consent.id)

        # Obtenir les documents signés
        documents = []
        Config = request.env["privacy.docuseal.config"].sudo()
        config = Config.search([("active", "=", True)], limit=1)

        if config:
            Interface = request.env["privacy.docuseal.interface"].sudo()
            try:
                documents = Interface.get_submission_documents(
                    config, consent.docuseal_submission_id
                )
            except Exception as e:
                _logger.error("Échec du téléchargement des documents signés : %s", e)

        # Traiter la complétion
        consent._process_docuseal_completion(submission_data, documents)

    def _handle_submission_expired(self, consent, submission_data):
        """Traiter l'événement submission.expired."""
        _logger.info("Traitement de submission.expired pour le consentement %s",
                     consent.id)

        consent.write({
            "docuseal_status": "expired",
        })
        consent.message_post(
            body="La demande de signature DocuSeal a expiré.",
            message_type="notification",
        )

    def _handle_form_completed(self, consent, submission_data):
        """Traiter l'événement form.completed (complétion partielle)."""
        _logger.info("Traitement de form.completed pour le consentement %s",
                     consent.id)

        consent.message_post(
            body="Le signataire a complété le formulaire. En attente de la "
                 "finalisation de la soumission.",
            message_type="notification",
        )
