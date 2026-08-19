"""Abonnements Web Push pour la messagerie SMS (notifications mobiles).

Chaque enregistrement = un abonnement push d'un navigateur/appareil (endpoint du
service push du navigateur + clés de chiffrement). L'envoi utilise VAPID (paire de
clés P-256 propre à l'instance, générée à l'installation) et ``pywebpush`` pour le
chiffrement aes128gcm.

Le canal push est INDÉPENDANT du bus Odoo : il est délivré par le service push du
navigateur (FCM/Mozilla) même quand l'onglet est fermé — c'est ce qui rend les
notifications fiables sur téléphone, contrairement à ``window.Notification`` qui
n'existe que tant que la SPA est ouverte au premier plan.
"""
import base64
import json
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

VAPID_PRIVATE_PARAM = "bf_sms_archive.vapid_private_key"
VAPID_PUBLIC_PARAM = "bf_sms_archive.vapid_public_key"
VAPID_SUBJECT_PARAM = "bf_sms_archive.vapid_subject"
DEFAULT_SUBJECT = "mailto:webmaster@example.com"
PUSH_TIMEOUT = 8  # s — borne le temps de blocage de l'ingestion webhook


def _messenger_path(env):
    """Chemin de la SPA « Messagerie », résolu dynamiquement depuis l'xmlid de
    l'action cliente — portable (pas d'id d'action codé en dur)."""
    try:
        action = env.ref("bf_sms_archive.sms_messenger_client_action")
        return "/odoo/action-%d" % action.id
    except Exception:
        return "/odoo"


class SmsPushSubscription(models.Model):
    _name = "sms.archive.push.subscription"
    _description = "Abonnement Web Push (messagerie SMS)"
    _rec_name = "endpoint"

    user_id = fields.Many2one(
        "res.users", string="Utilisateur", required=True, ondelete="cascade",
        index=True, default=lambda self: self.env.uid,
    )
    partner_id = fields.Many2one(
        "res.partner", related="user_id.partner_id", store=True, index=True,
    )
    endpoint = fields.Char(required=True, index=True)
    p256dh = fields.Char(string="Clé publique cliente", required=True)
    auth = fields.Char(string="Secret d'authentification", required=True)
    ua = fields.Char(string="Appareil / navigateur")
    active = fields.Boolean(default=True)
    fail_count = fields.Integer(default=0)
    last_push = fields.Datetime(string="Dernier envoi")

    _sql_constraints = [
        ("endpoint_uniq", "unique(endpoint)", "Cet abonnement push existe déjà."),
    ]

    # ── Gestion des clés VAPID ────────────────────────────────────────
    @api.model
    def _ensure_vapid_keys(self):
        """Génère une paire VAPID P-256 si absente. Idempotent. Retourne la clé
        publique (application server key, base64url non paddée) destinée au
        navigateur."""
        ICP = self.env["ir.config_parameter"].sudo()
        pub = ICP.get_param(VAPID_PUBLIC_PARAM)
        priv = ICP.get_param(VAPID_PRIVATE_PARAM)
        if pub and priv:
            return pub
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        key = ec.generate_private_key(ec.SECP256R1())
        private_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        raw_pub = key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        app_server_key = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode()
        ICP.set_param(VAPID_PRIVATE_PARAM, private_pem)
        ICP.set_param(VAPID_PUBLIC_PARAM, app_server_key)
        if not ICP.get_param(VAPID_SUBJECT_PARAM):
            ICP.set_param(VAPID_SUBJECT_PARAM, DEFAULT_SUBJECT)
        _logger.info(
            "VAPID générée pour la messagerie SMS (clé publique %s…).",
            app_server_key[:12],
        )
        return app_server_key

    # ── Abonnement ────────────────────────────────────────────────────
    @api.model
    def _upsert(self, user_id, endpoint, p256dh, auth, ua=None):
        """Crée/réactive l'abonnement pour ``endpoint`` (unique). Sudo : le
        navigateur pousse ses propres clés, l'utilisateur courant est le
        propriétaire."""
        vals = {
            "user_id": user_id, "endpoint": endpoint, "p256dh": p256dh,
            "auth": auth, "ua": (ua or "")[:120], "active": True, "fail_count": 0,
        }
        existing = self.sudo().search([("endpoint", "=", endpoint)], limit=1)
        if existing:
            existing.write(vals)
            return existing
        return self.sudo().create(vals)

    # ── Envoi ─────────────────────────────────────────────────────────
    @api.model
    def _notify_new_message(self, msg):
        """Envoie un Web Push aux abonnements des ayants droit (propriétaire du
        fil et utilisateurs de la ligne partagée) pour un message ENTRANT.
        Totalement défensif : ne lève jamais (l'ingestion prime)."""
        recipients = msg._notify_users()
        if not recipients:
            return
        subs = self.sudo().search(
            [("user_id", "in", recipients.ids), ("active", "=", True)]
        )
        if not subs:
            return
        ICP = self.env["ir.config_parameter"].sudo()
        priv = ICP.get_param(VAPID_PRIVATE_PARAM)
        if not priv:
            return
        try:
            from pywebpush import WebPushException, webpush
            from py_vapid import Vapid01
        except ImportError:
            _logger.warning("pywebpush/py_vapid absent : Web Push désactivé.")
            return
        try:
            vapid = Vapid01.from_pem(priv.encode())
        except Exception:
            _logger.warning("Clé privée VAPID illisible.", exc_info=True)
            return

        thread = msg.thread_id
        base = (ICP.get_param("web.base.url") or "").rstrip("/")
        subject = ICP.get_param(VAPID_SUBJECT_PARAM) or DEFAULT_SUBJECT
        payload = json.dumps({
            "title": thread.contact_name or thread.phone_normalized or "Nouveau SMS",
            "body": (msg.body or "")[:140] or "📎 Pièce jointe",
            "tag": "sms-thread-%s" % thread.id,
            "thread_id": thread.id,
            "url": "%s%s" % (base, _messenger_path(self.env)),
        })
        for sub in subs:
            try:
                webpush(
                    subscription_info={
                        "endpoint": sub.endpoint,
                        "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                    },
                    data=payload,
                    vapid_private_key=vapid,
                    vapid_claims={"sub": subject},
                    timeout=PUSH_TIMEOUT,
                )
                sub.sudo().write({
                    "last_push": fields.Datetime.now(), "fail_count": 0,
                })
            except WebPushException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in (404, 410):
                    # Endpoint mort (app désinstallée / abonnement expiré).
                    sub.sudo().write({"active": False})
                    _logger.info("Abonnement push %s désactivé (HTTP %s).", sub.id, status)
                else:
                    sub.sudo().write({"fail_count": sub.fail_count + 1})
                    _logger.warning("Web Push échec (HTTP %s) : %s", status, exc)
            except Exception:
                _logger.warning("Web Push : erreur inattendue.", exc_info=True)
