import hashlib
import hmac
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class LibresignWebhookController(http.Controller):
    """Contrôleur pour les notifications webhook LibreSign."""

    @http.route(
        "/privacy/libresign/webhook",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def libresign_webhook(self, **kw):
        """Traiter les notifications webhook LibreSign.

        LibreSign envoie un webhook pour :
        - file_signed : le fichier a été signé par tous les signataires

        ⚠ La route était en ``type="json"`` et lisait ``request.jsonrequest``,
        retiré d'Odoo depuis la 17.0 : l'attribut n'existait plus, l'exception
        était avalée et la route répondait invariablement « Invalid JSON ».
        Le corps reçu est du JSON simple, pas une enveloppe JSON-RPC, d'où le
        passage en ``type="http"`` avec ``request.get_json_data()``.
        """
        raw_body = request.httprequest.get_data()
        try:
            data = request.get_json_data()
        except Exception:
            _logger.error("JSON invalide dans le webhook LibreSign")
            return self._response(400, "error", "Invalid JSON")
        if not isinstance(data, dict):
            return self._response(400, "error", "Invalid JSON")

        Config = request.env["privacy.libresign.config"].sudo()
        config = Config.search([("active", "=", True)], limit=1)
        if not self._verify_webhook_signature(raw_body, config):
            return self._response(401, "error", "Invalid signature")

        event = data.get("event", "")
        _logger.info("Webhook LibreSign reçu : %s", event)
        file_data = data.get("file", {}) or {}
        file_uuid = str(file_data.get("uuid", ""))

        if not file_uuid:
            _logger.warning("UUID de fichier manquant dans le webhook LibreSign")
            return self._response(400, "error", "Missing file UUID")

        # Trouver le consentement associé
        Consent = request.env["privacy.consent"].sudo()
        consent = Consent.search([
            ("libresign_file_uuid", "=", file_uuid),
        ], limit=1)

        if not consent:
            _logger.info("Aucun consentement trouvé pour le fichier LibreSign %s",
                         file_uuid)
            return self._response(200, "ok", "File not tracked")

        if event == "file_signed":
            self._handle_file_signed(consent, file_data)

        return self._response(200, "ok")

    @staticmethod
    def _response(status, state, message=None):
        payload = {"status": state}
        if message:
            payload["message"] = message
        return request.make_json_response(payload, status=status)

    def _verify_webhook_signature(self, raw_body, config):
        """Vérifier la signature LibreSign — HMAC-SHA256 sur les octets bruts.

        ⚠ Deux défauts corrigés ici. L'implémentation précédente hachait une
        **re-sérialisation** du JSON analysé, qui ne peut pas correspondre aux
        octets émis dès que l'ordre des clés ou l'échappement diffère. Et elle
        **acceptait** les requêtes non signées quand aucun secret n'était
        configuré ; sans secret, on refuse désormais.

        ⚠ Réserve à lever avant de s'appuyer dessus : contrairement à DocuSeal,
        qui documente son en-tête ``X-Docuseal-Signature`` au format
        ``<horodatage>.<signature>``, le schéma retenu ici — HMAC-SHA256
        hexadécimal du corps brut dans ``X-LibreSign-Signature`` — vient du code
        d'origine et **n'a pas été confronté à un envoi réel de LibreSign**. À
        confirmer contre une instance vivante lors de la première intégration ;
        l'intégration native ``bf_sign`` reste le chemin recommandé.
        """
        secret = ""
        if config and config.webhook_secret_encrypted:
            secret = config._decrypt_value(config.webhook_secret_encrypted) or ""
        if not secret:
            _logger.warning(
                "Webhook LibreSign refusé : aucun secret de webhook n'est "
                "configuré sur la configuration LibreSign active.")
            return False

        signature = request.httprequest.headers.get("X-LibreSign-Signature", "")
        if not signature:
            _logger.warning("Webhook LibreSign refusé : en-tête de signature absent.")
            return False

        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            _logger.warning("Webhook LibreSign refusé : signature invalide.")
            return False
        return True

    def _handle_file_signed(self, consent, file_data):
        """Traiter l'événement file_signed."""
        _logger.info("Traitement de file_signed pour le consentement %s", consent.id)

        # Télécharger le document signé
        documents = []
        Config = request.env["privacy.libresign.config"].sudo()
        config = Config.search([("active", "=", True)], limit=1)

        if config:
            Interface = request.env["privacy.libresign.interface"].sudo()
            try:
                doc = Interface.get_file_content(config, consent.libresign_file_uuid)
                if doc:
                    documents.append(doc)
            except Exception as e:
                _logger.error("Échec du téléchargement du document signé : %s", e)

        consent._process_libresign_completion(file_data, documents)
