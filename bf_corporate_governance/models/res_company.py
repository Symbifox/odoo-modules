from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .corporate_compliance_template import LEGAL_FORMS, MONTHS


class ResCompany(models.Model):
    _inherit = 'res.company'

    # Le profil corporatif que le générateur d'obligations lit et retient.
    #
    # ⚠️ Une fin d'exercice À PART de `fiscalyear_last_month` / `fiscalyear_last_day`
    # (module account) : ce module ne dépend pas de la comptabilité, et la fin
    # d'exercice qui commande les échéances légales n'est pas toujours celle que la
    # comptabilité porte. Sur une base réelle, quatre fins d'exercice divergeaient
    # à la fois : la résolution, le cabinet comptable, `res.company` et le REQ.
    corporate_legal_form = fields.Selection(
        selection=LEGAL_FORMS, string="Legal form")
    corporate_incorporation_date = fields.Date(string="Date of incorporation")
    corporate_fy_end_month = fields.Selection(
        selection=MONTHS, string="Fiscal year ends (month)")
    corporate_fy_end_day = fields.Integer(string="Fiscal year ends (day)")

    @api.constrains('corporate_fy_end_day')
    def _check_corporate_fy_end_day(self):
        for company in self:
            if company.corporate_fy_end_day and not 1 <= company.corporate_fy_end_day <= 31:
                raise ValidationError(self.env._(
                    "The fiscal year end day must be between 1 and 31 (31: last day of the month)."))
