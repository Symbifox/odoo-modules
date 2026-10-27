from odoo import api, fields, models

from ..hooks import PARAM_HIDE_ENABLED, hide_third_party_menus, restore_third_party_menus
from .bf_budget import DEFAULT_ALERT_AMOUNT, DEFAULT_ALERT_PCT, PARAM_DEFAULT_AMOUNT, PARAM_DEFAULT_PCT


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_budget_hide_thirdparty_menus = fields.Boolean(
        string="Hide budget menus from third-party modules",
        config_parameter=PARAM_HIDE_ENABLED,
        help="Several accounting add-ons ship their own \"Budgets\" menu, "
             "usually empty. This setting hides them in favor of this "
             "one. Nothing is deleted: unchecking turns them back on.",
    )

    bf_budget_default_alert_threshold_pct = fields.Float(
        string="Default alert threshold (%)",
        config_parameter=PARAM_DEFAULT_PCT,
        default=DEFAULT_ALERT_PCT,
        help="Given to new budgets. Each budget keeps its own and can "
             "change it.",
    )
    bf_budget_default_alert_threshold_amount = fields.Float(
        string="Default alert floor",
        config_parameter=PARAM_DEFAULT_AMOUNT,
        default=DEFAULT_ALERT_AMOUNT,
        help="Given to new budgets, in the currency of their company. "
             "Below this amount, a variance is not flagged.",
    )

    def set_values(self):
        """Applique le réglage tout de suite, dans les deux sens.

        Un réglage qui n'agit qu'à la prochaine installation n'est pas un réglage.
        L'action est IDEMPOTENTE et ne dépend pas d'une transition : un greffon
        tiers installé après nous doit se faire masquer au prochain
        enregistrement des réglages, pas seulement si la case vient de changer.
        """
        super().set_values()
        self._bf_budget_store_alert_defaults()
        for record in self:
            if record.bf_budget_hide_thirdparty_menus:
                hide_third_party_menus(record.env)
            else:
                restore_third_party_menus(record.env)

    def _bf_budget_store_alert_defaults(self):
        """Écrit les deux seuils par défaut, zéro compris.

        🔴 Le cœur d'Odoo EFFACE un paramètre numérique qui vaut 0 (`repr(value)
        if value else False`) : 0 % ou 0 $ reviendrait alors au repli de 10 % et
        250 $. Écrit tel quel, « 0.0 » se relit comme un vrai zéro.
        """
        ICP = self.env["ir.config_parameter"].sudo()
        for record in self:
            ICP.set_param(PARAM_DEFAULT_PCT, repr(record.bf_budget_default_alert_threshold_pct))
            ICP.set_param(PARAM_DEFAULT_AMOUNT, repr(record.bf_budget_default_alert_threshold_amount))
