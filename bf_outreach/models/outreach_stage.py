# -*- coding: utf-8 -*-
from odoo import api, fields, models


class BfOutreachStage(models.Model):
    _name = "bf.outreach.stage"
    _description = "Outreach stage"
    _order = "sequence, id"

    name = fields.Char(string="Stage", required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    fold = fields.Boolean(
        string="Folded",
        help="Folded stages show as a compact column in the kanban.",
    )
    stage_type = fields.Selection(
        [
            ("todo", "To contact"),
            ("active", "Running"),
            ("won", "Won"),
            ("lost", "Dropped"),
        ],
        string="Type",
        default="active",
        required=True,
        help="\"Won\" and \"Dropped\" close the file: the follow-up "
             "cadence stops and the target no longer shows up among the "
             "overdue ones.",
    )
    campaign_ids = fields.Many2many(
        "bf.outreach.campaign",
        "bf_outreach_stage_campaign_rel",
        "stage_id",
        "campaign_id",
        string="Campaigns",
        help="Leave empty to make the stage available in every campaign.",
    )
    description = fields.Text(string="Description", translate=True)

    @api.model
    def _first_stage(self, campaign_id=None):
        """Première étape disponible pour une campagne (ou étape commune)."""
        domain = ["|", ("campaign_ids", "=", False), ("campaign_ids", "in", campaign_id)]
        if not campaign_id:
            domain = [("campaign_ids", "=", False)]
        return self.search(domain, order="sequence, id", limit=1)
