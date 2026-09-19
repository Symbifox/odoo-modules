from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Les récurrences qu'une convention emploie réellement. Pas de moteur générique :
# une obligation qui ne rentre dans aucune de ces cases se saisit à la main, ce
# qui est honnête, alors qu'un moteur mal réglé produit des échéances fausses
# que personne ne vérifie.
RECURRENCES = [
    ("none", "Ponctuelle"),
    ("monthly", "Mensuelle"),
    ("quarterly", "Trimestrielle"),
    ("semiannual", "Semestrielle"),
    ("annual", "Annuelle"),
]

DELTAS = {
    "monthly": relativedelta(months=1),
    "quarterly": relativedelta(months=3),
    "semiannual": relativedelta(months=6),
    "annual": relativedelta(years=1),
}


class Obligation(models.Model):
    """Ce que la convention impose à l'employeur, et quand.

    🔴 Une obligation dont personne n'est averti est une obligation manquée. Le
    modèle ne sert donc pas à consigner après coup : il pose une activité
    `mail.activity` un nombre de jours avant l'échéance, sur la personne
    responsable. Sans ce rappel, il ne serait qu'une liste qu'on relit le jour
    où le syndicat la ressort.
    """

    _name = "bf.labour.obligation"
    _description = "Obligation de l'employeur"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_due, id"

    name = fields.Char(string="Obligation", required=True, tracking=True)
    unit_id = fields.Many2one(
        "bf.labour.unit", string="Unité de négociation", required=True,
        ondelete="cascade", index=True, tracking=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="unit_id.company_id",
        store=True, readonly=True, index=True,
    )
    agreement_id = fields.Many2one(
        "bf.labour.agreement", string="Convention",
        domain="[('unit_id', '=', unit_id)]", ondelete="set null",
    )
    article_id = fields.Many2one(
        "bf.labour.agreement.article", string="Article",
        domain="[('agreement_id', '=', agreement_id)]", ondelete="set null",
    )
    description = fields.Text(string="Ce qu'il faut faire")
    responsible_id = fields.Many2one(
        "res.users", string="Responsable", tracking=True,
        default=lambda self: self.env.user,
        help="La personne sur qui l'activité de rappel est posée.",
    )

    date_due = fields.Date(string="Échéance", required=True, tracking=True)
    recurrence = fields.Selection(
        RECURRENCES, string="Récurrence", default="none", required=True,
    )
    reminder_days = fields.Integer(
        string="Rappel (jours avant)", default=14,
        help="Zéro veut dire aucun rappel, pas un rappel le jour même.",
    )
    state = fields.Selection(
        [
            ("pending", "À faire"),
            ("done", "Faite"),
            ("waived", "Sans objet"),
        ],
        string="État", default="pending", required=True, tracking=True,
    )
    date_done = fields.Date(string="Faite le", readonly=True, copy=False)
    note = fields.Text(string="Note")
    reminder_posted = fields.Boolean(
        string="Rappel posé", default=False, copy=False, readonly=True,
        help="Empêche le traitement planifié de reposer la même activité à "
             "chaque passage.",
    )

    # ⚠️ NON stockés : ils se jugent contre aujourd'hui.
    is_late = fields.Boolean(string="En retard", compute="_compute_lateness")
    days_to_due = fields.Integer(
        string="Jours avant l'échéance", compute="_compute_lateness",
    )

    @api.depends("date_due", "state")
    def _compute_lateness(self):
        today = fields.Date.context_today(self)
        for obligation in self:
            if obligation.state != "pending" or not obligation.date_due:
                obligation.is_late = False
                obligation.days_to_due = 0
                continue
            delta = (obligation.date_due - today).days
            obligation.days_to_due = delta
            obligation.is_late = delta < 0

    @api.constrains("reminder_days")
    def _check_reminder_days(self):
        for obligation in self:
            if obligation.reminder_days < 0:
                raise ValidationError(_(
                    "Un rappel ne se pose pas après l'échéance."
                ))

    def action_done(self):
        """Marquer faite, et reconduire si l'obligation revient.

        La reconduction crée le prochain enregistrement plutôt que de déplacer
        celui-ci : ce qui a été fait reste lisible avec sa date, sinon
        l'historique de conformité disparaît au premier passage.
        """
        followers = self.env["bf.labour.obligation"]
        for obligation in self:
            obligation.write({
                "state": "done",
                "date_done": fields.Date.context_today(obligation),
            })
            delta = DELTAS.get(obligation.recurrence)
            if delta:
                followers |= obligation.copy({
                    "date_due": obligation.date_due + delta,
                    "state": "pending",
                    "date_done": False,
                    "reminder_posted": False,
                })
        return followers

    def action_waive(self):
        self.write({"state": "waived"})
        return True

    @api.model
    def _cron_post_reminders(self):
        """Poser l'activité de rappel sur les obligations qui arrivent.

        ⚠️ Le filtre porte sur `reminder_posted`, pas sur l'existence d'une
        activité : une activité que quelqu'un a annulée à la main ne doit pas
        revenir à chaque passage du traitement.
        """
        today = fields.Date.context_today(self)
        candidates = self.search([
            ("state", "=", "pending"),
            ("reminder_posted", "=", False),
            ("reminder_days", ">", 0),
        ])
        posted = self.browse()
        for obligation in candidates:
            if not obligation.date_due:
                continue
            trigger = obligation.date_due - relativedelta(days=obligation.reminder_days)
            if trigger > today:
                continue
            obligation.activity_schedule(
                "mail.mail_activity_data_todo",
                date_deadline=obligation.date_due,
                summary=obligation.name,
                note=obligation.description or "",
                user_id=(obligation.responsible_id or self.env.user).id,
            )
            obligation.reminder_posted = True
            posted |= obligation
        return posted
