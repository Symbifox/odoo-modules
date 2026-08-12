# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models


class BfFund(models.Model):
    """Fund — the *designation* of a gift (where the money goes). Drives the
    analytic (GL) distribution of donation lines. This is the RE « Fund »."""

    _name = "bf.fund"
    _description = "Fonds de collecte"
    _order = "code, name"
    _check_company_auto = True

    name = fields.Char(string="Nom du fonds", required=True, translate=True)
    code = fields.Char(string="Code")
    active = fields.Boolean(default=True)
    fund_type = fields.Selection(
        [
            ("unrestricted", "Non affecté"),
            ("restricted", "Affecté"),
            ("endowment", "Fonds de dotation"),
        ],
        string="Type de fonds",
        default="unrestricted",
        required=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Société",
        default=lambda self: self.env.company,
    )
    analytic_account_id = fields.Many2one(
        "account.analytic.account",
        string="Compte analytique",
        check_company=True,
        help="Compte analytique vers lequel les dons de ce fonds sont ventilés. "
        "Renseigne automatiquement la distribution analytique des lignes de don.",
    )
    description = fields.Text(string="Description")

    # --- Amounts raised ------------------------------------------------------
    donation_line_ids = fields.One2many(
        "donation.line", "fund_id", string="Lignes de don"
    )
    amount_raised = fields.Monetary(
        string="Montant amassé",
        compute="_compute_amount_raised",
        currency_field="currency_id",
    )
    donation_count = fields.Integer(
        string="Nombre de dons", compute="_compute_amount_raised"
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id", string="Devise", readonly=True
    )

    _sql_constraints = [
        (
            "code_company_uniq",
            "unique(code, company_id)",
            "Un fonds avec ce code existe déjà pour cette société.",
        ),
    ]

    @api.depends("donation_line_ids.amount", "donation_line_ids.donation_id.state")
    def _compute_amount_raised(self):
        for fund in self:
            lines = fund.donation_line_ids.filtered(
                lambda line: line.donation_id.state == "done"
            )
            fund.amount_raised = sum(lines.mapped("amount"))
            fund.donation_count = len(lines.mapped("donation_id"))

    @api.depends("code", "name")
    def _compute_display_name(self):
        for fund in self:
            fund.display_name = f"[{fund.code}] {fund.name}" if fund.code else fund.name

    def action_view_donations(self):
        self.ensure_one()
        donations = self.donation_line_ids.mapped("donation_id")
        return {
            "type": "ir.actions.act_window",
            "name": _("Dons — %s", self.name),
            "res_model": "donation.donation",
            "view_mode": "list,form",
            "domain": [("id", "in", donations.ids)],
        }
