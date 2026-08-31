from odoo import _, api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    ex_benefit_id = fields.Many2one(
        "bf.ex.benefit", string="Avantage",
        help="Les dépenses portant ce produit alimentent le registre d'usage "
             "de cet avantage.",
    )


class HrExpense(models.Model):
    _inherit = "hr.expense"

    ex_benefit_id = fields.Many2one(
        "bf.ex.benefit", string="Avantage",
        compute="_compute_ex_benefit_id", store=True, readonly=False,
        help="Repris du produit, modifiable. À l'approbation, une ligne d'usage "
             "confirmée est créée au montant réellement engagé.",
    )
    ex_usage_id = fields.Many2one(
        "bf.ex.usage", string="Usage produit", readonly=True, copy=False,
    )

    @api.depends("product_id")
    def _compute_ex_benefit_id(self):
        for expense in self:
            if expense.ex_benefit_id:
                continue
            expense.ex_benefit_id = expense.product_id.product_tmpl_id.ex_benefit_id

    def _ex_create_usage(self):
        """Créer la ligne d'usage d'une dépense, une seule fois.

        Le garde est `ex_usage_id` : `state` est un champ CALCULÉ sur
        `hr.expense`, donc il peut être recalculé plusieurs fois pour la même
        transition. Se fier à la transition seule créerait des doublons.
        """
        Usage = self.env["bf.ex.usage"].sudo()
        for expense in self:
            if expense.ex_usage_id or not expense.ex_benefit_id:
                continue
            if not expense.employee_id:
                continue
            usage = Usage.create({
                "employee_id": expense.employee_id.id,
                "benefit_id": expense.ex_benefit_id.id,
                "date": expense.date or fields.Date.context_today(expense),
                "quantity": expense.quantity or 1.0,
                "amount": expense.total_amount_currency or expense.total_amount,
                "source": "expense",
                "note": _("Note de frais : %s", expense.name or expense.display_name),
            })
            usage.action_confirm()
            expense.ex_usage_id = usage.id
        return True

    def write(self, vals):
        result = super().write(vals)
        # `state` est calculé et stocké : il n'apparaît pas toujours dans `vals`.
        # On regarde donc l'état APRÈS écriture plutôt que la valeur écrite.
        approved = self.filtered(
            lambda e: e.state in ("approved", "done") and not e.ex_usage_id
        )
        if approved:
            approved._ex_create_usage()
        return result
