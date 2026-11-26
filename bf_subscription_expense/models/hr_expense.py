from odoo import fields, models


class HrExpense(models.Model):
    _inherit = "hr.expense"

    # 🔴 `groups` AU CHAMP : tout employé ouvre ses notes de frais, et
    # le registre ne s'ouvre plus qu'à qui on l'attribue. Caché à la seule vue,
    # le champ resterait lisible par RPC (le nom d'affichage se lit en sudo) et
    # inscriptible : un employé rattacherait sa dépense à n'importe quel
    # abonnement, et elle compterait dans son coût cumulé.
    subscription_id = fields.Many2one(
        "subscription.subscription",
        string="Subscription",
        groups="bf_subscription.group_subscription_user",
        index=True, copy=False, ondelete="set null", tracking=True, check_company=True,
        domain="[('company_id', '=', company_id)]",
        help="Subscription this expense pays. An approved or paid expense counts "
             "in the subscription's total paid and its last bill date.",
    )
