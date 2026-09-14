"""Ce que le calendrier ajoute à une proposition d'absence."""

from odoo import fields, models


class BfPartnerAbsenceSuggestion(models.Model):
    _inherit = "bf.partner.absence.suggestion"

    calendar_uid = fields.Char(
        string="Calendar entry",
        index=True,
        copy=False,
        help="The identifier of the original event. It is what keeps the "
             "same entry from being proposed twice on each read.")
    calendar_label = fields.Char(
        string="Calendar title",
        readonly=True,
        help="The title as written in the calendar, so the match can be "
             "judged.")
