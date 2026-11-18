# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # Plain related fields on the company, with readonly=False: Odoo reads them from
    # company_id and writes them back. A compute without its own default_get would blank
    # the company's value the first time the page is saved.
    bf_retail_margin_target = fields.Float(
        related="company_id.bf_retail_margin_target", readonly=False)
    bf_retail_price_rounding = fields.Selection(
        related="company_id.bf_retail_price_rounding", readonly=False)
    bf_retail_cost_max_age = fields.Integer(
        related="company_id.bf_retail_cost_max_age", readonly=False)
    bf_retail_price_gap_min = fields.Float(
        related="company_id.bf_retail_price_gap_min", readonly=False)
