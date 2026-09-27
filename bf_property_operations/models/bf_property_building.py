"""L'immeuble sait quelle équipe en répond.

C'est ce seul champ qui rend l'acheminement possible : sans lui, une demande
d'occupant se distribue à la main, billet par billet, ce qui suffit à un
syndicat de douze portes et ne suffit pas à un gestionnaire qui a des
concierges par quart.
"""
from odoo import fields, models


class BfPropertyBuilding(models.Model):
    _inherit = "bf.property.building"

    bf_maintenance_team_id = fields.Many2one(
        "maintenance.team",
        string="Équipe d'entretien",
        tracking=True,
        help="L'équipe qui répond de cet immeuble. Une demande ouverte ici lui "
             "est acheminée, et les équipements de l'immeuble lui reviennent "
             "par défaut.",
    )
