"""Push via UnifiedPush (ntfy) — app Android native, SANS Google/Firebase.

L'app s'enregistre auprès d'un distributeur UnifiedPush (l'app ntfy du téléphone)
et obtient une URL d'``endpoint``. Le serveur POST le message JSON à cette URL
(auth par jeton ntfy ``up*``) ; ntfy le relaie au distributeur, qui le remet à
l'app, laquelle dessine ELLE-MÊME la notification (→ deep-link + réponse rapide).

Messages :
  - {type:"sms", title, body, thread_id, message_id}  → nouvelle notification
  - {type:"clear", thread_id}                          → efface la notif d'un fil
  - {type:"clear_all"}                                 → efface toutes les notifs
"""
import json
import logging

import requests

from odoo import api, models

_logger = logging.getLogger(__name__)

NTFY_TOKEN_PARAM = "bf_sms_archive.ntfy_publish_token"
POST_TIMEOUT = 8


class SmsUnifiedPush(models.AbstractModel):
    _name = "sms.archive.unifiedpush"
    _description = "Envoi push UnifiedPush/ntfy (app native, sans Google)"

    @api.model
    def _devices(self, owner):
        return self.env["sms.archive.mobile.device"].sudo().search([
            ("user_id", "=", owner.id), ("active", "=", True),
            ("push_endpoint", "!=", False),
        ])

    @api.model
    def _post(self, endpoint, payload):
        token = self.env["ir.config_parameter"].sudo().get_param(NTFY_TOKEN_PARAM)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer %s" % token
        return requests.post(
            endpoint, data=json.dumps(payload), headers=headers, timeout=POST_TIMEOUT)

    @api.model
    def _send(self, owner, payload):
        """Envoie ``payload`` aux endpoints de l'utilisateur. Défensif : ne lève
        jamais. Purge les endpoints morts (403/404/410)."""
        for dev in self._devices(owner):
            try:
                resp = self._post(dev.push_endpoint, payload)
                if resp.status_code in (403, 404, 410):
                    dev.write({"push_endpoint": False})
                    _logger.info("UnifiedPush endpoint mort (device %s, HTTP %s) purgé.",
                                 dev.id, resp.status_code)
                elif resp.status_code >= 400:
                    _logger.warning("UnifiedPush HTTP %s : %s",
                                    resp.status_code, resp.text[:150])
            except Exception:
                _logger.warning("UnifiedPush : erreur d'envoi.", exc_info=True)

    @api.model
    def _notify_new_message(self, msg):
        thread = msg.thread_id
        payload = {
            "type": "sms",
            "title": thread.contact_name or thread.phone_normalized or "Nouveau SMS",
            "body": (msg.body or "")[:180] or "📎 Pièce jointe",
            "thread_id": thread.id,
            "message_id": msg.id,
        }
        for user in msg._notify_users():
            if self._devices(user):
                self._send(user, dict(payload))

    @api.model
    def _notify_clear(self, owner, thread_id):
        """Efface la notification d'un fil sur les appareils (lu ailleurs)."""
        if not owner:
            return
        self._send(owner, {"type": "clear", "thread_id": int(thread_id)})

    @api.model
    def _notify_clear_all(self, owner):
        if not owner:
            return
        self._send(owner, {"type": "clear_all"})
