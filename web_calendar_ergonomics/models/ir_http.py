# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Les préférences de surlignage dans la session.

La vue calendrier les lit au chargement, sans RPC de plus, comme Odoo expose
ses autres réglages d'usager.
"""

from odoo import models

from .res_users import CHAMPS_PROPRES


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        info = super().session_info()
        if info:
            usager = self.env.user
            for champ in CHAMPS_PROPRES:
                info[champ] = usager[champ]
        return info
