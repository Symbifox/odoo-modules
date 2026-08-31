from odoo import api, fields, models

from ..hooks import PARAM_HIDE_ENABLED, hide_third_party_menus, restore_third_party_menus


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_budget_hide_thirdparty_menus = fields.Boolean(
        string="Masquer les menus budget des modules tiers",
        config_parameter=PARAM_HIDE_ENABLED,
        help="Plusieurs greffons comptables livrent leur propre menu « Budgets », "
        "généralement vide. Ce réglage les masque au profit de celui-ci. Rien "
        "n'est supprimé : décocher les rallume.",
    )

    def set_values(self):
        """Applique le réglage tout de suite, dans les deux sens.

        Un réglage qui n'agit qu'à la prochaine installation n'est pas un réglage.
        L'action est IDEMPOTENTE et ne dépend pas d'une transition : un greffon
        tiers installé après nous doit se faire masquer au prochain
        enregistrement des réglages, pas seulement si la case vient de changer.
        """
        super().set_values()
        for record in self:
            if record.bf_budget_hide_thirdparty_menus:
                hide_third_party_menus(record.env)
            else:
                restore_third_party_menus(record.env)
