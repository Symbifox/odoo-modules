from odoo import fields, models


class HouseholdChild(models.Model):
    _inherit = "bf.household.child"

    celebrate_birthday = fields.Boolean(
        string="Celebrate their birthday", default=True,
        help="Puts the birthday in Celebrations and the calendar, when the birth day is known.")
