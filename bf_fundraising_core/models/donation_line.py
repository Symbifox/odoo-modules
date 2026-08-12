# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import api, fields, models


class DonationLine(models.Model):
    """Attach each gift line to a Fund. The fund's analytic account drives the
    analytic (GL) distribution, so revenue lands in the right designation."""

    _inherit = "donation.line"

    fund_id = fields.Many2one(
        "bf.fund",
        string="Fonds",
        ondelete="restrict",
        help="Fonds (désignation) auquel ce don est affecté. Détermine la "
        "ventilation analytique de la ligne.",
    )

    @api.depends("product_id", "fund_id")
    def _compute_analytic_distribution(self):
        """Prefer the fund's analytic account; otherwise fall back to the
        product-based distribution computed by the ``donation`` module."""
        with_fund = self.filtered(
            lambda line: line.fund_id and line.fund_id.analytic_account_id
        )
        for line in with_fund:
            line.analytic_distribution = {
                str(line.fund_id.analytic_account_id.id): 100.0
            }
        remaining = self - with_fund
        if remaining:
            super(DonationLine, remaining)._compute_analytic_distribution()
