"""Appareil mobile (app Android native) lié à la messagerie SMS.

Chaque enregistrement = une installation de l'app native, authentifiée par un
``device_token`` porteur (Bearer) émis à la connexion et un ``fcm_token`` pour la
notification push (Firebase Cloud Messaging). Séparé de ``sms.archive.push.subscription``
(qui, lui, sert le Web Push du navigateur/PWA).
"""
import secrets
from datetime import timedelta

from odoo import api, fields, models

CODE_TTL_MINUTES = 3  # durée de vie du code d'échange unique


class SmsMobileDevice(models.Model):
    _name = "sms.archive.mobile.device"
    _description = "Appareil mobile — messagerie SMS"
    _rec_name = "name"
    _order = "last_seen desc, id desc"

    user_id = fields.Many2one(
        "res.users", string="Utilisateur", required=True, ondelete="cascade",
        index=True,
    )
    name = fields.Char(string="Appareil", default="Appareil Android")
    device_token = fields.Char(
        string="Jeton d'appareil", required=True, index=True, copy=False,
        groups="bf_sms_archive.group_sms_manager",
    )
    fcm_token = fields.Char(string="Jeton FCM (legacy)", index=True)
    push_endpoint = fields.Char(
        string="Endpoint UnifiedPush",
        help="URL d'endpoint UnifiedPush (ntfy) vers laquelle pousser les "
             "notifications de cet appareil.")
    platform = fields.Char(default="android")
    app_version = fields.Char()
    active = fields.Boolean(default=True)
    last_seen = fields.Datetime(string="Vu la dernière fois")
    # Code unique à usage unique pour l'échange web-login → jeton (le vrai jeton
    # ne transite jamais dans une URL/deep-link).
    pending_code = fields.Char(index=True, copy=False,
                               groups="bf_sms_archive.group_sms_manager")
    pending_code_expiry = fields.Datetime()

    _sql_constraints = [
        ("device_token_uniq", "unique(device_token)",
         "Ce jeton d'appareil existe déjà."),
    ]

    @api.model
    def _issue(self, user_id, name=None, platform="android"):
        """Émet un nouvel appareil + jeton porteur pour l'utilisateur donné."""
        return self.sudo().create({
            "user_id": user_id,
            "name": (name or "Appareil Android")[:80],
            "device_token": secrets.token_urlsafe(32),
            "platform": platform,
        })

    @api.model
    def _issue_pending(self, user_id, name=None, platform="android"):
        """Émet un appareil + un code d'échange unique (retourné au navigateur).
        Le jeton porteur n'est révélé qu'à l'échange HTTPS."""
        device = self._issue(user_id, name=name, platform=platform)
        code = secrets.token_urlsafe(24)
        device.write({
            "pending_code": code,
            "pending_code_expiry": fields.Datetime.now() + timedelta(minutes=CODE_TTL_MINUTES),
        })
        return code

    @api.model
    def _exchange(self, code):
        """Échange un code unique non expiré contre le jeton porteur. À usage
        unique (le code est consommé). Retourne l'appareil ou vide."""
        if not code:
            return self.browse()
        device = self.sudo().search([("pending_code", "=", code)], limit=1)
        if not device or not device.pending_code_expiry \
                or device.pending_code_expiry < fields.Datetime.now():
            return self.browse()
        device.write({"pending_code": False, "pending_code_expiry": False})
        return device

    @api.model
    def _resolve(self, token):
        """Retourne l'appareil actif correspondant au jeton porteur (ou vide)."""
        if not token:
            return self.browse()
        return self.sudo().search(
            [("device_token", "=", token), ("active", "=", True)], limit=1,
        )
