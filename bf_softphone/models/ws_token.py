import base64
import hmac
import hashlib
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

# Durée de validité d'un jeton WS. Ce n'est PAS un secret de fraîcheur : le but du
# jeton est de prouver qu'il a été émis par Odoo à un membre du groupe, pas qu'il
# est récent. 12 h couvre une journée de travail ; au rechargement de page, le
# service en redemande un neuf. JsSIP réutilise l'URL (jeton inclus) à chaque
# reconnexion — la TTL doit donc dépasser la durée d'une session ouverte.
_WS_TOKEN_TTL = 12 * 3600


class ResUsersWsToken(models.Model):
    _inherit = "res.users"

    @staticmethod
    def _ws_secret(env):
        """Clé serveur stable pour signer les jetons WS (database.secret)."""
        return (env["ir.config_parameter"].sudo().get_param("database.secret") or "").encode()

    @api.model
    def _issue_ws_token(self, uid, now_ts):
        """Émet un jeton signé pour l'utilisateur uid. now_ts vient de l'appelant
        (pas de Date.now() implicite). Format : base64url('uid:exp:sig')."""
        exp = int(now_ts) + _WS_TOKEN_TTL
        msg = ("%d:%d" % (int(uid), exp)).encode()
        sig = hmac.new(self._ws_secret(self.env), msg, hashlib.sha256).hexdigest()
        raw = "%d:%d:%s" % (int(uid), exp, sig)
        return base64.urlsafe_b64encode(raw.encode()).decode()

    @api.model
    def _verify_ws_token(self, token, now_ts):
        """Valide un jeton. Retourne l'uid (int) si valide, sinon None.
        Sans état : recalcule la signature et compare en temps constant."""
        try:
            raw = base64.urlsafe_b64decode((token or "").encode()).decode()
            uid_s, exp_s, sig = raw.split(":", 2)
            uid, exp = int(uid_s), int(exp_s)
        except Exception:
            return None
        if int(now_ts) > exp:
            return None
        expected = hmac.new(
            self._ws_secret(self.env), ("%d:%d" % (uid, exp)).encode(),
            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return uid
