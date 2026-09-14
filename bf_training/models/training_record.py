import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .training_activity import MODES

_logger = logging.getLogger(__name__)

ETATS_EXPIRATION = [
    ("permanent", "Ne périme pas"),
    ("valid", "Valide"),
    ("expiring", "Expire bientôt"),
    ("expired", "Périmée"),
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
    _description = "Réalisation de formation"
    _inherit = ["mail.thread"]
    _order = "date_done desc, id desc"

    employee_id = fields.Many2one(
        "hr.employee", string="Personne", required=True, index=True, tracking=True)
    activity_id = fields.Many2one(
        "bf.training.activity", string="Activité", required=True, index=True, tracking=True)
    category_id = fields.Many2one(
        related="activity_id.category_id", string="Catégorie", store=True, index=True)
    company_id = fields.Many2one(
        "res.company", string="Société", required=True,
        default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id", string="Devise")

    date_done = fields.Date(
        string="Faite le", required=True, index=True, tracking=True,
        default=fields.Date.context_today,
        help="Le jour où la formation a réellement été suivie, pas celui de la "
             "saisie.")
    hours = fields.Float(string="Heures", digits=(6, 2), tracking=True)
    mode = fields.Selection(MODES, string="Mode", tracking=True)
    state = fields.Selection(
        [("draft", "Brouillon"),
         ("confirmed", "Confirmée"),
         ("cancelled", "Annulée")],
        string="État", default="draft", required=True, tracking=True)

    provider_partner_id = fields.Many2one(
        "res.partner", string="Formateur ou organisme", tracking=True)
    certificate_number = fields.Char(string="Numéro d'attestation")
    date_issued = fields.Date(string="Délivrée le")
    date_expiry = fields.Date(
        string="Expire le", compute="_compute_date_expiry", store=True, readonly=False,
        tracking=True,
        help="Reprise de la validité de l'activité, et modifiable : une "
             "attestation reçue de l'extérieur porte sa propre date.")
    expiry_state = fields.Selection(
        ETATS_EXPIRATION, string="Validité", default="permanent", index=True, readonly=True,
        help="Écrit à l'enregistrement et rafraîchi chaque jour par une tâche "
             "planifiée. Ce n'est pas un champ calculé : un état qui dépend de "
             "la date du jour ne se recalcule jamais tout seul.")
    attachment_ids = fields.Many2many(
        "ir.attachment", "bf_training_record_attachment_rel", "record_id", "attachment_id",
        string="Pièces justificatives")
    attachment_count = fields.Integer(
        string="Pièces", compute="_compute_attachment_count")

    content_version = fields.Integer(
        string="Version suivie", default=1,
        help="La version du contenu au moment où la formation a été suivie.")
    is_outdated = fields.Boolean(
        string="Version dépassée", compute="_compute_is_outdated", store=True)

    plan_id = fields.Many2one("bf.training.plan", string="Plan de formation", tracking=True)
    trainer_employee_id = fields.Many2one("hr.employee", string="Formateur interne")
    trainer_partner_id = fields.Many2one("res.partner", string="Formateur externe")
    support_provided = fields.Boolean(
        string="Accompagnement offert",
        help="Un accompagnement pendant l'apprentissage, ou une interaction "
             "possible avec l'organisateur.")

    hourly_cost = fields.Monetary(
        string="Coût horaire", currency_field="currency_id",
        help="Figé au moment de la saisie : le taux d'aujourd'hui ne réécrit pas "
             "une dépense d'il y a trois ans.")
    salary_cost = fields.Monetary(
        string="Salaire", compute="_compute_costs", store=True, currency_field="currency_id")
    payroll_charge_rate = fields.Float(
        string="Charges (%)", digits=(5, 2), default=0.0,
        help="Les cotisations de l'employeur sur ce salaire.")
    payroll_charges = fields.Monetary(
        string="Charges", compute="_compute_costs", store=True, currency_field="currency_id")
    other_cost = fields.Monetary(string="Autres frais", currency_field="currency_id")
    total_cost = fields.Monetary(
        string="Coût total", compute="_compute_costs", store=True, currency_field="currency_id")

    is_complete = fields.Boolean(
        string="Complète", compute="_compute_is_complete", store=True,
        help="Une ligne incomplète reste au registre et sort des totaux.")
    missing_info = fields.Char(
        string="Ce qui manque", compute="_compute_is_complete", store=True)
    note = fields.Html(string="Note", sanitize_attributes=False)

    _sql_constraints = [
        ("hours_positive", "check (hours >= 0)", "Des heures ne peuvent pas être négatives."),
        ("other_cost_positive", "check (other_cost >= 0)", "Un frais ne peut pas être négatif."),
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
            manques = []
            if not rec.hours:
                manques.append("les heures")
            if not rec.hourly_cost:
                manques.append("le coût horaire")
            if rec.activity_id.requires_plan and not rec.plan_id:
                manques.append("le plan de formation")
            if rec.activity_id.requires_support and not rec.support_provided:
                manques.append("l'accompagnement")
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
                    "Le plan « %(plan)s » ne couvre pas le %(jour)s. Une durée "
                    "établie dans un plan doit l'être dans le plan de la bonne "
                    "période.",
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
