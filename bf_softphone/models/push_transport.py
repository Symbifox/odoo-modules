"""Le réveil d'appel annonce ce qu'il chiffre.

``sms.archive.unifiedpush._webpush_types`` dit à l'app quels types de messages
le serveur chiffre TOUJOURS pour un appareil qui a remis ses clés, et l'app
refuse un message de ces types qui arrive en clair. « call » ne vient pas de
``bf_sms_archive`` : c'est le réveil de ce module (``controllers/pbx_api.py``)
qui le pousse, chiffré depuis la même version.

⚠️ D'où l'extension ICI et non une liste en dur côté textos : un
``bf_softphone`` d'avant le chiffrement, sous un ``bf_sms_archive`` à jour,
n'annonce jamais « call ». Il pousse ses réveils en clair, et l'app, qui ne les
attend pas chiffrés, les laisse sonner.
"""
from odoo import api, models


class SmsUnifiedPushSoftphone(models.AbstractModel):
    _inherit = "sms.archive.unifiedpush"

    @api.model
    def _webpush_types(self):
        return super()._webpush_types() + ["call"]
