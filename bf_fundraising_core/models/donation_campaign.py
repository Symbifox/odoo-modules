# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import api, fields, models


class DonationCampaign(models.Model):
    """Extend the OCA donation campaign with a goal, end date and computed
    amount raised so it behaves like the RE « Campaign » (top of the
    Fund → Campaign → Appeal → Package hierarchy)."""

    _inherit = "donation.campaign"

    date_end = fields.Date(string="End date")
    goal_amount = fields.Monetary(string="Goal", currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )
    appeal_ids = fields.One2many("bf.appeal", "campaign_id", string="Appeals")
    appeal_count = fields.Integer(compute="_compute_amounts", string="Appeals")
    donation_ids = fields.One2many("donation.donation", "campaign_id", string="Donations")
    amount_raised = fields.Monetary(
        string="Amount raised",
        compute="_compute_amounts",
        currency_field="currency_id",
    )
    progress = fields.Float(string="Progress (%)", compute="_compute_amounts")

    @api.depends(
        "donation_ids.amount_total", "donation_ids.state", "goal_amount", "appeal_ids"
    )
    def _compute_amounts(self):
        for campaign in self:
            done = campaign.donation_ids.filtered(lambda d: d.state == "done")
            raised = sum(done.mapped("amount_total"))
            campaign.amount_raised = raised
            campaign.appeal_count = len(campaign.appeal_ids)
            campaign.progress = (
                (raised / campaign.goal_amount * 100.0)
                if campaign.goal_amount
                else 0.0
            )
