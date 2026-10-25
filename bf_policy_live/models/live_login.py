# -*- coding: utf-8 -*-
"""Lien de connexion Odoo à usage unique de la clé USB live.

POURQUOI
Après le QR, la personne est authentifiée auprès d'Authentik (2FA comprise),
mais son navigateur, sur la clé, n'a encore aucune session. La clé live
demande ici un lien (POST /api/v1/policy/live/login-link, jeton du QR) et
l'ouvre dans Brave : Odoo s'ouvre connecté, sans rien taper.

LE JETON
32 octets aléatoires, remis UNE fois dans l'URL. On n'en garde que le
sha256 : un dump de la base ne donne aucun lien utilisable. Il vit 5 minutes
et se consomme par un UPDATE atomique (deux ouvertures simultanées : une seule
gagne).

PAR OÙ IL PASSE
Le jeton n'est accepté que par `res.users._check_credentials` avec le type
`bf_live_link`, et seulement pendant que la route du lien s'exécute (marqueur
`_LINK_ROUTE`, une variable de contexte Python, que rien ne transmet par RPC).
/web/login accepte un champ `type` venu du client : sans le marqueur, et sans
la clé `bf_live_token` (absente de ses CREDENTIAL_PARAMS), il ne passe pas.

2FA
Le justificatif rend `mfa: skip` : le lien n'est émis que pour un jeton dont
`amr` porte `mfa`, contrôlé par Odoo à la route login-link, donc la deuxième
étape a déjà eu lieu chez Authentik.

QUI LIT QUOI
Personne, sauf le système (sudo). Les administrateurs lisent les fiches (qui,
quand, d'où), jamais un jeton : il n'est stocké nulle part.
"""
import contextvars
import hashlib
import logging
import secrets
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessDenied, AccessError

_logger = logging.getLogger(__name__)

LINK_TYPE = "bf_live_link"
TOKEN_KEY = "bf_live_token"
TTL_SECONDS = 300
# Au-delà, un appelant qui tire des liens en boucle est refusé (429).
MAX_PER_HOUR = 20
# Un jeton urlsafe de 32 octets fait 43 caractères ; on borne large.
MAX_TOKEN_LEN = 128

# Vrai seulement pendant l'appel de la route du lien. Une ContextVar ne se
# règle ni par contexte Odoo, ni par paramètre HTTP, ni par RPC.
_LINK_ROUTE = contextvars.ContextVar("bf_policy_live_link_route", default=False)


def token_hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


class BfPolicyLiveLogin(models.Model):
    _name = "bf.policy.live.login"
    _description = "Blue Fox OS — Lien de connexion Odoo de clé USB live"
    _order = "id desc"

    token_hash = fields.Char(required=True, readonly=True, index=True, copy=False)
    user_id = fields.Many2one(
        "res.users", string="Personne", required=True, ondelete="cascade",
        index=True, readonly=True)
    expires_at = fields.Datetime("Expire le", required=True, readonly=True)
    used = fields.Boolean("Utilisé", default=False, readonly=True)
    used_at = fields.Datetime("Utilisé le", readonly=True)
    created_ip = fields.Char("IP de demande", readonly=True)
    used_ip = fields.Char("IP d'ouverture", readonly=True)

    _sql_constraints = [
        ("token_hash_uniq", "unique(token_hash)", "Jeton déjà émis."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("Un lien de connexion ne se crée que par la clé live."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise AccessError(_("Un lien de connexion ne se modifie pas."))
        return super().write(vals)

    # ------------------------------------------------- service (privé, RPC)
    @api.model
    def _issue(self, user, ip):
        """Rend le jeton brut (à mettre dans l'URL), ou None si trop de liens
        ont été tirés dans l'heure. Appel interne en sudo seulement."""
        if not (self.env.su and user and len(user) == 1):
            raise AccessError("_issue : appel interne en sudo seulement")
        now = fields.Datetime.now()
        recent = self.search_count([
            ("user_id", "=", user.id),
            ("create_date", ">", now - timedelta(hours=1)),
        ])
        if recent >= MAX_PER_HOUR:
            return None
        raw = secrets.token_urlsafe(32)
        self.create({
            "token_hash": token_hash(raw),
            "user_id": user.id,
            "expires_at": now + timedelta(seconds=TTL_SECONDS),
            "created_ip": ip,
        })
        return raw

    @api.model
    def _peek(self, raw):
        """(fiche, raison) SANS consommer : sert à retrouver le login à passer
        à session.authenticate et à journaliser la raison d'un refus."""
        if not raw or not isinstance(raw, str) or len(raw) > MAX_TOKEN_LEN:
            return self.browse(), "malformed"
        rec = self.sudo().search([("token_hash", "=", token_hash(raw))], limit=1)
        if not rec:
            return rec, "unknown"
        if rec.used:
            return rec, "already used"
        if rec.expires_at <= fields.Datetime.now():
            return rec, "expired"
        return rec, None

    @api.model
    def _consume(self, raw, uid, ip):
        """Consommation atomique : vrai si CE jeton, de CET usager, valide et
        jamais servi, vient d'être marqué utilisé par nous."""
        if not raw or not isinstance(raw, str) or len(raw) > MAX_TOKEN_LEN:
            return False
        now = fields.Datetime.now()
        self.env.cr.execute(
            """UPDATE bf_policy_live_login
                  SET used = TRUE, used_at = %s, used_ip = %s
                WHERE token_hash = %s AND user_id = %s
                  AND used IS NOT TRUE AND expires_at > %s
            RETURNING id""",
            (now, ip, token_hash(raw), uid, now))
        ok = bool(self.env.cr.fetchone())
        self.invalidate_model(["used", "used_at", "used_ip"])
        return ok

    @api.autovacuum
    def _gc_live_logins(self):
        """Purge quotidienne : liens expirés ou servis depuis plus d'un jour."""
        limit = fields.Datetime.now() - timedelta(days=1)
        old = self.sudo().search(["|", ("expires_at", "<", limit),
                                  "&", ("used", "=", True), ("used_at", "<", limit)])
        old.unlink()


class ResUsers(models.Model):
    _inherit = "res.users"

    def _check_credentials(self, credential, env):
        try:
            return super()._check_credentials(credential, env)
        except AccessDenied:
            if credential.get("type") != LINK_TYPE:
                raise
            raw = credential.get(TOKEN_KEY)
            user = self.env.user
            if not (_LINK_ROUTE.get() and raw and env.get("interactive")
                    and user.active and user._is_internal()):
                raise
            ip = env.get("REMOTE_ADDR") or "n/a"
            if not self.env["bf.policy.live.login"].sudo()._consume(raw, user.id, ip):
                raise
            _logger.info("[bf_policy_live] lien de connexion servi : user=%s ip=%s",
                         user.login, ip)
            return {"uid": user.id, "auth_method": LINK_TYPE, "mfa": "skip"}
