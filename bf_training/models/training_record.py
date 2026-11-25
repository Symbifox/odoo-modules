import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .training_activity import MODES

_logger = logging.getLogger(__name__)

ETATS_EXPIRATION = [
    ("permanent", "Never expires"),
    ("valid", "Valid"),
    ("expiring", "Expires soon"),
    ("expired", "Expired"),
]


class BfTrainingRecord(models.Model):
    """La réalisation : ce qui a été suivi, quand, combien de temps, et la preuve.

    C'est la pièce du registre. Trois règles de conception, chacune tirée d'un
    défaut observé dans ce qui existe :

    1. **Une réalisation ne s'écrase pas.** Un renouvellement crée une ligne de
       plus. Trois secourismes en neuf ans laissent trois lignes, et on peut
       montrer la suite.
    2. **La date est la vraie.** Pas la date de saisie, pas celle de l'invitation
       à l'examen : le jour où la formation a été faite.
    3. **Ce qui manque ne vaut pas zéro.** Sans heures ou sans coût horaire, la
       ligne est marquée incomplète et sort du calcul, au lieu d'y entrer pour
       une valeur nulle qui ressemble à une mesure.
    """

    _name = "bf.training.record"
    _description = "Training completion"
    _inherit = ["mail.thread"]
    _order = "date_done desc, id desc"

    employee_id = fields.Many2one(
        "hr.employee", string="Person", required=True, index=True, tracking=True)
    activity_id = fields.Many2one(
        "bf.training.activity", string="Activity", required=True, index=True, tracking=True)
    category_id = fields.Many2one(
        related="activity_id.category_id", string="Category", store=True, index=True)
    company_id = fields.Many2one(
        "res.company", string="Company", required=True,
        default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id", string="Currency")

    date_done = fields.Date(
        string="Done on", required=True, index=True, tracking=True,
        default=fields.Date.context_today,
        help="The day the training was actually completed, not the day it "
             "was entered.")
    hours = fields.Float(string="Hours", digits=(6, 2), tracking=True)
    mode = fields.Selection(MODES, string="Mode", tracking=True)
    state = fields.Selection(
        [("draft", "Draft"),
         ("confirmed", "Confirmed"),
         ("cancelled", "Cancelled")],
        string="State", default="draft", required=True, tracking=True)

    provider_partner_id = fields.Many2one(
        "res.partner", string="Trainer or body", tracking=True)
    certificate_number = fields.Char(string="Certificate number")
    date_issued = fields.Date(string="Issued on")
    date_expiry = fields.Date(
        string="Expires on", compute="_compute_date_expiry", store=True, readonly=False,
        tracking=True,
        help="Taken from the activity's validity, and editable: a "
             "certificate received from outside carries its own date.")
    expiry_state = fields.Selection(
        ETATS_EXPIRATION, string="Validity", default="permanent", index=True, readonly=True,
        help="Written on save and refreshed every day by a scheduled job. "
             "This is not a computed field: a state that depends on "
             "today's date never recomputes on its own.")
    attachment_ids = fields.Many2many(
        "ir.attachment", "bf_training_record_attachment_rel", "record_id", "attachment_id",
        string="Supporting documents")
    attachment_count = fields.Integer(
        string="Documents", compute="_compute_attachment_count")

    content_version = fields.Integer(
        string="Version completed", default=1,
        help="The version of the content at the time the training was "
             "completed.")
    is_outdated = fields.Boolean(
        string="Outdated version", compute="_compute_is_outdated", store=True)

    plan_id = fields.Many2one("bf.training.plan", string="Training plan", tracking=True)
    trainer_employee_id = fields.Many2one("hr.employee", string="Internal "
                                                                "trainer")
    trainer_partner_id = fields.Many2one("res.partner", string="External "
                                                               "trainer")
    support_provided = fields.Boolean(
        string="Support provided",
        help="Support during the learning, or the possibility of "
             "interacting with the organiser.")

    hourly_cost = fields.Monetary(
        string="Hourly cost", currency_field="currency_id",
        help="Frozen when entered: today's rate does not rewrite an "
             "expense from three years ago.")
    salary_cost = fields.Monetary(
        string="Salary", compute="_compute_costs", store=True, currency_field="currency_id")
    payroll_charge_rate = fields.Float(
        string="Payroll costs (%)", digits=(5, 2), default=0.0,
        help="The employer's contributions on this salary.")
    payroll_charges = fields.Monetary(
        string="Payroll costs", compute="_compute_costs", store=True, currency_field="currency_id")
    other_cost = fields.Monetary(string="Other costs", currency_field="currency_id")
    total_cost = fields.Monetary(
        string="Total cost", compute="_compute_costs", store=True, currency_field="currency_id")

    is_complete = fields.Boolean(
        string="Complete", compute="_compute_is_complete", store=True,
        help="An incomplete line stays in the register and drops out of "
             "the totals.")
    missing_info = fields.Char(
        string="What is missing", compute="_compute_is_complete", store=True)
    note = fields.Html(string="Note", sanitize_attributes=False)

    _sql_constraints = [
        ("hours_positive", "check (hours >= 0)", "Hours cannot be negative."),
        ("other_cost_positive", "check (other_cost >= 0)", "A cost cannot "
                                                           "be negative."),
    ]

    # ------------------------------------------------------------------
    # Calculs qui ne dépendent pas de la date du jour
    # ------------------------------------------------------------------

    @api.depends("employee_id.name", "activity_id.name")
    def _compute_display_name(self):
        """« Personne · Activité », jamais « bf.training.…,12 ».

        🔴 Sans ce calcul, le fil d'Ariane, le chatter et chaque pont qui cite
        une réalisation affichent l'identifiant technique : c'est ce que la QA de
        parcours a vu sur tous les écrans qui la mentionnent.
        """
        for rec in self:
            rec.display_name = "%s · %s" % (
                rec.employee_id.name or "?", rec.activity_id.name or "?")

    @api.depends("date_done", "activity_id.validity_months")
    def _compute_date_expiry(self):
        for rec in self:
            mois = rec.activity_id.validity_months
            if rec.date_done and mois:
                rec.date_expiry = rec.date_done + relativedelta(months=mois)
            elif not rec.date_expiry:
                rec.date_expiry = False

    @api.depends("hours", "hourly_cost", "payroll_charge_rate", "other_cost")
    def _compute_costs(self):
        for rec in self:
            salaire = (rec.hours or 0.0) * (rec.hourly_cost or 0.0)
            charges = salaire * (rec.payroll_charge_rate or 0.0) / 100.0
            rec.salary_cost = salaire
            rec.payroll_charges = charges
            rec.total_cost = salaire + charges + (rec.other_cost or 0.0)

    @api.depends("hours", "hourly_cost", "date_done", "activity_id",
                 "activity_id.requires_plan", "plan_id",
                 "activity_id.requires_support", "support_provided")
    def _compute_is_complete(self):
        for rec in self:
            # 🔴 Ces quatre morceaux vivaient hors de tout `_()`, en français :
            # un anglophone lisait « les heures, le coût horaire » dans la
            # colonne « Ce qui manque ». Ils passent par le catalogue.
            #
            # ⚠️ `missing_info` est un calculé STOCKÉ : la valeur garde la langue
            # de qui a déclenché le calcul, et un recalcul par quelqu'un d'une
            # autre langue la réécrit. Rendre la phrase à l'affichage
            # demanderait de stocker des codes et de changer la forme du champ ;
            # ce n'est pas le travail de cette bascule-ci. Le défaut qui restait
            # — du français EN DUR, que personne ne pouvait traduire — est levé.
            manques = []
            if not rec.hours:
                manques.append(_("the hours"))
            if not rec.hourly_cost:
                manques.append(_("the hourly cost"))
            if rec.activity_id.requires_plan and not rec.plan_id:
                manques.append(_("the training plan"))
            if rec.activity_id.requires_support and not rec.support_provided:
                manques.append(_("the support provided"))
            rec.is_complete = not manques
            rec.missing_info = ", ".join(manques) if manques else False

    @api.depends("content_version", "activity_id.content_version",
                 "activity_id.reopen_on_change")
    def _compute_is_outdated(self):
        """Le contenu a-t-il changé depuis ?

        ⚠️ Ce calcul-ci peut être stocké sans danger : il ne dépend d'aucune
        date. C'est la comparaison de deux numéros de version, et elle ne bouge
        que si l'un des deux bouge.
        """
        for rec in self:
            rec.is_outdated = bool(
                rec.activity_id.reopen_on_change
                and rec.content_version < rec.activity_id.content_version)

    def _compute_attachment_count(self):
        for rec in self:
            rec.attachment_count = len(rec.attachment_ids)

    # ------------------------------------------------------------------
    # L'expiration : écrite, jamais calculée-stockée
    # ------------------------------------------------------------------
    def _etat_expiration(self, jour, preavis=90):
        """L'état de validité de cette réalisation au jour donné."""
        self.ensure_one()
        if not self.date_expiry:
            return "permanent"
        if self.date_expiry < jour:
            return "expired"
        if self.date_expiry <= jour + relativedelta(days=preavis):
            return "expiring"
        return "valid"

    def _sync_expiry(self, jour=None):
        """Écrit l'état de validité. Rend le nombre de lignes changées."""
        jour = jour or fields.Date.context_today(self)
        changees = 0
        for rec in self:
            etat = rec._etat_expiration(jour)
            if rec.expiry_state != etat:
                super(BfTrainingRecord, rec).write({"expiry_state": etat})
                changees += 1
        return changees

    @api.model
    def _cron_refresh_expiry(self):
        """La tâche qui fait bouger l'expiration, faute de quoi rien ne la ferait.

        🔴 C'est le coeur du module. Un champ stocké calculé depuis `date_end`
        seule affiche « valide » pour l'éternité, parce qu'un calcul stocké ne se
        rejoue pas parce que le temps a passé. Ici, une tâche planifiée relit
        toutes les lignes qui peuvent avoir changé d'état et les écrit.
        """
        jour = fields.Date.context_today(self)
        candidates = self.search([("date_expiry", "!=", False),
                                  ("state", "!=", "cancelled")])
        changees = candidates._sync_expiry(jour)
        _logger.info("Registre de formation : %s validités mises à jour.", changees)
        return changees

    # ------------------------------------------------------------------
    # Écriture
    # ------------------------------------------------------------------
    @api.onchange("activity_id")
    def _onchange_activity_id(self):
        for rec in self:
            if rec.activity_id:
                rec.mode = rec.activity_id.mode
                if not rec.hours:
                    rec.hours = rec.activity_id.duration_hours
                if not rec.provider_partner_id:
                    rec.provider_partner_id = rec.activity_id.provider_partner_id

    @api.onchange("employee_id")
    def _onchange_employee_id(self):
        for rec in self:
            if rec.employee_id and not rec.hourly_cost:
                rec.hourly_cost = rec.employee_id.sudo().hourly_cost

    @api.constrains("plan_id", "date_done")
    def _check_plan_couvre(self):
        for rec in self:
            if rec.plan_id and rec.date_done and not rec.plan_id.date_couvre(rec.date_done):
                raise ValidationError(_(
                    "Plan \"%(plan)s\" does not cover %(jour)s. A "
                    "duration set out in a plan must be set out in the "
                    "plan for the right period.",
                    plan=rec.plan_id.name, jour=rec.date_done))

    @api.model_create_multi
    def create(self, vals_list):
        for valeurs in vals_list:
            if valeurs.get("employee_id") and not valeurs.get("hourly_cost"):
                employe = self.env["hr.employee"].browse(valeurs["employee_id"]).sudo()
                if employe.hourly_cost:
                    valeurs["hourly_cost"] = employe.hourly_cost
            if valeurs.get("activity_id"):
                activite = self.env["bf.training.activity"].browse(valeurs["activity_id"])
                if not valeurs.get("mode"):
                    valeurs["mode"] = activite.mode
                if not valeurs.get("content_version"):
                    valeurs["content_version"] = activite.content_version
        lignes = super().create(vals_list)
        lignes._sync_expiry()
        lignes._rafraichir_obligations()
        return lignes

    def write(self, vals):
        resultat = super().write(vals)
        if {"date_expiry", "date_done", "activity_id", "state"} & set(vals):
            self._sync_expiry()
        if {"date_done", "activity_id", "state", "employee_id", "hours"} & set(vals):
            self._rafraichir_obligations()
        return resultat

    def _rafraichir_obligations(self):
        """Rejoue les exigences que ces réalisations peuvent avoir changées.

        ⚠️ Restreint aux personnes touchées. Sans cela, saisir une ligne
        recalculerait tout l'effectif de toutes les exigences en heures du parc.
        """
        activites = self.mapped("activity_id")
        employes = self.mapped("employee_id")
        if not activites or not employes:
            return
        exigences = self.env["bf.training.requirement"].search([
            "|", ("activity_id", "in", activites.ids),
            ("requirement_type", "=", "hours")])
        if exigences:
            self.env["bf.training.obligation"]._rafraichir(
                exigences, employes=employes)

    def action_confirm(self):
        self.write({"state": "confirmed"})

    def action_cancel(self):
        self.write({"state": "cancelled"})

    def action_reset(self):
        self.write({"state": "draft"})
