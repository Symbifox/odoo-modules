"""La page d'accusé de réception de l'avis.

Le lien part à la seule adresse désignée, mais un lien est un porteur : il revient dans les
réponses qui le citent. Accuser demande donc aussi un code à usage unique, envoyé à cette
adresse au moment de l'accusé (C-1.1, art. 31 : l'adresse est l'ancre de la réception). Le
GET montre l'avis et n'accuse jamais rien, parce que les filtres de courriel ouvrent les
liens tout seuls. Un mauvais jeton répond comme un avis inexistant.

La page n'emprunte pas la mise en page du site : un script de mesure d'audience y
recevrait l'adresse, jeton compris. Une page nue, sans référent, jamais mise en cache.
"""
from werkzeug.exceptions import NotFound

from odoo import SUPERUSER_ID, http
from odoo.exceptions import UserError
from odoo.http import request

HEADERS = [
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
    ("X-Robots-Tag", "noindex, nofollow"),
    ("X-Content-Type-Options", "nosniff"),
]


class PrivacyBreachAck(http.Controller):

    def _notice_or_404(self, notice_id, token):
        # Le système agit, pas le visiteur : connecté ou non, interne ou non, il n'écrit pas au fil.
        notice = request.env(user=SUPERUSER_ID)["privacy.breach.notice"].browse(notice_id).exists()
        if not notice or notice.state == "draft" or not notice._check_ack_token(token):
            raise NotFound()
        return notice

    @http.route("/privacy/breach/<int:notice_id>/<string:token>", type="http", auth="public",
                methods=["GET"], website=False, sitemap=False)
    def ack_page(self, notice_id, token, **kw):
        notice = self._notice_or_404(notice_id, token)
        return self._render(notice, token)

    def _render(self, notice, token, error=False, info=False):
        return request.render("privacy_breach_notice.ack_page", {
            "notice": notice, "token": token, "pdf_ok": notice._pdf_intact(), "error": error,
            "info": info, "masked": notice._ack_masked_address()}, headers=HEADERS)

    # csrf=False : le jeton de 256 bits de l'adresse rend la requête infalsifiable par un tiers,
    # et un navigateur qui bloque les témoins ne doit pas empêcher un RPRP d'accuser réception.
    @http.route("/privacy/breach/<int:notice_id>/<string:token>", type="http", auth="public",
                methods=["POST"], website=False, sitemap=False, csrf=False)
    def ack_submit(self, notice_id, token, name=None, title=None, sha256=None, confirm=None,
                   code=None, action=None, **kw):
        notice = self._notice_or_404(notice_id, token)
        if action == "code":
            return self._render(notice, token, info=notice._ack_send_code())
        error = False
        if not confirm:
            error = "Cochez la case pour confirmer la réception."
        else:
            # Hors du point de sauvegarde : un essai raté reste compté même si rien d'autre n'est écrit.
            ok, error = notice._ack_verify_code(code)
        if not error:
            try:
                # Un point de sauvegarde : si la note du chatter échoue après l'écriture, l'accusé
                # ne doit pas rester consigné pendant que la page affiche une erreur.
                with request.env.cr.savepoint():
                    notice._record_ack(
                        name, title, sha256, "link",
                        ip=request.httprequest.remote_addr,
                        user_agent=request.httprequest.headers.get("User-Agent"))
            except UserError as e:
                error = str(e)
            notice.invalidate_recordset()
        return self._render(notice, token, error=error)

    @http.route("/privacy/breach/<int:notice_id>/<string:token>/pdf", type="http", auth="public",
                methods=["GET"], website=False, sitemap=False)
    def ack_pdf(self, notice_id, token, **kw):
        notice = self._notice_or_404(notice_id, token)
        if not notice._pdf_intact():
            raise NotFound()
        pdf = notice.pdf_attachment_id.raw
        return request.make_response(pdf, headers=[
            ("Content-Type", "application/pdf"),
            ("Content-Disposition", f'inline; filename="{notice.name}-v{notice.version}.pdf"'),
        ] + HEADERS)
