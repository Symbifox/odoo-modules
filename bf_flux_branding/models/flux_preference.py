# -*- coding: utf-8 -*-
from odoo import api, models


class FluxPreference(models.Model):
    _inherit = "bf.flux.preference"

    @api.model
    def _flux_mise_en_page(self):
        return "bf_flux_branding.mail_layout_flux"
