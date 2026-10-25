from odoo import _, api, models
from odoo.exceptions import ValidationError


class AccountMove(models.Model):
    """La pièce d'une note de frais ne se rattache pas à un abonnement : ses dépenses, si.

    Elle regroupe toutes les dépenses de la période, au nom de l'employé. Rattachée,
    elle compterait les dépenses des autres abonnements, et celles de l'abonnement
    une deuxième fois si elles y sont déjà liées.
    """
    _inherit = "account.move"

    def _suggest_subscription(self):
        if self.expense_sheet_id:
            return False
        return super()._suggest_subscription()

    @api.constrains("subscription_id", "expense_sheet_id")
    def _check_subscription_not_expense_report(self):
        for move in self:
            if move.subscription_id and move.expense_sheet_id:
                raise ValidationError(_(
                    "%(move)s comes from the expense report %(sheet)s: link its "
                    "expenses to the subscription instead, not the entry."
                ) % {"move": move.display_name, "sheet": move.expense_sheet_id.display_name})
