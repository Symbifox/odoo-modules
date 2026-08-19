"""Envoi FCM (Firebase Cloud Messaging, HTTP v1) — push de l'app Android native.

Le jeton OAuth du compte de service est frappé localement avec **PyJWT** (déjà
présent) → pas de dépendance ``google-auth`` à ajouter. Le JSON du compte de
service Firebase est stocké dans le paramètre système
``bf_sms_archive.fcm_service_account_json`` (posé lors de la configuration Firebase).

Messages **data-only, priorité HIGH** : l'app construit elle-même la notification
(avec l'action de réponse rapide), et le message réveille l'appareil même en
arrière-plan / Doze.
"""
import json
import logging
import time

import requests

from odoo import api, models

_logger = logging.getLogger(__name__)

FCM_SA_PARAM = "bf_sms_archive.fcm_service_account_json"
_OAUTH_URL = "https://oauth2.googleapis.com/token"
_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_TOKEN_CACHE = {}  # {client_email: (access_token, exp_epoch)} — par worker


class SmsFcm(models.AbstractModel):
    _name = "sms.archive.fcm"
    _description = "Envoi FCM (push app native)"

    @api.model
    def _sa_info(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(FCM_SA_PARAM)
        if not raw:
            return None
        try:
            info = json.loads(raw)
            if info.get("private_key") and info.get("client_email") and info.get("project_id"):
                return info
        except ValueError:
            pass
        _logger.warning("FCM : JSON de compte de service illisible/incomplet.")
        return None

    @api.model
    def _access_token(self, sa):
        import jwt  # PyJWT
        email = sa["client_email"]
        now = time.time()
        cached = _TOKEN_CACHE.get(email)
        if cached and cached[1] - 60 > now:
            return cached[0]
        iat = int(now)
        exp = iat + 3600
        assertion = jwt.encode(
            {"iss": email, "scope": _SCOPE, "aud": _OAUTH_URL, "iat": iat, "exp": exp},
            sa["private_key"], algorithm="RS256",
        )
        resp = requests.post(_OAUTH_URL, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        }, timeout=10)
        resp.raise_for_status()
        token = resp.json()["access_token"]
        _TOKEN_CACHE[email] = (token, exp)
        return token

    @api.model
    def _notify_new_message(self, msg):
        """Envoie un push FCM aux appareils des ayants droit (propriétaire du fil
        et utilisateurs de la ligne partagée) pour un entrant.
        Défensif : ne lève jamais. No-op si Firebase pas configuré."""
        sa = self._sa_info()
        if not sa:
            return
        recipients = msg._notify_users()
        if not recipients:
            return
        Device = self.env["sms.archive.mobile.device"].sudo()
        devices = Device.search([
            ("user_id", "in", recipients.ids), ("active", "=", True),
            ("fcm_token", "!=", False),
        ])
        if not devices:
            return
        try:
            token = self._access_token(sa)
        except Exception:
            _logger.warning("FCM : échec d'obtention du jeton OAuth.", exc_info=True)
            return
        url = "https://fcm.googleapis.com/v1/projects/%s/messages:send" % sa["project_id"]
        headers = {"Authorization": "Bearer %s" % token,
                   "Content-Type": "application/json"}
        thread = msg.thread_id
        data = {
            "type": "sms",
            "title": thread.contact_name or thread.phone_normalized or "Nouveau SMS",
            "body": (msg.body or "")[:180] or "📎 Pièce jointe",
            "thread_id": str(thread.id),
            "message_id": str(msg.id),
            "line_id": str(msg.line_id.id or ""),
        }
        for dev in devices:
            payload = {"message": {
                "token": dev.fcm_token,
                "data": data,
                "android": {"priority": "HIGH"},
            }}
            try:
                r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
                if r.status_code == 200:
                    continue
                dead = r.status_code == 404 or (
                    r.status_code in (400, 403)
                    and ("UNREGISTERED" in r.text or "NOT_FOUND" in r.text
                         or "InvalidRegistration" in r.text))
                if dead:
                    dev.write({"fcm_token": False})
                    _logger.info("FCM : jeton mort (device %s) purgé.", dev.id)
                else:
                    _logger.warning("FCM send HTTP %s : %s", r.status_code, r.text[:200])
            except Exception:
                _logger.warning("FCM : erreur d'envoi.", exc_info=True)
