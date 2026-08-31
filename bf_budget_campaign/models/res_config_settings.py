# -*- coding: utf-8 -*-
"""Le taux de revient par défaut, réglable par instance.

Un taux horaire ne se devine pas depuis le code : il dépend de la structure de
rémunération de chaque organisation. Le module en propose donc un, il ne
l'impose pas, et il ne s'en sert JAMAIS pour remplacer un coût réel.
"""

from odoo import api, fields, models

PARAM_TAUX = "bf_budget_campaign.default_hourly_cost"
TAUX_DEFAUT = 50.0


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_campaign_hourly_cost = fields.Float(
        string="Taux de revient par défaut",
        config_parameter=PARAM_TAUX,
        default=TAUX_DEFAUT,
        help="Sert UNIQUEMENT à estimer les heures qu'Odoo n'a pas pu valoriser,"
             " faute de coût horaire sur la fiche employé. L'estimation est"
             " affichée à part et n'entre jamais dans la dépense réelle.",
    )

    @api.model
    def _bf_campaign_hourly_cost(self):
        """Le taux retenu, avec un repli explicite.

        ⚠️ `get_param` d'une clé absente rend `False`, et `float(False)` vaut 0,0 :
        sans ce repli, une instance qui n'a jamais ouvert les réglages estimerait
        toutes ses heures à zéro, ce qui est exactement le silence qu'on veut
        éviter.
        """
        brut = self.env["ir.config_parameter"].sudo().get_param(PARAM_TAUX)
        if brut in (False, None, ""):
            return TAUX_DEFAUT
        try:
            return float(brut)
        except (TypeError, ValueError):
            return TAUX_DEFAUT
