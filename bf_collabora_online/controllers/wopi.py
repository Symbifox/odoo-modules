import json
import logging
import re

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


    # ------------------------------------------------------------------
    # 🔴 Le contexte de sociétés, que la couche site web écrase
    # ------------------------------------------------------------------
    def _bf_societes_du_navigateur(self):
        """Rendre à la requête les sociétés que la personne a réellement cochées.

        🔴 `cool_frame` est déclarée `website=True` en amont. Or
        `website/models/ir_http.py` FORCE `allowed_company_ids` à la société du
        site sur toute requête site web, en écrasant le choix du back-office.
        Conséquence mesurée en production : un document rattaché à un
        enregistrement d'une AUTRE société renvoie 403, et cocher la société
        dans le sélecteur n'y change rien, puisque le sélecteur n'est jamais lu.

        On relit donc le témoin `cids` que le navigateur envoie de toute façon,
        et on le recroise avec les sociétés auxquelles la personne a droit. On
        n'élargit jamais au-delà de `company_ids` : au pire on rend ce qu'elle
        obtiendrait en cochant toutes ses cases.
        """
        permises = request.env.user.company_ids.ids
        if len(permises) < 2:
            return
        # Odoo 18 sépare par « - » ; les versions antérieures par « , ».
        brut = request.httprequest.cookies.get("cids") or ""
        choisies = [int(n) for n in re.findall(r"\d+", brut)]
        actives = [c for c in choisies if c in permises] or permises
        if set(actives) != set(request.env.context.get("allowed_company_ids") or []):
            request.update_context(allowed_company_ids=actives)

    def _bf_societes_du_jeton(self, access_token):
        """Même correctif pour les appels du SERVEUR Collabora, qui n'a pas de témoin.

        `file_info` et `file_content` sont appelées par le serveur de documents,
        pas par le navigateur : aucune session, donc `allowed_company_ids`
        retombe sur la seule société principale de la personne. Un document
        d'une autre société se ferait refuser au milieu de l'édition, après que
        le cadre se soit ouvert.

        Le jeton n'autorise qu'UNE pièce, pour UNE personne, et `cool_frame` a
        déjà vérifié son droit d'écriture au moment de l'émettre. Rendre ici
        l'ensemble de ses sociétés ne lui donne donc rien de plus que ce que le
        jeton porte déjà.
        """
        jeton = jwt_amont.verify_token(request, access_token)
        if "error" in jeton:
            return
        societes = jeton["user"].sudo().company_ids.ids
        if societes:
            request.update_context(allowed_company_ids=societes)

    @http.route()
    def cool_frame(self, attachment_id, mode):
        self._bf_societes_du_navigateur()
        return super().cool_frame(attachment_id, mode)

    @http.route()
    def file_content(self, attachment_id, access_token, access_token_ttl=0):
        self._bf_societes_du_jeton(access_token)
        return super().file_content(
            attachment_id, access_token, access_token_ttl=access_token_ttl)

    @http.route()
    def file_info(self, attachment_id, access_token, access_token_ttl=0):
        self._bf_societes_du_jeton(access_token)
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
