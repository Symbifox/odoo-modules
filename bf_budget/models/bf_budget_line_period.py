from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BfBudgetLinePeriod(models.Model):
    """La répartition mensuelle d'une ligne budgétaire.

    Elle existe pour une seule raison : une dépense annuelle ne se lisse pas sur
    douze mois. Un renouvellement de domaines qui tombe en mars doit être prévu en
    mars, sinon le théorique déclare un dépassement en mars et un sous-emploi les
    onze autres mois, et l'alerte devient du bruit.
    """

    _name = "bf.budget.line.period"
    _description = "Répartition mensuelle d'une ligne budgétaire"
    _order = "line_id, sequence, date_start"

    line_id = fields.Many2one(
        "bf.budget.line", required=True, ondelete="cascade", index=True
    )
    budget_id = fields.Many2one(related="line_id.budget_id", store=True, index=True)
    currency_id = fields.Many2one(related="line_id.currency_id")
    sequence = fields.Integer(default=0)
    date_start = fields.Date(required=True)
    date_end = fields.Date(required=True)
    amount_planned = fields.Monetary(string="Prévu", currency_field="currency_id")
    name = fields.Char(compute="_compute_name", store=True)

    @api.depends("date_start")
    def _compute_name(self):
        for period in self:
            period.name = period.date_start.strftime("%Y-%m") if period.date_start else ""

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for period in self:
            if period.date_end < period.date_start:
                raise ValidationError(
                    _("Une période ne peut pas finir avant d'avoir commencé.")
                )
