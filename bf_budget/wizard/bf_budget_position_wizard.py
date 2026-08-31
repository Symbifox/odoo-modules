from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Regroupements proposés. Volontairement grossiers : trois à cinq postes
# expliquent l'essentiel de l'écart, et un poste par compte rend un budget que
# plus personne ne lit.
GROUPS = {
    "expense": [
        ("COGS", "Coût des ventes", ["expense_direct_cost"]),
        ("OPEX", "Charges d'exploitation", ["expense"]),
        ("AMORT", "Amortissements", ["expense_depreciation"]),
    ],
    "revenue": [
        ("REV", "Produits d'exploitation", ["income"]),
        ("REVAUT", "Autres produits", ["income_other"]),
    ],
}


class BfBudgetPositionWizard(models.TransientModel):
    """Propose des postes à partir du plan comptable réel de la société.

    ⛔ Aucun catalogue de postes n'est semé en dur : les plans comptables
    diffèrent d'une société et d'une localisation à l'autre, et un semis figé
    serait faux chez le premier client. L'assistant PROPOSE, l'utilisateur garde,
    fusionne ou jette.
    """

    _name = "bf.budget.position.wizard"
    _description = "Proposer des postes budgétaires"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    budget_type = fields.Selection(
        [("expense", "Charges"), ("revenue", "Produits")],
        required=True,
        default="expense",
    )
    preview = fields.Text(compute="_compute_preview", string="Ce qui sera créé")

    @api.depends("company_id", "budget_type")
    def _compute_preview(self):
        for wizard in self:
            lines = []
            for code, name, types in wizard._proposals():
                accounts = wizard._accounts_for(types)
                if accounts:
                    lines.append(
                        _("%(code)s — %(name)s : %(n)s compte(s)",
                          code=code, name=name, n=len(accounts))
                    )
            wizard.preview = "\n".join(lines) or _(
                "Aucun compte d'exploitation de ce sens dans cette société."
            )

    def _proposals(self):
        self.ensure_one()
        return GROUPS[self.budget_type]

    def _accounts_for(self, types):
        self.ensure_one()
        return self.env["account.account"].search(
            [
                ("account_type", "in", types),
                ("deprecated", "=", False),
                ("company_ids", "in", self.company_id.id),
            ]
        )

    def action_create(self):
        self.ensure_one()
        Position = self.env["bf.budget.position"]
        created = Position
        for code, name, types in self._proposals():
            accounts = self._accounts_for(types)
            if not accounts:
                continue
            existing = Position.search(
                [("code", "=", code), ("company_id", "=", self.company_id.id)], limit=1
            )
            if existing:
                continue
            created |= Position.create(
                {
                    "name": name,
                    "code": code,
                    "budget_type": self.budget_type,
                    "company_id": self.company_id.id,
                    "account_ids": [(6, 0, accounts.ids)],
                    "note": _(
                        "Proposé à partir du plan comptable le %(date)s. À découper "
                        "si un poste pèse trop lourd pour être lisible.",
                        date=fields.Date.context_today(self),
                    ),
                }
            )
        if not created:
            raise UserError(
                _("Rien à créer : ces postes existent déjà, ou aucun compte ne correspond.")
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Postes budgétaires"),
            "res_model": "bf.budget.position",
            "view_mode": "list,form",
            "domain": [("id", "in", created.ids)],
        }
