# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_appointment_agenda_project_id = fields.Many2one(
        related="company_id.bf_appointment_agenda_project_id",
        string="Projet de repli des ordres du jour de rendez-vous",
        readonly=False,
    )
