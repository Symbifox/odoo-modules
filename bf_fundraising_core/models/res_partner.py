# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models


class ResPartner(models.Model):
    """Turn a partner into a full RE-style *constituent*: classification,
    household grouping, solicit codes, giving summary and prospect ratings."""

    _inherit = "res.partner"

    is_constituent = fields.Boolean(
        string="Constituent", help="This record is a constituent (donor, "
                                   "member, prospect) tracked by "
                                   "fundraising."
    )
    constituent_type = fields.Selection(
        [
            ("individual", "Individual"),
            ("household", "Household"),
            ("organization", "Organization"),
            ("foundation", "Foundation"),
            ("other", "Other"),
        ],
        string="Constituent type",
    )
    is_household = fields.Boolean(
        string="Is a household",
        help="Record representing a household that groups several individuals.",
    )
    household_id = fields.Many2one(
        "res.partner",
        string="Household",
        domain="[('is_household', '=', True)]",
        help="Household this constituent belongs to.",
    )
    household_member_ids = fields.One2many(
        "res.partner", "household_id", string="Household members"
    )

    # --- Solicit codes -------------------------------------------------------
    solicit_code_ids = fields.Many2many(
        "bf.solicit.code",
        "res_partner_solicit_code_rel",
        "partner_id",
        "solicit_code_id",
        string="Solicit codes",
    )
    do_not_solicit = fields.Boolean(
        string="Do not solicit",
        compute="_compute_do_not_solicit",
        store=True,
    )

    # --- Giving summary (stored → sortable/filterable, incl. LYBUNT) ---------
    donation_amount_total = fields.Monetary(
        string="Total donations",
        compute="_compute_giving_summary",
        store=True,
        currency_field="fundraising_currency_id",
    )
    donation_largest = fields.Monetary(
        string="Largest donation",
        compute="_compute_giving_summary",
        store=True,
        currency_field="fundraising_currency_id",
    )
    donation_first_date = fields.Date(
        string="First donation", compute="_compute_giving_summary", store=True
    )
    donation_last_date = fields.Date(
        string="Last donation", compute="_compute_giving_summary", store=True
    )
    fundraising_currency_id = fields.Many2one(
        "res.currency",
        string="Currency (fundraising)",
        compute="_compute_fundraising_currency",
    )

    # --- Prospect / major-gift ratings --------------------------------------
    giving_capacity = fields.Monetary(
        string="Estimated giving capacity",
        currency_field="fundraising_currency_id",
    )
    wealth_rating = fields.Selection(
        [
            ("unknown", "Unknown"),
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
            ("major", "Major gift"),
        ],
        string="Wealth rating",
        default="unknown",
    )
    is_major_prospect = fields.Boolean(string="Major gift prospect")

    def _compute_fundraising_currency(self):
        currency = self.env.company.currency_id
        for partner in self:
            partner.fundraising_currency_id = currency

    @api.depends("solicit_code_ids.excludes_all")
    def _compute_do_not_solicit(self):
        for partner in self:
            partner.do_not_solicit = any(
                partner.solicit_code_ids.mapped("excludes_all")
            )

    @api.depends(
        "donation_ids.amount_total",
        "donation_ids.state",
        "donation_ids.donation_date",
    )
    def _compute_giving_summary(self):
        for partner in self:
            done = partner.donation_ids.filtered(lambda d: d.state == "done")
            amounts = done.mapped("amount_total")
            dates = [d for d in done.mapped("donation_date") if d]
            partner.donation_amount_total = sum(amounts)
            partner.donation_largest = max(amounts) if amounts else 0.0
            partner.donation_first_date = min(dates) if dates else False
            partner.donation_last_date = max(dates) if dates else False

    def action_view_constituent_donations(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Donations: %s", self.display_name),
            "res_model": "donation.donation",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }
