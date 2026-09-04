# Part of bf_recruitment_source_expense. Voir LICENSE.
from odoo import _, api, fields, models

from odoo.addons.bf_recruitment_expense.models.hr_expense import (
    EXPENSE_EXCLUDED_STATES,
)


class HrRecruitmentSource(models.Model):
    """Ce que ce site a coûté, et ce qu'il a rapporté pour ce prix."""

    _inherit = "hr.recruitment.source"

    currency_id = fields.Many2one(
        related="job_id.company_id.currency_id", string="Devise", readonly=True,
    )
    expense_ids = fields.One2many(
        "hr.expense", "recruitment_source_id", string="Débours d'affichage",
    )
    expense_total = fields.Monetary(
        string="Débours", compute="_compute_source_cost",
        currency_field="currency_id",
        help="Les débours imputés à CE site, les refusés exclus. Les débours "
             "du poste qu'aucun site ne porte ne sont pas ici : le poste les "
             "compte à part.",
    )
    cost_per_applicant = fields.Monetary(
        string="Coût par candidature", compute="_compute_source_cost",
        currency_field="currency_id",
        help="Débours de ce site divisés par les candidatures qu'il a "
             "rapportées. Sans candidature, il n'y a pas de coût par "
             "candidature : le champ reste à zéro.",
    )
    cost_per_hire_from_source = fields.Monetary(
        string="Coût par embauche (ce site)", compute="_compute_source_cost",
        currency_field="currency_id",
        help="⚠️ Les seuls débours de ce site. Le coût par embauche du POSTE, "
             "lui, compte aussi le temps du panel et les débours sans site.",
    )

    @api.depends(
        "expense_ids.total_amount", "expense_ids.state",
        "applicant_count", "hired_count",
    )
    def _compute_source_cost(self):
        for source in self:
            # ⚠️ `sudo` comme dans `bf_recruitment_expense` : les notes de frais d'autrui ne sont
            # pas lisibles par un recruteur, et sans lui le total serait
            # partiel et silencieux.
            expenses = self.env["hr.expense"].sudo().search([
                ("recruitment_source_id", "=", source.id),
                ("state", "not in", EXPENSE_EXCLUDED_STATES),
            ])
            total = sum(expenses.mapped("total_amount"))
            source.expense_total = total
            source.cost_per_applicant = (
                total / source.applicant_count if source.applicant_count else 0.0
            )
            source.cost_per_hire_from_source = (
                total / source.hired_count if source.hired_count else 0.0
            )

    def _stat_warning_messages(self):
        """Un site payé qui n'a rien rapporté est le seul chiffre qui compte.

        ⚠️ On étend la liste de `bf_recruitment_source` plutôt que de la remplacer : les
        avertissements du lien tracé restent, celui-ci s'ajoute.
        """
        messages = super()._stat_warning_messages()
        self.ensure_one()
        if self.expense_total and not self.applicant_count:
            messages.append(_(
                "Ce site a coûté quelque chose et n'a rapporté aucune "
                "candidature. Il n'y a pas de coût par candidature, et zéro "
                "n'en serait pas un."
            ))
        return messages
