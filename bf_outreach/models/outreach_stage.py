# -*- coding: utf-8 -*-
from odoo import api, fields, models


class BfOutreachStage(models.Model):
    _name = "bf.outreach.stage"
    _description = "Étape de démarchage"
    _order = "sequence, id"

    name = fields.Char(string="Étape", required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    fold = fields.Boolean(
        string="Repliée",
        help="Les étapes repliées apparaissent en colonne compacte dans le kanban.",
    )
    stage_type = fields.Selection(
        [
            ("todo", "À contacter"),
            ("active", "En cours"),
            ("won", "Gagnée"),
            ("lost", "Abandonnée"),
        ],
        string="Type",
        default="active",
        required=True,
        help="« Gagnée » et « Abandonnée » ferment le dossier : la cadence de "
        "relance s'arrête et la cible ne remonte plus dans les retards.",
    )
    campaign_ids = fields.Many2many(
        "bf.outreach.campaign",
        "bf_outreach_stage_campaign_rel",
        "stage_id",
        "campaign_id",
        string="Campagnes",
        help="Laisser vide pour rendre l'étape disponible dans toutes les campagnes.",
    )
    description = fields.Text(string="Description")

    @api.model
    def _first_stage(self, campaign_id=None):
        """Première étape disponible pour une campagne (ou étape commune)."""
        domain = ["|", ("campaign_ids", "=", False), ("campaign_ids", "in", campaign_id)]
        if not campaign_id:
            domain = [("campaign_ids", "=", False)]
        return self.search(domain, order="sequence, id", limit=1)
