# -*- coding: utf-8 -*-
from odoo import fields, models


class RetailWeekday(models.Model):
    """The days of the week, as records: a supplier delivers on Tuesday and Friday, and that
    is read by a computation, not by a person. Seven records, seeded, never created."""
    _name = "bf.retail.weekday"
    _description = "Day of the week"
    _order = "code"

    name = fields.Char("Day", required=True, translate=True)
    # Monday is 0, as Python's `date.weekday` counts.
    code = fields.Integer("Number", required=True)

    _sql_constraints = [("code_unique", "UNIQUE (code)", "That day is already there.")]
