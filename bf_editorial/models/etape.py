# -*- coding: utf-8 -*-
"""Les étapes de production d'une entrée."""

from odoo import fields, models


class EditorialStage(models.Model):
    _name = "bf.editorial.stage"
    _description = "Editorial stage"
    _order = "sequence, id"

    name = fields.Char(string="Name", required=True, translate=True)
    sequence = fields.Integer(string="Sequence", default=10)
    fold = fields.Boolean(
        string="Folded",
        help="Folded in the kanban view as long as no entry is in it.",
    )
    is_closing = fields.Boolean(
        string="Closing stage",
        help="An entry in this stage is considered published and counts "
             "in the cadence.",
    )
    is_abandoned = fields.Boolean(
        string="Abandoned stage",
        help="An entry in this stage drops out of the statistics without "
             "counting as a publication.",
    )
    description = fields.Text(string="Description", translate=True)
    calendar_ids = fields.Many2many(
        "bf.editorial.calendar", string="Calendars",
        help="When empty, the stage is offered to all calendars.",
    )
