# Part of bf_recruitment_expense. Voir LICENSE.
from odoo import api, fields, models

# Une dépense refusée n'est pas un débours. Toutes les autres le sont, y compris
# celles encore à soumettre : l'argent est engagé bien avant d'être approuvé, et
# un coût de recrutement qui n'apparaît qu'au remboursement arrive trop tard
# pour servir à quoi que ce soit.
EXPENSE_EXCLUDED_STATES = ("refused",)


class HrExpense(models.Model):
    _inherit = "hr.expense"

    job_id = fields.Many2one(
        "hr.job", string="Poste à pourvoir",
        index=True, ondelete="set null", check_company=True,
        help="Rattache ce débours au poste qu'il sert à pourvoir. Sert au coût "
             "par embauche, et reporte la clé analytique du poste.",
    )

    @api.depends("product_id", "account_id", "employee_id", "job_id")
    def _compute_analytic_distribution(self):
        """Le poste fournit la clé analytique, quand rien d'autre ne l'a fournie.

        ⚠️ L'ordre compte, et il est celui du coeur : un modèle de distribution
        l'emporte, puis ce qui est déjà écrit sur la dépense, puis le poste. On
        n'écrase jamais une distribution posée à la main.

        🔴 Conséquence assumée : changer le poste d'une dépense qui porte déjà
        une distribution ne la récrit PAS. C'est le prix de ne jamais effacer
        une saisie humaine, et il vaut mieux que l'inverse.
        """
        super()._compute_analytic_distribution()
        for expense in self:
            if expense.analytic_distribution:
                continue
            job_distribution = expense.job_id.analytic_distribution
            if job_distribution:
                expense.analytic_distribution = dict(job_distribution)
