from dateutil.relativedelta import relativedelta

from odoo import Command, fields

from odoo.addons.bf_budget.tests.common import BfBudgetCommon


class BfBudgetExpenseCommon(BfBudgetCommon):
    """Reprend le décor comptable du socle et lui ajoute des notes de frais."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.groups_id |= cls.env.ref("hr_expense.group_hr_expense_manager")
        Account = cls.env["account.account"].with_company(cls.company)
        cls.account_tax = Account.create(
            {"name": "Taxes à recouvrer", "code": "118000", "account_type": "asset_current"}
        )
        cls.expense_journal = cls.env["account.journal"].create(
            {
                "name": "Notes de frais",
                "code": "NFBG",
                "type": "purchase",
                "company_id": cls.company.id,
            }
        )
        cls.company.expense_journal_id = cls.expense_journal
        cls.employee_partner = cls.env["res.partner"].create({"name": "Employée budget"})
        cls.employee_partner.with_company(cls.company).property_account_payable_id = (
            cls.account_payable
        )
        cls.employee = cls.env["hr.employee"].create(
            {
                "name": "Employée budget",
                "company_id": cls.company.id,
                "work_contact_id": cls.employee_partner.id,
            }
        )
        # ⚠️ Sans plan comptable de localisation, la société n'a ni pays fiscal
        # ni groupe de taxes : une taxe créée sans eux est refusée par Postgres.
        # Odoo refuse aussi de comptabiliser une taxe d'un autre pays que le pays
        # fiscal de la société : on fixe les deux au même.
        cls.country = cls.env.ref("base.ca")
        cls.company.account_fiscal_country_id = cls.country
        cls.tax_group = cls.env["account.tax.group"].create(
            {"name": "Taxes budget", "company_id": cls.company.id, "country_id": cls.country.id}
        )
        cls.tax_recoverable = cls._make_tax("Taxe récupérable 10 %", cls.account_tax)
        # Répartition sans compte : Odoo laisse la taxe sur le compte de charge.
        cls.tax_non_recoverable = cls._make_tax("Taxe non récupérable 10 %", False)
        cls.today = fields.Date.context_today(cls.env.user)

        # Payée par la société : Odoo exige un moyen de paiement sortant autorisé
        # pour les notes, pris sur un journal de banque.
        cls.bank_journal = cls.env["account.journal"].create(
            {"name": "Banque budget", "code": "BQBG", "type": "bank", "company_id": cls.company.id}
        )
        method = cls.bank_journal.outbound_payment_method_line_ids[:1]
        # Sans compte d'attente, Odoo refuse d'approuver la note (« compte en
        # suspens par défaut »).
        method.payment_account_id = Account.create(
            {"name": "Paiements en suspens", "code": "101900", "account_type": "asset_current"}
        )
        cls.company.company_expense_allowed_payment_method_line_ids = method

        # ⚠️ Une devise inventée : sur une base nue, chercher « USD » pour tester
        # une conversion convertirait parfois une devise vers elle-même.
        cls.foreign_currency = cls.env["res.currency"].create(
            {"name": "XBE", "symbol": "¤", "rounding": 0.01}
        )
        cls.env["res.currency.rate"].create(
            {
                "name": cls.date_start,
                "currency_id": cls.foreign_currency.id,
                "company_id": cls.company.id,
                "rate": 0.7317,
            }
        )

    @classmethod
    def _make_tax(cls, name, account):
        def repartition():
            return [
                Command.create({"repartition_type": "base"}),
                Command.create(
                    {"repartition_type": "tax", "account_id": account.id if account else False}
                ),
            ]

        return cls.env["account.tax"].create(
            {
                "name": name,
                "amount": 10.0,
                "amount_type": "percent",
                "type_tax_use": "purchase",
                "company_id": cls.company.id,
                "country_id": cls.country.id,
                "tax_group_id": cls.tax_group.id,
                "invoice_repartition_line_ids": repartition(),
                "refund_repartition_line_ids": repartition(),
            }
        )

    _amount_counter = 0

    @classmethod
    def _sheet(cls, total=110.0, account=None, tax=None, date=None, analytic=None, stage="approve",
               mode="own_account", currency=None, distribution=None):
        """Une pièce d'une note, menue jusqu'à l'étape voulue.

        ⚠️ Chaque total est rendu unique : Odoo refuse d'approuver une note qu'il
        croit en double (même employé, même montant, même date) et rend alors un
        assistant au lieu d'approuver. Le test passerait sur une pièce restée
        soumise sans que rien ne le dise.
        """
        cls._amount_counter += 1
        vals = {
            "name": "Note budget %d" % cls._amount_counter,
            "employee_id": cls.employee.id,
            "company_id": cls.company.id,
            "currency_id": (currency or cls.currency).id,
            "account_id": (account or cls.account_software).id,
            "total_amount_currency": total,
            "date": date or cls.today,
            "payment_mode": mode,
            "tax_ids": [Command.set(tax.ids if tax else [])],
        }
        if analytic:
            vals["analytic_distribution"] = {str(analytic.id): 100.0}
        if distribution:
            vals["analytic_distribution"] = distribution
        expense = cls.env["hr.expense"].create(vals)
        sheet = cls.env["hr.expense.sheet"].create(
            {
                "name": "Pièce budget %d" % cls._amount_counter,
                "employee_id": cls.employee.id,
                "company_id": cls.company.id,
                "expense_line_ids": [Command.set(expense.ids)],
            }
        )
        if stage in ("submit", "approve", "post"):
            sheet.action_submit_sheet()
        if stage in ("approve", "post"):
            result = sheet.action_approve_expense_sheets()
            assert not result, "approbation détournée vers un assistant : %s" % result
            assert sheet.approval_state == "approve"
        if stage == "post":
            sheet.action_sheet_move_post()
        return sheet

    @classmethod
    def _last_year(cls):
        return cls.date_start - relativedelta(days=15)

    def _line(self, position=None, budget=None, **extra):
        budget = budget or self.env["bf.budget"].create(
            {
                "name": "Exercice notes de frais",
                "company_id": self.company.id,
                "date_start": self.date_start,
                "date_end": self.date_end,
            }
        )
        vals = {
            "budget_id": budget.id,
            "position_id": (position or self.position_software).id,
            "amount_planned": 1200.0,
        }
        vals.update(extra)
        return self.env["bf.budget.line"].create(vals)
