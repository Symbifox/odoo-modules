from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

EXPENSE_TYPES = ("expense", "expense_depreciation", "expense_direct_cost")
REVENUE_TYPES = ("income", "income_other")


class BfBudgetPosition(models.Model):
    """Le poste budgétaire : un regroupement nommé de comptes du grand livre.

    C'est l'axe principal du module. Il est délibérément grossier : trois à cinq
    postes expliquent l'essentiel de l'écart, et un poste par compte rend un
    budget illisible que plus personne ne consulte.
    """

    _name = "bf.budget.position"
    _description = "Budget item"
    _order = "budget_type, code, name"

    name = fields.Char(string="Name", required=True, translate=True)
    code = fields.Char(help="Short code, used for display order and reports.")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    budget_type = fields.Selection(
        [("expense", "Expenses"), ("revenue", "Revenue")],
        required=True,
        default="expense",
    )
    account_ids = fields.Many2many(
        "account.account",
        "bf_budget_position_account_rel",
        "position_id",
        "account_id",
        string="Accounts",
        required=True,
        help="The general ledger accounts whose entries feed this budget item.",
    )
    account_count = fields.Integer(compute="_compute_account_count")
    note = fields.Text(string="Rationale", help="Why this budget item "
                                                   "exists, and what it "
                                                   "does not cover.")

    _sql_constraints = [
        (
            "code_company_uniq",
            "unique(code, company_id)",
            "A budget item code must be unique per company.",
        ),
    ]

    @api.depends("account_ids")
    def _compute_account_count(self):
        for position in self:
            position.account_count = len(position.account_ids)

    @api.constrains("account_ids", "company_id")
    def _check_accounts_company(self):
        """Un poste ne peut pas pointer un compte d'une autre société.

        ⚠️ Depuis Odoo 18, `account.account` porte `company_ids` (plusieurs
        sociétés), pas `company_id` : un compte partagé est légitime, seul un
        compte étranger à la société du poste est refusé.
        """
        for position in self:
            foreign = position.account_ids.filtered(
                lambda a, p=position: a.company_ids and p.company_id not in a.company_ids
            )
            if foreign:
                raise ValidationError(
                    _(
                        "These accounts do not belong to the company "
                        "\"%(company)s\": %(accounts)s",
                        company=position.company_id.display_name,
                        accounts=", ".join(foreign.mapped("display_name")),
                    )
                )

    @api.constrains("account_ids", "budget_type")
    def _check_account_direction(self):
        """Un poste de charges ne se remplit pas de comptes de produits.

        La règle n'est pas cosmétique : le signe du réalisé dépend du sens du
        poste. Un compte de produit rangé dans un poste de charges rendrait un
        réalisé négatif que personne ne saurait lire.
        """
        for position in self:
            expected = EXPENSE_TYPES if position.budget_type == "expense" else REVENUE_TYPES
            wrong = position.account_ids.filtered(lambda a, e=expected: a.account_type not in e)
            if wrong:
                raise ValidationError(
                    _(
                        "The budget item \"%(name)s\" is of type "
                        "%(kind)s, but these accounts are not: "
                        "%(accounts)s",
                        name=position.display_name,
                        kind=_("expense") if position.budget_type == "expense" else _("revenue"),
                        accounts=", ".join(
                            "%s (%s)" % (a.display_name, a.account_type) for a in wrong
                        ),
                    )
                )

    @api.model
    def _operating_account_domain(self, budget_type, company):
        """Les comptes d'exploitation d'une société, pour un sens donné."""
        types = EXPENSE_TYPES if budget_type == "expense" else REVENUE_TYPES
        return [
            ("account_type", "in", list(types)),
            ("deprecated", "=", False),
            ("company_ids", "in", company.id),
        ]

    def action_view_accounts(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Budget item accounts"),
            "res_model": "account.account",
            "view_mode": "list,form",
            "domain": [("id", "in", self.account_ids.ids)],
        }
