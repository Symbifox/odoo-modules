"""La page d'entente : ce que voit le visiteur entre son code et le contenu.

Une page intercalaire plutôt qu'une redirection sèche vers bf_sign. Trois
raisons, toutes vécues :

* le visiteur vient de confirmer son identité sur une page de marque ; se
  retrouver sans transition sur un écran de signature sans lui dire pourquoi
  fait fuir ;
* bf_sign ne sait pas d'où l'on vient et n'a pas d'URL de retour. La page de
  confirmation reçoit donc, ici, un lien de retour vers le transfert ;
* la demande de signature doit être créée quelque part. La créer au moment où
  le visiteur DEMANDE à signer, et pas à chaque coup d'œil sur la page,
  garde le journal lisible.
"""
import logging

from odoo import _
from odoo.http import request, route

from odoo.addons.bf_securetransfer.controllers.main import (
    SecureTransferController,
    _apply_locale,
    _apply_security_headers,
    _client_ip,
    _render_page,
    _resolve_transfer_by_token,
    _session_member,
    _user_agent,
)

_logger = logging.getLogger(__name__)


class SecureTransferSignPortal(SecureTransferController):

    def _nda_context(self, transfer, token, locale, member, **kw):
        visuals = transfer.brand_id._visuals()
        return {
            "brand": transfer.brand_id,
            "visuals": visuals,
            "transfer": transfer,
            "token": token,
            "locale": locale,
            "identity_label": member.display_identity if member else "",
            "nda_state": member.nda_state if member else "pending",
            "nda_error": kw.get("nda_error"),
        }

    @route("/s/<string:token>/nda", type="http", auth="public",
           methods=["GET"], sitemap=False)
    def st_nda_page(self, token, **kw):
        """L'entente présentée. Aucun contenu du transfert n'y transparaît."""
        locale = _apply_locale()
        transfer = _resolve_transfer_by_token(token)
        if transfer is None or transfer.state in ("draft", "cancelled"):
            return request.not_found()
        visuals = transfer.brand_id._visuals()
        available, _reason = transfer._is_available()
        if not available or not transfer.nda_required:
            return request.redirect("/s/%s" % token, code=303)
        # ⚠ Les barrières amont valent ici aussi : sans le mot de passe et sans
        # le code, on n'a rien à montrer — pas même le titre de l'entente.
        if transfer.has_password \
                and not request.session.get("st_unlock_%d" % transfer.id):
            return request.redirect("/s/%s" % token, code=303)
        if not request.session.get("st_otp_ok_%d" % transfer.id):
            return request.redirect("/s/%s" % token, code=303)
        member = _session_member(transfer)
        if member and member.state == "blocked":
            return request.redirect("/s/%s" % token, code=303)
        if member and member._nda_ok():
            return request.redirect("/s/%s" % token, code=303)
        # ⚠ Aucune identité : on RÉPOND ici plutôt que de renvoyer vers
        # `/s/<token>`. Un aller-retour entre deux pages qui se renvoient la
        # balle est une boucle pour le navigateur, et le visiteur n'en tire
        # aucune explication. Cette page-là peut lui dire quoi faire.
        ctx = self._nda_context(transfer, token, locale, member, **kw)
        if not member:
            ctx["nda_state"] = "no_identity"
        response = _render_page("bf_securetransfer_sign.page_nda", ctx)
        return _apply_security_headers(response, img_host=visuals.get("logo_host"))

    @route("/s/<string:token>/nda/sign", type="http", auth="public",
           methods=["POST"], csrf=False, sitemap=False)
    def st_nda_sign(self, token, **post):
        """Créer (au besoin) l'entente de CE visiteur et l'y conduire.

        En POST : la création d'une demande de signature est un effet de bord,
        elle n'a pas sa place derrière un GET que le navigateur peut rejouer,
        précharger ou mettre en cache.
        """
        _apply_locale()
        transfer = _resolve_transfer_by_token(token)
        if transfer is None or transfer.state in ("draft", "cancelled"):
            return request.not_found()
        if not transfer.nda_required or not transfer._is_available()[0]:
            return request.redirect("/s/%s" % token, code=303)
        if transfer.has_password \
                and not request.session.get("st_unlock_%d" % transfer.id):
            return request.redirect("/s/%s" % token, code=303)
        if not request.session.get("st_otp_ok_%d" % transfer.id):
            return request.redirect("/s/%s" % token, code=303)
        member = _session_member(transfer)
        if not member or member.state == "blocked":
            return request.redirect("/s/%s" % token, code=303)
        ip, ua = _client_ip(), _user_agent()
        url = member._nda_signing_url(ip=ip, ua=ua)
        if not url:
            _logger.warning(
                "bf_securetransfer_sign: aucune URL de signature pour %s sur %s",
                member.display_identity, transfer.name)
            return request.redirect("/s/%s/nda?nda_error=1" % token, code=303)
        # Le retour après signature est reconstruit par la page de confirmation
        # de bf_sign, à partir de l'enregistrement source (cf.
        # `bf.sign.request._st_return_url`) — pas depuis la session, qui peut
        # se perdre entre les deux pages.
        return request.redirect(url, code=303)
