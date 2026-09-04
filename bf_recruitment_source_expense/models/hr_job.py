# Part of bf_recruitment_source_expense. Voir LICENSE.
from odoo import _, api, fields, models
from odoo.tools.misc import formatLang

from odoo.addons.bf_recruitment_expense.models.hr_expense import (
    EXPENSE_EXCLUDED_STATES,
)


class HrJob(models.Model):
    """Ce que les sites expliquent, et ce qui leur échappe."""

    _inherit = "hr.job"

    attributed_expense_total = fields.Monetary(
        string="Débours imputés à un site", compute="_compute_source_expense_split",
        currency_field="currency_id",
    )
    unattributed_expense_total = fields.Monetary(
        string="Débours sans site", compute="_compute_source_expense_split",
        currency_field="currency_id",
        help="Les débours rattachés au poste qu'aucun site ne porte : un "
             "déplacement, un repas, une annonce payée sans qu'on note où. "
             "Aucun coût par candidature ne les couvre.",
    )

    @api.depends(
        "expense_ids.total_amount", "expense_ids.state",
        "expense_ids.recruitment_source_id",
    )
    def _compute_source_expense_split(self):
        for job in self:
            expenses = self.env["hr.expense"].sudo().search([
                ("job_id", "=", job.id),
                ("state", "not in", EXPENSE_EXCLUDED_STATES),
            ])
            attributed = expenses.filtered("recruitment_source_id")
            job.attributed_expense_total = sum(attributed.mapped("total_amount"))
            job.unattributed_expense_total = sum(
                (expenses - attributed).mapped("total_amount")
            )

    def _source_warning_text(self, total, sourced, sources):
        """La somme non imputée s'écrit à côté des candidatures inexpliquées.

        Les deux écarts sont le même fait vu par ses deux bouts : ce que les
        sites n'expliquent pas de la recette, et ce qu'ils n'expliquent pas de
        la dépense.
        """
        message = super()._source_warning_text(total, sourced, sources)
        self.ensure_one()
        unattributed = self.sudo().unattributed_expense_total
        if unattributed:
            extra = _(
                "%(amount)s de débours ne sont imputés à aucun site : aucun "
                "coût par candidature ne les couvre.",
                # ⚠️ `formatLang`, pas le rendu QWeb monétaire : celui-ci
                # rend du HTML avec des entités, et ce champ est du texte.
                amount=formatLang(
                    self.env, unattributed, currency_obj=self.currency_id,
                ),
            )
            message = "\n".join(filter(None, [message, extra]))
        return message

    def action_view_unattributed_expenses(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Débours sans site"),
            "res_model": "hr.expense",
            "view_mode": "list,form",
            "domain": [
                ("job_id", "=", self.id),
                ("recruitment_source_id", "=", False),
            ],
        }
