# -*- coding: utf-8 -*-
"""Les étapes de production d'une entrée."""

from odoo import fields, models


class EditorialStage(models.Model):
    _name = "bf.editorial.stage"
    _description = "Étape éditoriale"
    _order = "sequence, id"

    name = fields.Char(string="Nom", required=True, translate=True)
    sequence = fields.Integer(string="Séquence", default=10)
    fold = fields.Boolean(
        string="Repliée",
        help="Repliée dans la vue kanban tant qu'aucune entrée ne s'y trouve.",
    )
    is_closing = fields.Boolean(
        string="Étape de sortie",
        help="Une entrée dans cette étape est considérée publiée et compte"
             " dans la cadence.",
    )
    is_abandoned = fields.Boolean(
        string="Abandon",
        help="Une entrée dans cette étape sort des statistiques sans compter"
             " comme une publication.",
    )
    description = fields.Text(string="Description", translate=True)
    calendar_ids = fields.Many2many(
        "bf.editorial.calendar", string="Calendriers",
        help="Vide, l'étape est offerte à tous les calendriers.",
    )
