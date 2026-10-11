from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _get_translation_frontend_modules_name(cls):
        # Le bouton de thème de la page publique est libellé par
        # static/src/js/linkpage_theme.js. Le site ne charge les traductions
        # que des modules nommés ici : sans cette ligne, le visiteur lirait la
        # langue de la source quelle que soit la langue de la page.
        return super()._get_translation_frontend_modules_name() + ["bf_linkpage"]
