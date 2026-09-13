import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

ETATS = [
    ("pending", "Assignée"),
    ("in_progress", "En cours"),
    ("done", "Faite"),
    ("cancelled", "Annulée"),
]


class BfTrainingAssignment(models.Model):
    """Demander à quelqu'un de suivre une activité, et le relancer.

    L'assignation n'est pas l'obligation : l'obligation est la règle appliquée à
    une personne, l'assignation est le geste qu'on pose pour la remplir. On peut
    assigner sans exigence (une recommandation), et une exigence peut être
    remplie sans qu'on ait jamais assigné quoi que ce soit.

    Ce modèle vient de `bf_security_awareness`, où il ne servait qu'à la
    remédiation après un hameçonnage simulé. Il est repris ici parce que rien
    dans son travail n'était propre à la cybersécurité, et parce qu'il était
    encore à zéro enregistrement partout : le déplacer ne coûtait rien.
    """

    _name = "bf.training.assignment"
    _description = "Assignation de formation"
    _inherit = ["mail.thread"]
    _order = "due_date, id"

    partner_id = fields.Many2one(
        "res.partner", string="Personne", required=True, index=True, tracking=True,
        help="Le contact, parce qu'une formation peut être assignée à quelqu'un "
             "qui n'a pas de fiche d'employé.")
    employee_id = fields.Many2one(
        "hr.employee", string="Employé", index=True, tracking=True)
    activity_id = fields.Many2one(
        "bf.training.activity", string="Activité", required=True, index=True, tracking=True)
    requirement_id = fields.Many2one(
        "bf.training.requirement", string="Exigence", ondelete="set null")
    obligation_id = fields.Many2one(
        "bf.training.obligation", string="Obligation", ondelete="set null", index=True)
    company_id = fields.Many2one(
        "res.company", string="Société", required=True,
        default=lambda self: self.env.company, index=True)

    assigned_date = fields.Datetime(string="Assignée le", default=fields.Datetime.now)
    assigned_by_id = fields.Many2one(
        "res.users", string="Assignée par", default=lambda self: self.env.user)
    due_date = fields.Date(string="Échéance", index=True, tracking=True)
    state = fields.Selection(ETATS, string="État", default="pending", required=True,
                             index=True, tracking=True)
    completion = fields.Integer(
        string="Avancement (%)", default=0,
        help="Rempli par le module qui porte le lecteur, quand il y en a un.")
    record_id = fields.Many2one(
        "bf.training.record", string="Réalisation", ondelete="set null",
        help="La pièce qui a clos l'assignation.")
    reminder_count = fields.Integer(string="Relances", default=0, readonly=True)
    last_reminder_date = fields.Datetime(string="Dernière relance", readonly=True)
    note = fields.Html(string="Note", sanitize_attributes=False)

    _sql_constraints = [
        ("completion_bornee", "check (completion >= 0 and completion <= 100)",
         "Un avancement se dit entre 0 et 100."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for valeurs in vals_list:
            if not valeurs.get("due_date"):
                jours = int(self.env["ir.config_parameter"].sudo().get_param(
                    "bf_training.default_due_days", 30))
                valeurs["due_date"] = fields.Date.context_today(self) + relativedelta(days=jours)
            if valeurs.get("employee_id") and not valeurs.get("partner_id"):
                employe = self.env["hr.employee"].browse(valeurs["employee_id"]).sudo()
                partenaire = employe.work_contact_id or employe.user_id.partner_id
                if partenaire:
                    valeurs["partner_id"] = partenaire.id
        return super().create(vals_list)

    def action_mark_done(self):
        for rec in self:
            rec.state = "done"
            rec.completion = 100

    def action_cancel(self):
        self.write({"state": "cancelled"})

    def _relancer(self):
        """Poste la relance au fil de l'assignation. Rend le nombre de relances."""
        gabarit = self.env.ref(
            "bf_training.mail_template_training_reminder", raise_if_not_found=False)
        envoyees = 0
        for rec in self:
            if gabarit:
                gabarit.send_mail(rec.id, force_send=False)
            else:
                rec.message_post(body=_(
                    "Rappel : l'activité « %(activite)s » est attendue pour le "
                    "%(echeance)s.",
                    activite=rec.activity_id.name,
                    echeance=rec.due_date or "sans échéance"))
            rec.reminder_count += 1
            rec.last_reminder_date = fields.Datetime.now()
            envoyees += 1
        return envoyees

    def action_remind(self):
        return self._relancer()

    @api.model
    def _cron_reminders(self):
        """Relance ce qui approche de l'échéance ou l'a passée, une fois par jour."""
        jour = fields.Date.context_today(self)
        preavis = int(self.env["ir.config_parameter"].sudo().get_param(
            "bf_training.reminder_days", 7))
        limite = jour + relativedelta(days=preavis)
        candidates = self.search([
            ("state", "in", ("pending", "in_progress")),
            ("due_date", "<=", limite),
        ])
        # Une relance par jour au plus : le registre rappelle, il ne harcèle pas.
        a_relancer = candidates.filtered(
            lambda a: not a.last_reminder_date
            or a.last_reminder_date.date() < jour)
        envoyees = a_relancer._relancer()
        _logger.info("Registre de formation : %s relances.", envoyees)
        return envoyees
