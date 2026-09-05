"""Appareil mobile (app Android native) lié à la messagerie SMS.

Chaque enregistrement = une installation de l'app native, authentifiée par un
``device_token`` porteur (Bearer) émis à la connexion et un ``fcm_token`` pour la
notification push (Firebase Cloud Messaging). Séparé de ``sms.archive.push.subscription``
(qui, lui, sert le Web Push du navigateur/PWA).
"""
import base64
import hashlib
import logging
import secrets
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

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
    # 🔴 Le défi PKCE, et la raison d'être de tout ce mécanisme : un schéma
    # d'application personnalisé n'est PAS exclusif sur Android. Une autre
    # application peut déclarer ``com.bluefoxconsultant.sms://auth`` et recevoir
    # le code d'appariement à la place de la nôtre. Sans PKCE elle l'échangerait
    # contre un jeton porteur, donc contre la messagerie SMS de la personne.
    # Avec, le code intercepté ne vaut rien : l'échange exige un vérificateur
    # que seule l'application qui a lancé l'appariement possède, et qui n'est
    # jamais sorti d'elle.
    #
    # ⚠️ L'allowlist de schémas de redirection ne remplace pas ceci : elle ferme
    # la redirection ouverte CÔTÉ SERVEUR, pas l'interception locale du code sur
    # l'appareil.
    pkce_challenge = fields.Char(string="Défi PKCE", copy=False,
                                 groups="bf_sms_archive.group_sms_manager")

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
    def _issue_pending(self, user_id, name=None, platform="android",
                       challenge=None):
        """Émet un appareil + un code d'échange unique (retourné au navigateur).
        Le jeton porteur n'est révélé qu'à l'échange HTTPS."""
        device = self._issue(user_id, name=name, platform=platform)
        code = secrets.token_urlsafe(24)
        device.write({
            "pending_code": code,
            "pending_code_expiry": fields.Datetime.now() + timedelta(minutes=CODE_TTL_MINUTES),
            "pkce_challenge": challenge or False,
        })
        return code

    @api.model
    def _verifie_pkce(self, attendu, verificateur):
        """Vrai quand le vérificateur correspond au défi enregistré.

        ⚠️ Le défi est le SHA-256 du vérificateur, en base64url sans
        remplissage : la méthode « plain » de la norme n'est pas acceptée ici,
        elle laisserait passer un défi égal au vérificateur et ne protégerait
        de rien.
        """
        if not verificateur:
            return False
        condense = hashlib.sha256(verificateur.encode("utf-8")).digest()
        calcule = base64.urlsafe_b64encode(condense).decode().rstrip("=")
        # Comparaison à temps constant : un défi se compare comme un secret.
        return secrets.compare_digest(calcule, (attendu or "").strip())

    @api.model
    def _exchange(self, code, verificateur=None):
        """Échange un code unique non expiré contre le jeton porteur.

        À usage unique (le code est consommé), et le code seul ne suffit PAS :
        il faut le vérificateur PKCE. Un schéma d'application personnalisé
        n'étant pas exclusif sur Android, c'est la seule chose qui distingue
        l'application qui a lancé l'appariement de celle qui a intercepté sa
        réponse. Retourne l'appareil ou vide.
        """
        if not code:
            return self.browse()
        device = self.sudo().search([("pending_code", "=", code)], limit=1)
        if not device:
            return self.browse()
        # ⚠️ L'appareil en attente est JETÉ, pas laissé en place, dans les deux
        # refus : son jeton porteur n'a jamais été révélé — seul l'échange le
        # rend — et un code présenté en boucle jusqu'à ce que la bonne
        # application arrive donnerait sa chance à celle qui l'a intercepté.
        if not device.pending_code_expiry \
                or device.pending_code_expiry < fields.Datetime.now():
            device.unlink()
            return self.browse()
        if not self._verifie_pkce(device.pkce_challenge, verificateur):
            _logger.warning(
                "Messagerie SMS : échange refusé, vérificateur PKCE absent "
                "ou faux")
            device.unlink()
            return self.browse()
        device.write({
            "pending_code": False,
            "pending_code_expiry": False,
            "pkce_challenge": False,
        })
        return device

    @api.model
    def _resolve(self, token):
        """Retourne l'appareil actif correspondant au jeton porteur (ou vide)."""
        if not token:
            return self.browse()
        return self.sudo().search(
            [("device_token", "=", token), ("active", "=", True)], limit=1,
        )
