from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .bf_budget_position import EXPENSE_TYPES, REVENUE_TYPES

# Champs qu'un budget ouvert ne laisse plus toucher. Le reste (suivi, motif de
# dépassement, abonnés) reste modifiable : geler le plan n'est pas geler la
# conversation autour du plan.
FROZEN_FIELDS = {"date_start", "date_end", "budget_type", "company_id", "line_ids"}


class BfBudget(models.Model):
    """Un exercice budgétaire : des postes, des montants prévus, et des états.

    Le module n'enregistre que le PLAN et les DÉCISIONS. Le réalisé n'est jamais
    stocké : il se recalcule à chaque lecture depuis la comptabilité, qui reste la
    seule vérité du réel.
    """

    _name = "bf.budget"
    _description = "Operating budget"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_start desc, id desc"

    name = fields.Char(string="Name", required=True, tracking=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        tracking=True,
        index=True,
    )
    currency_id = fields.Many2one(related="company_id.currency_id", string="Currency")
    date_start = fields.Date(string="Fiscal year start", required=True, tracking=True)
    date_end = fields.Date(string="Fiscal year end", required=True, tracking=True)
    budget_type = fields.Selection(
        [("expense", "Expenses"), ("revenue", "Revenue")],
        required=True,
        default="expense",
        tracking=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("open", "Open"),
            ("revised", "Revised"),
            ("closed", "Closed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
        help="An open budget can no longer be edited: it is revised.",
    )
    line_ids = fields.One2many(
        "bf.budget.line", "budget_id", string="Lines", copy=True
    )  # copy=True : une révision sans ses lignes ne révise rien
    line_count = fields.Integer(compute="_compute_line_count")

    revision_of_id = fields.Many2one(
        "bf.budget",
        string="Revision of",
        readonly=True,
        ondelete="set null",
        help="The budget this one replaces.",
    )
    revision = fields.Integer(default=0, readonly=True, help="0 for the "
                                                             "original.")
    revision_ids = fields.One2many("bf.budget", "revision_of_id", string="Revisions")

    alert_threshold_pct = fields.Float(
        string="Alert threshold (%)",
        default=10.0,
        help="Variance beyond which a line is flagged. No market practice "
             "sets this figure: it is a choice, not a truth.",
    )
    alert_threshold_amount = fields.Monetary(
        string="Alert floor",
        default=250.0,
        currency_field="currency_id",
        help="Below this amount, a variance is not flagged, even as a "
             "percentage. Keeps a 40% overrun on a $50 budget item from "
             "waking someone up.",
    )

    amount_planned = fields.Monetary(
        compute="_compute_amounts", currency_field="currency_id", string="Planned"
    )
    amount_actual = fields.Monetary(
        compute="_compute_amounts", currency_field="currency_id", string="Actual"
    )
    amount_committed = fields.Monetary(
        compute="_compute_amounts", currency_field="currency_id", string="Committed"
    )
    amount_theoretical = fields.Monetary(
        compute="_compute_amounts", currency_field="currency_id", string="Theoretical"
    )
    amount_variance = fields.Monetary(
        compute="_compute_amounts", currency_field="currency_id", string="Variance"
    )
    alert_line_count = fields.Integer(compute="_compute_amounts", string="Flagged "
                                                                         "lines")

    uncovered_account_ids = fields.Many2many(
        "account.account",
        compute="_compute_coverage",
        string="Uncovered accounts",
        help="Operating accounts of the right type that no budget item of "
             "this budget covers. A budget can look on track simply "
             "because a budget item is missing.",
    )
    duplicated_account_ids = fields.Many2many(
        "account.account",
        compute="_compute_coverage",
        string="Accounts counted twice",
        help="Accounts covered by more than one budget item of this "
             "budget: their entries are counted that many times.",
    )
    coverage_warning = fields.Boolean(compute="_compute_coverage")

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------
    @api.depends("line_ids")
    def _compute_line_count(self):
        for budget in self:
            budget.line_count = len(budget.line_ids)

    @api.depends(
        "line_ids.amount_planned",
        "line_ids.amount_actual",
        "line_ids.amount_committed",
        "line_ids.amount_theoretical",
        "line_ids.is_alert",
    )
    def _compute_amounts(self):
        for budget in self:
            lines = budget.line_ids
            budget.amount_planned = sum(lines.mapped("amount_planned"))
            budget.amount_actual = sum(lines.mapped("amount_actual"))
            budget.amount_committed = sum(lines.mapped("amount_committed"))
            budget.amount_theoretical = sum(lines.mapped("amount_theoretical"))
            budget.amount_variance = budget.amount_planned - budget.amount_committed
            budget.alert_line_count = len(lines.filtered("is_alert"))

    @api.depends("line_ids.position_id", "budget_type", "company_id")
    def _compute_coverage(self):
        Account = self.env["account.account"]
        Position = self.env["bf.budget.position"]
        for budget in self:
            covered = Account.browse()
            seen = set()
            duplicated = set()
            for line in budget.line_ids:
                for account in line.position_id.account_ids:
                    if account.id in seen:
                        duplicated.add(account.id)
                    seen.add(account.id)
                covered |= line.position_id.account_ids
            domain = Position._operating_account_domain(budget.budget_type, budget.company_id)
            operating = Account.search(domain)
            budget.uncovered_account_ids = operating - covered
            budget.duplicated_account_ids = Account.browse(sorted(duplicated))
            budget.coverage_warning = bool(budget.uncovered_account_ids or duplicated)

    # ------------------------------------------------------------------
    # Contraintes
    # ------------------------------------------------------------------
    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for budget in self:
            if budget.date_end < budget.date_start:
                raise ValidationError(
                    _("The fiscal year end cannot come before its start.")
                )

    # ------------------------------------------------------------------
    # Le gel du plan
    # ------------------------------------------------------------------
    def write(self, vals):
        """Un budget ouvert ne se modifie plus.

        Le refus porte sur les champs du PLAN, pas sur le suivi : on doit pouvoir
        commenter, suivre et clore un budget ouvert sans le rouvrir.
        """
        touched = FROZEN_FIELDS & set(vals)
        if touched and "state" not in vals:
            locked = self.filtered(lambda b: b.state in ("open", "revised", "closed"))
            if locked:
                raise UserError(
                    _(
                        "\"%(name)s\" is no longer a draft: its plan can "
                        "no longer be edited. Create a revision from it, "
                        "or reset it to draft.",
                        name=locked[0].display_name,
                    )
                )
        return super().write(vals)

    def unlink(self):
        opened = self.filtered(lambda b: b.state != "draft")
        if opened:
            raise UserError(
                _(
                    "A budget that has been opened cannot be deleted: it "
                    "is cancelled, so that the record remains. Affected: "
                    "%(names)s",
                    names=", ".join(opened.mapped("display_name")),
                )
            )
        return super().unlink()

    # ------------------------------------------------------------------
    # Actions d'état
    # ------------------------------------------------------------------
    def action_open(self):
        for budget in self:
            if budget.state != "draft":
                raise UserError(_("Only a draft budget can be opened."))
            if not budget.line_ids:
                raise UserError(
                    _("\"%(name)s\" has no lines: there is nothing to open.",
                      name=budget.display_name)
                )
            budget.state = "open"
            budget.message_post(body=_("Budget opened."))
        return True

    def action_reset_draft(self):
        for budget in self:
            if budget.state not in ("open", "cancelled"):
                raise UserError(
                    _("Only an open or cancelled budget can be reset to draft.")
                )
            budget.state = "draft"
            budget.message_post(body=_("Budget reset to draft."))
        return True

    def action_close(self):
        for budget in self:
            if budget.state != "open":
                raise UserError(_("Only an open budget can be closed."))
            budget.state = "closed"
            budget.message_post(body=_("Budget closed."))
        return True

    def action_cancel(self):
        for budget in self:
            if budget.state == "revised":
                raise UserError(
                    _("A budget that has already been revised cannot be "
                      "cancelled: its revision is the live one.")
                )
            budget.state = "cancelled"
            budget.message_post(body=_("Budget cancelled."))
        return True

    def action_create_revision(self):
        """Tire une révision : un NOUVEL enregistrement, l'original passe à « révisé ».

        C'est le comportement d'Odoo, et la raison est simple : un budget qu'on
        retouche en place ne mesure plus rien, puisque personne ne sait plus contre
        quoi le réel a été comparé.
        """
        self.ensure_one()
        if self.state not in ("open", "closed"):
            raise UserError(
                _("A revision is drawn from an open or closed budget, not "
                  "from a draft.")
            )
        revision = self.copy(
            {
                "name": _("%(name)s (rev. %(n)s)", name=self.name, n=self.revision + 1),
                "state": "draft",
                "revision_of_id": self.id,
                "revision": self.revision + 1,
            }
        )
        self.state = "revised"
        self.message_post(
            body=_("Revised by \"%(name)s\".", name=revision.display_name)
        )
        revision.message_post(
            body=_("Revision of \"%(name)s\".", name=self.display_name)
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.budget",
            "res_id": revision.id,
            "view_mode": "form",
            "target": "current",
        }

    def copy_data(self, default=None):
        vals_list = super().copy_data(default=default)
        for budget, vals in zip(self, vals_list):
            vals.setdefault("name", _("%(name)s (copy)", name=budget.name))
            vals.setdefault("state", "draft")
        return vals_list

    # ------------------------------------------------------------------
    # Aides
    # ------------------------------------------------------------------
    def action_view_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Budget lines"),
            "res_model": "bf.budget.line",
            "view_mode": "list,pivot,graph,form",
            "domain": [("budget_id", "=", self.id)],
            "context": {"default_budget_id": self.id, "search_default_group_position": 1},
        }

    def action_generate_lines_from_positions(self):
        """Crée une ligne par poste du bon sens qui n'en a pas encore."""
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_("Lines can only be generated on a draft budget."))
        positions = self.env["bf.budget.position"].search(
            [
                ("budget_type", "=", self.budget_type),
                ("company_id", "=", self.company_id.id),
            ]
        )
        existing = self.line_ids.mapped("position_id")
        created = self.env["bf.budget.line"]
        for position in positions - existing:
            created |= self.env["bf.budget.line"].create(
                {"budget_id": self.id, "position_id": position.id}
            )
        if not created:
            raise UserError(
                _("All budget items of this type already have their line "
                  "in this budget.")
            )
        return True

    @api.model
    def _default_fiscal_dates(self, company=None):
        """Les bornes de l'exercice courant de la société."""
        company = company or self.env.company
        today = fields.Date.context_today(self)
        day = company.fiscalyear_last_day or 31
        month = int(company.fiscalyear_last_month or "12")
        end = fields.Date.to_date("%s-%02d-%02d" % (today.year, month, min(day, 28)))
        if company.fiscalyear_last_day:
            # On repart du dernier jour réel du mois de clôture.
            end = (
                fields.Date.to_date("%s-%02d-01" % (today.year, month))
                + relativedelta(day=day)
            )
        if end < today:
            end += relativedelta(years=1)
        start = end + relativedelta(days=1, years=-1)
        return start, end

    # ------------------------------------------------------------------
    # Rapport
    # ------------------------------------------------------------------
    def _report_rows(self, as_of=None):
        """Les lignes du rapport, en aide PURE : aucune dépendance au rendu.

        Sortir la logique du gabarit QWeb la rend testable sans moteur de rendu,
        et évite qu'un rapport dépende d'un champ de marque déclaré dans un autre
        module.

        Le mois ET le cumul : une analyse d'écart lue mois par mois se laisse
        tromper par les décalages de calendrier, qui s'annulent d'une période à
        l'autre.
        """
        self.ensure_one()
        as_of = as_of or fields.Date.context_today(self)
        rows = []
        for line in self.line_ids:
            current = line.period_ids.filtered(
                lambda p, d=as_of: p.date_start <= d <= p.date_end
            )
            rows.append(
                {
                    "line": line,
                    "label": line.name,
                    "source": dict(line._fields["source"]._description_selection(line.env)).get(line.source),
                    "month_label": current[:1].name or "",
                    "month_planned": sum(current.mapped("amount_planned")),
                    "planned": line.amount_planned,
                    "theoretical": line.amount_theoretical,
                    "theoretical_basis": line.theoretical_basis,
                    "actual": line.amount_actual,
                    "committed": line.amount_committed,
                    "variance": line.amount_variance,
                    "drift": line.amount_drift,
                    "drift_pct": line.drift_pct,
                    "alert": line.is_alert,
                    "accepted": line.overrun_accepted,
                }
            )
        return rows

    def _report_totals(self, rows):
        """Les totaux du rapport, calculés sur les mêmes lignes que l'affichage."""
        keys = ("month_planned", "planned", "theoretical", "actual", "committed", "variance", "drift")
        return {key: sum(row[key] for row in rows) for key in keys}
