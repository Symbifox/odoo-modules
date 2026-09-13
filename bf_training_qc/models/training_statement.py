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
    _description = "Relevé de participation au développement des compétences"
    _inherit = ["mail.thread"]
    _order = "year desc, id desc"

    name = fields.Char(string="Relevé", compute="_compute_name", store=True)
    company_id = fields.Many2one(
        "res.company", string="Société", required=True,
        default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    year = fields.Integer(
        string="Année civile", required=True,
        default=lambda self: fields.Date.context_today(self).year)
    state = fields.Selection(
        [("draft", "Brouillon"), ("computed", "Calculé"), ("closed", "Clos")],
        string="État", default="draft", required=True, tracking=True)

    payroll = fields.Monetary(
        string="Masse salariale", currency_field="currency_id", tracking=True,
        help="Telle que calculée pour la déclaration. Le registre ne la devine "
             "pas : sans elle, rien n'est conclu.")
    threshold = fields.Monetary(
        string="Seuil d'assujettissement", currency_field="currency_id",
        compute="_compute_threshold", store=True, readonly=False)
    rate = fields.Float(
        string="Taux (%)", digits=(5, 2), compute="_compute_threshold",
        store=True, readonly=False)
    is_subject = fields.Boolean(
        string="Assujettie", compute="_compute_totaux", store=True)
    minimum_participation = fields.Monetary(
        string="Participation minimale", currency_field="currency_id",
        compute="_compute_totaux", store=True)

    carryover_in = fields.Monetary(
        string="Excédent reporté", currency_field="currency_id",
        help="L'excédent de l'année précédente, qui devient une dépense "
             "admissible de celle-ci.")
    eligible_total = fields.Monetary(
        string="Dépenses admissibles", currency_field="currency_id",
        compute="_compute_totaux", store=True)
    shortfall = fields.Monetary(
        string="Cotisation à verser", currency_field="currency_id",
        compute="_compute_totaux", store=True)
    carryover_out = fields.Monetary(
        string="Excédent à reporter", currency_field="currency_id",
        compute="_compute_totaux", store=True)

    line_ids = fields.One2many(
        "bf.training.statement.line", "statement_id", string="Dépenses comptées")
    excluded_ids = fields.One2many(
        "bf.training.statement.line", "excluded_statement_id",
        string="Écartées, et pourquoi")
    line_count = fields.Integer(string="Comptées", compute="_compute_comptes")
    excluded_count = fields.Integer(string="Écartées", compute="_compute_comptes")
    excluded_hours = fields.Float(
        string="Heures écartées", compute="_compute_comptes", digits=(8, 2))
    note = fields.Html(string="Note", sanitize_attributes=False)

    _sql_constraints = [
        ("annee_par_societe", "unique (company_id, year)",
         "Il y a déjà un relevé pour cette société et cette année."),
        ("annee_plausible", "check (year between 1996 and 2100)",
         "Cette année ne ressemble à rien."),
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
                raise UserError(_("Un relevé clos ne se recalcule pas."))
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
                raise UserError(_("Calculer le relevé avant de le clore."))
            if rec.is_subject and rec.excluded_count:
                rec.message_post(body=_(
                    "Relevé clos avec %(n)s réalisation(s) écartée(s), "
                    "%(h).2f heure(s) qui ne comptent pas.",
                    n=rec.excluded_count, h=rec.excluded_hours))
        self.write({"state": "closed"})

    def action_reset(self):
        self.write({"state": "draft"})

    def action_open_excluded(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Réalisations écartées"),
            "res_model": "bf.training.statement.line",
            "view_mode": "list",
            "domain": [("excluded_statement_id", "=", self.id)],
        }


class BfTrainingStatementLine(models.Model):
    """Une réalisation, vue par le relevé : comptée, ou écartée avec sa raison."""

    _name = "bf.training.statement.line"
    _description = "Ligne de relevé de participation"
    _order = "date_done, id"

    statement_id = fields.Many2one(
        "bf.training.statement", string="Relevé", ondelete="cascade", index=True)
    excluded_statement_id = fields.Many2one(
        "bf.training.statement", string="Relevé (écartée)", ondelete="cascade", index=True)
    record_id = fields.Many2one(
        "bf.training.record", string="Réalisation", ondelete="cascade", required=True)
    employee_id = fields.Many2one("hr.employee", string="Personne")
    activity_id = fields.Many2one("bf.training.activity", string="Activité")
    date_done = fields.Date(string="Faite le")
    hours = fields.Float(string="Heures", digits=(6, 2))
    currency_id = fields.Many2one(
        related="record_id.currency_id", string="Devise")
    amount = fields.Monetary(string="Montant", currency_field="currency_id")
    reason = fields.Char(string="Pourquoi elle ne compte pas")
