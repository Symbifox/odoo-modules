import logging
import time
import urllib.parse

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


def _token_from_request(t):
    """Récupère le jeton, que l'appel vienne en direct (arg `t`) ou d'un
    sous-appel nginx auth_request. ⚠️ Dans un auth_request, les args de la requête
    d'origine ne sont PAS transmis au sous-appel ($args/$arg_t vides) ; nginx passe
    donc l'URI d'origine dans l'en-tête `X-Original-URI` (patron canonique), d'où on
    extrait `t`."""
    if t:
        return t
    orig = request.httprequest.headers.get("X-Original-URI", "")
    if "?" in orig:
        return urllib.parse.parse_qs(orig.split("?", 1)[1]).get("t", [None])[0]
    return None


class SoftphoneWsAuth(http.Controller):
    """Point de validation appelé par nginx (auth_request) avant de laisser un
    WebSocket SIP atteindre Asterisk. Élimine la force brute anonyme sur /ws :
    sans jeton émis par Odoo à un membre du groupe, la connexion est refusée.

    ⚠️ auth='public' : nginx appelle en serveur-à-serveur, SANS cookie de session
    Odoo. La validation est donc STATELESS (HMAC), elle ne dépend d'aucune session.
    csrf=False, save_session=False : GET pur, sans effet de bord.
    """

    @http.route("/bf_softphone/ws_auth", type="http", auth="public",
                methods=["GET"], csrf=False, save_session=False)
    def ws_auth(self, t=None, **kw):
        t = _token_from_request(t)
        uid = request.env["res.users"].sudo()._verify_ws_token(t, int(time.time()))
        if not uid:
            return request.make_response("", status=403)
        # Le jeton est signé ; on confirme que l'uid est toujours un membre actif
        # du groupe (révocation immédiate si on retire quelqu'un du groupe).
        user = request.env["res.users"].sudo().browse(uid)
        if not (user.exists() and user.active
                and user.has_group("bf_softphone.group_softphone_user")):
            return request.make_response("", status=403)
        return request.make_response("", status=200)
