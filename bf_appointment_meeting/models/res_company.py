# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    bf_appointment_agenda_project_id = fields.Many2one(
        "project.project",
        string="Projet de repli des ordres du jour de rendez-vous",
        help="Projet où atterrit l'ordre du jour d'un rendez-vous dont le type "
             "n'en désigne aucun. `meeting.agenda` exige un projet : sans ce "
             "repli, un type non configuré ne produit simplement pas d'ordre "
             "du jour.",
    )
