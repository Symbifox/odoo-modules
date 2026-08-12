# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import api, fields, models


class DonationDonation(models.Model):
    """Attribute each gift to the Appeal / Package it came from (Campaign is
    already provided by the ``donation`` module as ``campaign_id``)."""

    _inherit = "donation.donation"

    appeal_id = fields.Many2one(
        "bf.appeal",
        string="Sollicitation",
        tracking=True,
        ondelete="restrict",
        help="Sollicitation à l'origine du don. La choisir renseigne "
        "automatiquement la campagne.",
    )
    package_id = fields.Many2one(
        "bf.package",
        string="Trousse",
        tracking=True,
        ondelete="restrict",
        domain="[('appeal_id', '=', appeal_id)]",
    )

    @api.onchange("appeal_id")
    def _onchange_appeal_id(self):
        """Keep the hierarchy coherent: selecting an appeal fills in its
        campaign, and clears a package that no longer belongs to the appeal."""
        for donation in self:
            if donation.appeal_id:
                if donation.appeal_id.campaign_id:
                    donation.campaign_id = donation.appeal_id.campaign_id
                if (
                    donation.package_id
                    and donation.package_id.appeal_id != donation.appeal_id
                ):
                    donation.package_id = False

    @api.onchange("package_id")
    def _onchange_package_id(self):
        for donation in self:
            if donation.package_id and donation.package_id.appeal_id:
                donation.appeal_id = donation.package_id.appeal_id
