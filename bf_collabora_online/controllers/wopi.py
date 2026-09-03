import json
import logging

from odoo import http
from odoo.http import request

from odoo.addons.collabora_odoo.controllers.cool_wopi_controller import CoolWopiController
from odoo.addons.collabora_odoo.utils import jwt as jwt_amont

_logger = logging.getLogger(__name__)


class BfCoolWopiController(CoolWopiController):
    """Corriger `IsAdminUser` sans recopier le contrôleur amont.

    On laisse la méthode d'origine produire toute la réponse, puis on remplace
    la seule valeur fautive. Recopier les quarante lignes du `CheckFileInfo`
    reviendrait à forker : chaque correctif amont serait à refaire à la main,
    y compris ceux qui touchent l'accès.
    """

    @http.route()
    def file_info(self, attachment_id, access_token, access_token_ttl=0):
        reponse = super().file_info(
            attachment_id, access_token, access_token_ttl=access_token_ttl)
        if getattr(reponse, "status_code", None) != 200:
            return reponse
        try:
            charge = json.loads(reponse.data)
        except (TypeError, ValueError):
            # Une réponse qu'on ne sait pas relire se laisse passer telle
            # quelle : le rôle de ce module est de corriger une valeur, pas de
            # casser l'ouverture d'un document.
            return reponse

        # ⚠️ La route est `auth='public'` : `request.env.user` est l'usager
        # public, pas la personne. L'identité vraie est dans le jeton, comme
        # côté amont.
        jeton = jwt_amont.verify_token(request, access_token)
        if "error" in jeton:
            return reponse
        try:
            charge["IsAdminUser"] = bool(
                jeton["user"].sudo().has_group("base.group_system"))
        except Exception:
            _logger.exception("Collabora : IsAdminUser non résolu, valeur prudente")
            charge["IsAdminUser"] = False

        reponse.data = json.dumps(charge).encode()
        return reponse
