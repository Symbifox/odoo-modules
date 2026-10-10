from markupsafe import Markup

from odoo import api, fields, models
from dateutil.relativedelta import relativedelta
from .gen_portees import LIBELLE_SANTE, PORTEE_SANTE
from .parent_guard import au_nom_du_proprietaire
from .health_dependent import mention_de_la_personne


FREQUENCY_MONTHS = {
    "quarterly": 3,
    "biannually": 6,
    "yearly": 12,
    "two_years": 24,
    "five_years": 60,
}


class HealthScreening(models.Model):
    _name = "health.screening"
    # Verrou de Gen (voir models/gen_portees.py).
    _gen_scope = PORTEE_SANTE
    _gen_scope_label = LIBELLE_SANTE
    _description = "Examen de santé"
    _inherit = ["bf.health.note.only", "mail.thread", "mail.activity.mixin", "bf.health.dependent.mixin"]
    _order = "next_due asc, name asc"

    name = fields.Char(string="Nom de l'examen", required=True)
    screening_type = fields.Selection(
        [
            ("physical", "Examen physique"),
            ("dental", "Dentaire"),
            ("vision", "Vision"),
            ("dermatology", "Dermatologie"),
            ("blood_test", "Bilan sanguin"),
            ("other", "Autre"),
        ],
        string="Type",
    )
    frequency = fields.Selection(
        [
            ("quarterly", "Trimestriel"),
            ("biannually", "Semestriel"),
            ("yearly", "Annuel"),
            ("two_years", "Aux 2 ans"),
            ("five_years", "Aux 5 ans"),
            ("custom", "Personnalisé"),
        ],
        string="Fréquence",
    )
    frequency_months = fields.Integer(string="Fréquence (mois)")
    last_date = fields.Date(string="Dernière date", tracking=True)
    next_due = fields.Date(
        string="Prochaine échéance",
        compute="_compute_next_due",
        store=True,
        tracking=True,
    )
    provider = fields.Char(string="Fournisseur / Clinique")
    lead_days = fields.Integer(string="Jours d'avance pour rappel", default=30)
    notes = fields.Text(string="Notes")
    state = fields.Selection(
        [
            ("up_to_date", "À jour"),
            ("due_soon", "Bientôt dû"),
            ("overdue", "En retard"),
        ],
        string="État",
        compute="_compute_state",
        store=True,
    )
    color = fields.Integer(string="Couleur", compute="_compute_state", store=True)
    days_until_due = fields.Integer(
        string="Jours avant échéance",
        compute="_compute_due_info",
    )
    is_overdue = fields.Boolean(
        string="En retard",
        compute="_compute_due_info",
    )

    @api.depends("last_date", "frequency", "frequency_months")
    def _compute_next_due(self):
        for rec in self:
            if not rec.last_date or not rec.frequency:
                rec.next_due = False
                continue
            if rec.frequency == "custom":
                months = rec.frequency_months or 12
            else:
                months = FREQUENCY_MONTHS.get(rec.frequency, 12)
            rec.next_due = rec.last_date + relativedelta(months=months)

    @api.depends("next_due")
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if not rec.next_due:
                rec.state = "up_to_date"
                rec.color = 10
                continue
            delta = (rec.next_due - today).days
            if delta < 0:
                rec.state = "overdue"
                rec.color = 1
            elif delta <= (rec.lead_days or 30):
                rec.state = "due_soon"
                rec.color = 3
            else:
                rec.state = "up_to_date"
                rec.color = 10

    @api.depends("next_due")
    def _compute_due_info(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if not rec.next_due:
                rec.days_until_due = 999
                rec.is_overdue = False
                continue
            delta = (rec.next_due - today).days
            rec.days_until_due = delta
            rec.is_overdue = delta < 0

    @api.model
    def _cron_check_screenings(self):
        """Create activities for screenings coming due."""
        activity_type = self.env.ref(
            "bf_health.mail_activity_type_screening_reminder",
            raise_if_not_found=False,
        )
        if not activity_type:
            return

        today = fields.Date.context_today(self)
        screenings = self.search([
            ("next_due", "!=", False),
        ])
        for scr in screenings:
            threshold = scr.next_due - relativedelta(days=scr.lead_days or 30)
            if today < threshold:
                continue
            if today > scr.next_due + relativedelta(days=60):
                continue
            existing = scr.activity_ids.filtered(
                lambda a: a.activity_type_id == activity_type
            )
            if existing:
                continue
            # Au nom de la personne, assignée à elle.
            fiche, responsable = au_nom_du_proprietaire(scr)
            fiche.activity_schedule(
                "bf_health.mail_activity_type_screening_reminder",
                date_deadline=scr.next_due,
                # Résumé neutre : il part
                # dans les résumés quotidiens par courriel. Le nom reste dans la note.
                summary=fiche.env._("Healthy Fox : examen à planifier"),
                # Le nom seul, ni clinique ni date.
                note=Markup("<strong>%s</strong>%s") % (scr.name, mention_de_la_personne(scr)),
                user_id=responsable,
            )
