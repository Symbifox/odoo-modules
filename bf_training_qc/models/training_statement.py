import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

#: Le taux de participation, et le seuil de masse salariale qui assujettit.
#: Tous deux sont des paramètres, parce qu'ils bougent par règlement et qu'un
#: chiffre en dur dans le code se périme sans prévenir.
PARAM_TAUX = "bf_training_qc.participation_rate"
PARAM_SEUIL = "bf_training_qc.payroll_threshold"


class BfTrainingStatement(models.Model):
    """Le relevé de la participation au développement des compétences.

    ⚠️ **Ce relevé n'est pas la déclaration.** Il l'appuie. Le document sorti le
    dit en toutes lettres, et le chiffre reste à valider par qui produit la
    déclaration annuelle.
    """

    _name = "bf.training.statement"
    _description = "Skills development participation statement"
    _inherit = ["mail.thread"]
    _order = "year desc, id desc"

    name = fields.Char(string="Statement", compute="_compute_name", store=True)
    company_id = fields.Many2one(
        "res.company", string="Company", required=True,
        default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    year = fields.Integer(
        string="Calendar year", required=True,
        default=lambda self: fields.Date.context_today(self).year)
    state = fields.Selection(
        [("draft", "Draft"), ("computed", "Computed"), ("closed", "Closed")],
        string="State", default="draft", required=True, tracking=True)

    payroll = fields.Monetary(
        string="Payroll", currency_field="currency_id", tracking=True,
        help="As computed for the return. The register does not guess it: "
             "without it, nothing is concluded.")
    threshold = fields.Monetary(
        string="Liability threshold", currency_field="currency_id",
        compute="_compute_threshold", store=True, readonly=False)
    rate = fields.Float(
        string="Rate (%)", digits=(5, 2), compute="_compute_threshold",
        store=True, readonly=False)
    is_subject = fields.Boolean(
        string="Liable", compute="_compute_totaux", store=True)
    minimum_participation = fields.Monetary(
        string="Minimum participation", currency_field="currency_id",
        compute="_compute_totaux", store=True)

    carryover_in = fields.Monetary(
        string="Surplus carried forward", currency_field="currency_id",
        help="Last year's surplus, which becomes an eligible expense of "
             "this one.")
    eligible_total = fields.Monetary(
        string="Eligible expenses", currency_field="currency_id",
        compute="_compute_totaux", store=True)
    shortfall = fields.Monetary(
        string="Contribution payable", currency_field="currency_id",
        compute="_compute_totaux", store=True)
    carryover_out = fields.Monetary(
        string="Surplus to carry forward", currency_field="currency_id",
        compute="_compute_totaux", store=True)

    line_ids = fields.One2many(
        "bf.training.statement.line", "statement_id", string="Expenses counted")
    excluded_ids = fields.One2many(
        "bf.training.statement.line", "excluded_statement_id",
        string="Excluded, and why")
    line_count = fields.Integer(string="Counted", compute="_compute_comptes")
    excluded_count = fields.Integer(string="Excluded", compute="_compute_comptes")
    excluded_hours = fields.Float(
        string="Hours excluded", compute="_compute_comptes", digits=(8, 2))
    note = fields.Html(string="Note", sanitize_attributes=False)

    _sql_constraints = [
        ("annee_par_societe", "unique (company_id, year)",
         "There is already a statement for this company and this year."),
        ("annee_plausible", "check (year between 1996 and 2100)",
         "That year makes no sense."),
    ]

    @api.depends("year", "company_id")
    def _compute_name(self):
        for rec in self:
            rec.name = _("Participation %(annee)s, %(societe)s",
                         annee=rec.year, societe=rec.company_id.name or "?")

    @api.depends("company_id")
    def _compute_threshold(self):
        IConf = self.env["ir.config_parameter"].sudo()
        seuil = float(IConf.get_param(PARAM_SEUIL, "2000000") or 0)
        taux = float(IConf.get_param(PARAM_TAUX, "1") or 0)
        for rec in self:
            if not rec.threshold:
                rec.threshold = seuil
            if not rec.rate:
                rec.rate = taux

    @api.depends("payroll", "threshold", "rate", "carryover_in",
                 "line_ids.amount")
    def _compute_totaux(self):
        for rec in self:
            rec.is_subject = bool(rec.payroll and rec.payroll > rec.threshold)
            rec.minimum_participation = (rec.payroll or 0.0) * (rec.rate or 0.0) / 100.0
            admissible = sum(rec.line_ids.mapped("amount")) + (rec.carryover_in or 0.0)
            rec.eligible_total = admissible
            if not rec.is_subject:
                rec.shortfall = 0.0
                rec.carryover_out = 0.0
                continue
            ecart = rec.minimum_participation - admissible
            rec.shortfall = max(0.0, ecart)
            rec.carryover_out = max(0.0, -ecart)

    def _compute_comptes(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)
            rec.excluded_count = len(rec.excluded_ids)
            rec.excluded_hours = sum(rec.excluded_ids.mapped("hours"))

    # ------------------------------------------------------------------
    def action_compute(self):
        """Relire le registre et refaire les deux listes.

        ⚠️ Les réalisations qui ne comptent pas ne sont pas ignorées : elles
        passent dans la liste des écartées, avec la raison. Un relevé qui avale
        les trous rend un chiffre faux qui a l'air juste.
        """
        Ligne = self.env["bf.training.statement.line"]
        for rec in self:
            if rec.state == "closed":
                raise UserError(_("A closed statement is not recomputed."))
            (rec.line_ids | rec.excluded_ids).unlink()
            debut = fields.Date.to_date("%s-01-01" % rec.year)
            fin = fields.Date.to_date("%s-12-31" % rec.year)
            realisations = self.env["bf.training.record"].search([
                ("company_id", "=", rec.company_id.id),
                ("date_done", ">=", debut),
                ("date_done", "<=", fin),
                ("state", "!=", "cancelled"),
            ])
            valeurs = []
            for realisation in realisations:
                commune = {
                    "record_id": realisation.id,
                    "employee_id": realisation.employee_id.id,
                    "activity_id": realisation.activity_id.id,
                    "date_done": realisation.date_done,
                    "hours": realisation.hours,
                    "amount": realisation.total_cost,
                }
                if realisation.qc_countable:
                    valeurs.append(dict(commune, statement_id=rec.id))
                else:
                    valeurs.append(dict(
                        commune, excluded_statement_id=rec.id, amount=0.0,
                        reason=realisation.qc_excluded_reason))
            if valeurs:
                Ligne.create(valeurs)
            rec.state = "computed"
        return True

    def action_close(self):
        for rec in self:
            if rec.state == "draft":
                raise UserError(_("Compute the statement before closing it."))
            if rec.is_subject and rec.excluded_count:
                rec.message_post(body=_(
                    "Statement closed with %(n)s completion(s) excluded, "
                    "%(h).2f hour(s) that do not count.",
                    n=rec.excluded_count, h=rec.excluded_hours))
        self.write({"state": "closed"})

    def action_reset(self):
        self.write({"state": "draft"})

    def action_open_excluded(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Completions excluded"),
            "res_model": "bf.training.statement.line",
            "view_mode": "list",
            "domain": [("excluded_statement_id", "=", self.id)],
        }


class BfTrainingStatementLine(models.Model):
    """Une réalisation, vue par le relevé : comptée, ou écartée avec sa raison."""

    _name = "bf.training.statement.line"
    _description = "Participation statement line"
    _order = "date_done, id"

    statement_id = fields.Many2one(
        "bf.training.statement", string="Statement", ondelete="cascade", index=True)
    excluded_statement_id = fields.Many2one(
        "bf.training.statement", string="Statement (excluded)", ondelete="cascade", index=True)
    record_id = fields.Many2one(
        "bf.training.record", string="Completion", ondelete="cascade", required=True)
    employee_id = fields.Many2one("hr.employee", string="Person")
    activity_id = fields.Many2one("bf.training.activity", string="Activity")
    date_done = fields.Date(string="Done on")
    hours = fields.Float(string="Hours", digits=(6, 2))
    currency_id = fields.Many2one(
        related="record_id.currency_id", string="Currency")
    amount = fields.Monetary(string="Amount", currency_field="currency_id")
    reason = fields.Char(string="Why it does not count")
