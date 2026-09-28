"""L'application Symbifox Pastilles ne voit pas les étiquettes vierges.

Deux fuites qu'un lot de 500 étiquettes ouvrait dans l'application du socle :

* « Mes pastilles » s'arrête à 100 lignes, triées par dernier tapotement. Des
  centaines d'étiquettes jamais scannées y repoussaient les vraies pastilles hors
  de la liste, sans que rien ne le signale.
* Le catalogue de gravure proposait « Étiquette à associer » : graver une puce
  avec ce geste la rendait morte-née.

Les routes du socle ne changent pas : elles posent un drapeau de contexte, et les
recherches de ``bf.nfc.tag`` et ``bf.nfc.gesture`` écartent alors l'état vierge.
Les écrans du site, eux, continuent de tout montrer.
"""
from odoo import http
from odoo.http import request

from odoo.addons.bf_nfc.controllers.mobile import MobileNfc


class MobileNfcSansVierges(MobileNfc):

    @http.route()
    def mes_pastilles(self, limit=100, **kw):
        request.update_context(bf_qr_sans_vierges=True)
        return super().mes_pastilles(limit=limit, **kw)

    @http.route()
    def catalogue(self, **kw):
        request.update_context(bf_qr_sans_vierges=True)
        return super().catalogue(**kw)

    @http.route()
    def creer_pastille(self, **kw):
        request.update_context(bf_qr_sans_vierges=True)
        return super().creer_pastille(**kw)
