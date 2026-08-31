from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.tests.common import TransactionCase


class BfBudgetCommon(TransactionCase):
    """Un décor comptable minimal, monté à la main.

    ⛔ Volontairement SANS plan comptable de localisation : le module est destiné
    à un catalogue, et ses tests ne doivent dépendre d'aucun `l10n_*`. Tout ce
    dont ils ont besoin, c'est de quelques comptes, un journal et des écritures.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Société budget"})
        cls.env.user.write({"company_ids": [Command.link(cls.company.id)]})
        cls.env = cls.env(
            context=dict(cls.env.context, allowed_company_ids=[cls.company.id])
        )
        cls.currency = cls.company.currency_id

        Account = cls.env["account.account"].with_company(cls.company)
        cls.account_software = Account.create(
            {"name": "Licences logicielles", "code": "602100", "account_type": "expense"}
        )
        cls.account_telecom = Account.create(
            {"name": "Télécommunications", "code": "512202", "account_type": "expense"}
        )
        cls.account_revenue = Account.create(
            {"name": "Services", "code": "400000", "account_type": "income"}
        )
        cls.account_payable = Account.create(
            {"name": "Fournisseurs", "code": "200000", "account_type": "liability_payable",
             "reconcile": True}
        )
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "Opérations diverses",
                "code": "ODBG",
                "type": "general",
                "company_id": cls.company.id,
            }
        )

        cls.position_software = cls.env["bf.budget.position"].create(
            {
                "name": "Licences logicielles",
                "code": "SOFT",
                "budget_type": "expense",
                "company_id": cls.company.id,
                "account_ids": [Command.set(cls.account_software.ids)],
            }
        )
        cls.position_telecom = cls.env["bf.budget.position"].create(
            {
                "name": "Télécommunications",
                "code": "TEL",
                "budget_type": "expense",
                "company_id": cls.company.id,
                "account_ids": [Command.set(cls.account_telecom.ids)],
            }
        )
        cls.position_revenue = cls.env["bf.budget.position"].create(
            {
                "name": "Services rendus",
                "code": "REV",
                "budget_type": "revenue",
                "company_id": cls.company.id,
                "account_ids": [Command.set(cls.account_revenue.ids)],
            }
        )

        # Un exercice qui contient toujours aujourd'hui, pour que le théorique
        # ait un sens quelle que soit la date du jour.
        today = fields.Date.context_today(cls.env.user)
        cls.date_start = today.replace(month=1, day=1)
        cls.date_end = today.replace(month=12, day=31)

        cls.plan = cls.env["account.analytic.plan"].create({"name": "Projets budget"})
        cls.analytic_a = cls.env["account.analytic.account"].create(
            {"name": "Projet A", "plan_id": cls.plan.id}
        )
        cls.analytic_b = cls.env["account.analytic.account"].create(
            {"name": "Projet B", "plan_id": cls.plan.id}
        )

    @classmethod
    def _make_budget(cls, budget_type="expense", positions=None, planned=1200.0, state="draft"):
        budget = cls.env["bf.budget"].create(
            {
                "name": "Exercice test",
                "company_id": cls.company.id,
                "budget_type": budget_type,
                "date_start": cls.date_start,
                "date_end": cls.date_end,
            }
        )
        for position in positions or [cls.position_software]:
            cls.env["bf.budget.line"].create(
                {
                    "budget_id": budget.id,
                    "position_id": position.id,
                    "amount_planned": planned,
                }
            )
        if state != "draft":
            budget.action_open()
            if state != "open":
                budget.state = state
        return budget

    @classmethod
    def _post_bill(cls, account, amount, date=None, analytic=None, post=True):
        """Une écriture simple : une charge au débit, le fournisseur au crédit."""
        date = date or fields.Date.context_today(cls.env.user)
        line_vals = {
            "account_id": account.id,
            "debit": amount if amount > 0 else 0.0,
            "credit": -amount if amount < 0 else 0.0,
            "name": "Charge",
        }
        if analytic:
            line_vals["analytic_distribution"] = {str(analytic.id): 100.0}
        move = cls.env["account.move"].create(
            {
                "move_type": "entry",
                "journal_id": cls.journal.id,
                "date": date,
                "company_id": cls.company.id,
                "line_ids": [
                    Command.create(line_vals),
                    Command.create(
                        {
                            "account_id": cls.account_payable.id,
                            "debit": -amount if amount < 0 else 0.0,
                            "credit": amount if amount > 0 else 0.0,
                            "name": "Contrepartie",
                        }
                    ),
                ],
            }
        )
        if post:
            move.action_post()
        return move

    @classmethod
    def _post_revenue(cls, amount, date=None):
        """Un produit : le compte de produit au crédit."""
        date = date or fields.Date.context_today(cls.env.user)
        move = cls.env["account.move"].create(
            {
                "move_type": "entry",
                "journal_id": cls.journal.id,
                "date": date,
                "company_id": cls.company.id,
                "line_ids": [
                    Command.create(
                        {
                            "account_id": cls.account_revenue.id,
                            "credit": amount,
                            "debit": 0.0,
                            "name": "Produit",
                        }
                    ),
                    Command.create(
                        {
                            "account_id": cls.account_payable.id,
                            "debit": amount,
                            "credit": 0.0,
                            "name": "Contrepartie",
                        }
                    ),
                ],
            }
        )
        move.action_post()
        return move

    @classmethod
    def _internal_cost(cls, analytic, amount, date=None):
        """Une ligne analytique SANS pièce comptable : une feuille de temps.

        C'est exactement ce que le module appelle « coût interne » : un coût réel
        que la comptabilité ne porte pas.
        """
        # ⚠️ `account.analytic.line` n'a pas de champ « compte analytique »
        # unique : une colonne par plan racine. Une base fraîche porte DÉJÀ un
        # plan « Project », donc le plan de ce décor est le deuxième et sa
        # colonne n'est pas `account_id`. Écrire `account_id` en dur passerait
        # ici et casserait chez le premier client à deux plans.
        column = analytic.root_plan_id._column_name()
        return cls.env["account.analytic.line"].create(
            {
                "name": "Temps passé",
                column: analytic.id,
                "amount": -abs(amount),
                "date": date or fields.Date.context_today(cls.env.user),
                "company_id": cls.company.id,
            }
        )
