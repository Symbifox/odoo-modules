# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models


class BfAppeal(models.Model):
    """Appeal — a specific solicitation within a campaign (RE « Appeal »),
    e.g. « Envoi postal printemps 2026 »."""

    _name = "bf.appeal"
    _description = "Appeal"
    _order = "sequence, id"

    name = fields.Char(string="Appeal name", required=True, translate=True)
    code = fields.Char(string="Code")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    campaign_id = fields.Many2one(
        "donation.campaign",
        string="Campaign",
        ondelete="cascade",
        required=True,
    )
    company_id = fields.Many2one(related="campaign_id.company_id", store=True)
    date_start = fields.Date(string="Start date", default=fields.Date.context_today)
    date_end = fields.Date(string="End date")
    goal_amount = fields.Monetary(string="Goal", currency_field="currency_id")
    currency_id = fields.Many2one(
        related="company_id.currency_id",
        string="Currency",
        readonly=True,
        default=lambda self: self.env.company.currency_id,
    )
    note = fields.Text(string="Notes")

    package_ids = fields.One2many("bf.package", "appeal_id", string="Packages")
    donation_ids = fields.One2many("donation.donation", "appeal_id", string="Donations")
    amount_raised = fields.Monetary(
        string="Amount raised",
        compute="_compute_amount_raised",
        currency_field="currency_id",
    )
    progress = fields.Float(
        string="Progress (%)", compute="_compute_amount_raised"
    )

    _sql_constraints = [
        ("code_uniq", "unique(code, campaign_id)", "This appeal code "
                                                   "already exists in the "
                                                   "campaign."),
    ]

    @api.depends("donation_ids.amount_total", "donation_ids.state", "goal_amount")
    def _compute_amount_raised(self):
        for appeal in self:
            done = appeal.donation_ids.filtered(lambda d: d.state == "done")
            raised = sum(done.mapped("amount_total"))
            appeal.amount_raised = raised
            appeal.progress = (
                (raised / appeal.goal_amount * 100.0) if appeal.goal_amount else 0.0
            )

    @api.depends("code", "name")
    def _compute_display_name(self):
        for appeal in self:
            appeal.display_name = (
                f"[{appeal.code}] {appeal.name}" if appeal.code else appeal.name
            )


class BfPackage(models.Model):
    """Package — a variant/segment within an appeal (RE « Package »),
    e.g. « Enveloppe A » vs « Courriel »."""

    _name = "bf.package"
    _description = "Appeal package"
    _order = "sequence, id"

    name = fields.Char(string="Package name", required=True, translate=True)
    code = fields.Char(string="Code")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    appeal_id = fields.Many2one(
        "bf.appeal", string="Appeal", ondelete="cascade", required=True
    )
    campaign_id = fields.Many2one(
        related="appeal_id.campaign_id", string="Campaign", store=True
    )
    company_id = fields.Many2one(related="appeal_id.company_id", store=True)
    note = fields.Text(string="Notes")

    donation_ids = fields.One2many("donation.donation", "package_id", string="Donations")
    amount_raised = fields.Monetary(
        string="Amount raised",
        compute="_compute_amount_raised",
        currency_field="currency_id",
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id", string="Currency", readonly=True
    )

    @api.depends("donation_ids.amount_total", "donation_ids.state")
    def _compute_amount_raised(self):
        for package in self:
            done = package.donation_ids.filtered(lambda d: d.state == "done")
            package.amount_raised = sum(done.mapped("amount_total"))

    @api.depends("code", "name")
    def _compute_display_name(self):
        for package in self:
            package.display_name = (
                f"[{package.code}] {package.name}" if package.code else package.name
            )
