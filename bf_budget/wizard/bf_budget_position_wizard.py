from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import LazyTranslate

_lt = LazyTranslate(__name__)

# Regroupements proposés. Volontairement grossiers : trois à cinq postes
# expliquent l'essentiel de l'écart, et un poste par compte rend un budget que
# plus personne ne lit.
GROUPS = {
    "expense": [
        ("COGS", _lt("Cost of sales"), ["expense_direct_cost"]),
        ("OPEX", _lt("Operating expenses"), ["expense"]),
        ("AMORT", _lt("Depreciation"), ["expense_depreciation"]),
    ],
    "revenue": [
        ("REV", _lt("Operating revenue"), ["income"]),
        ("REVAUT", _lt("Other income"), ["income_other"]),
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
    _description = "Budget item suggestion"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    budget_type = fields.Selection(
        [("expense", "Expenses"), ("revenue", "Revenue")],
        required=True,
        default="expense",
    )
    preview = fields.Text(compute="_compute_preview", string="What will "
                                                             "be created")

    @api.depends("company_id", "budget_type")
    def _compute_preview(self):
        for wizard in self:
            lines = []
            for code, name, types in wizard._proposals():
                accounts = wizard._accounts_for(types)
                if accounts:
                    lines.append(
                        _("%(code)s - %(name)s: %(n)s account(s)",
                          code=code, name=name, n=len(accounts))
                    )
            wizard.preview = "\n".join(lines) or _(
                "No operating account of this type in this company."
            )

    def _proposals(self):
        # Les noms deviennent ceux des postes créés : dans la langue de qui les crée.
        self.ensure_one()
        return [(code, self.env._(name), types) for code, name, types in GROUPS[self.budget_type]]

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
                        "Suggested from the chart of accounts on "
                        "%(date)s. Split it if a budget item grows too "
                        "heavy to read.",
                        date=fields.Date.context_today(self),
                    ),
                }
            )
        if not created:
            raise UserError(
                _("Nothing to create: these budget items already exist, "
                  "or no account matches.")
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Budget items"),
            "res_model": "bf.budget.position",
            "view_mode": "list,form",
            "domain": [("id", "in", created.ids)],
        }
