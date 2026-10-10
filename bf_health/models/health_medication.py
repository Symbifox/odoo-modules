from markupsafe import Markup

from odoo import api, fields, models
from datetime import timedelta
from .gen_portees import LIBELLE_SANTE, PORTEE_SANTE
from .parent_guard import au_nom_du_proprietaire
from .health_dependent import mention_de_la_personne


FREQUENCY_MAP = {
    "daily": 1,
    "twice_daily": 2,
    "three_daily": 3,
    "weekly": 1,
    "biweekly": 1,
    "monthly": 1,
    "as_needed": 0,
}


class HealthMedication(models.Model):
    _name = "health.medication"
    # Verrou de Gen (voir models/gen_portees.py).
    _gen_scope = PORTEE_SANTE
    _gen_scope_label = LIBELLE_SANTE
    _description = "Médicament"
    _inherit = ["bf.health.note.only", "mail.thread", "mail.activity.mixin", "bf.health.dependent.mixin"]
    _order = "state asc, name asc"

    name = fields.Char(string="Nom du médicament", required=True)
    active = fields.Boolean(default=True)
    dosage = fields.Char(string="Dosage")
    form = fields.Selection(
        [
            ("tablet", "Comprimé"),
            ("capsule", "Capsule"),
            ("liquid", "Liquide"),
            ("injection", "Injection"),
            ("topical", "Topique"),
            ("inhaler", "Inhalateur"),
            ("patch", "Timbre"),
            ("other", "Autre"),
        ],
        string="Forme",
    )
    frequency = fields.Selection(
        [
            ("daily", "Quotidien"),
            ("twice_daily", "2x par jour"),
            ("three_daily", "3x par jour"),
            ("weekly", "Hebdomadaire"),
            ("biweekly", "Aux 2 semaines"),
            ("monthly", "Mensuel"),
            ("as_needed", "Au besoin"),
        ],
        string="Fréquence",
    )
    times_per_day = fields.Integer(
        string="Doses par jour",
        compute="_compute_times_per_day",
        store=True,
    )
    prescriber = fields.Char(string="Prescripteur")
    pharmacy = fields.Char(string="Pharmacie")
    din = fields.Char(string="DIN")
    date_start = fields.Date(string="Date de début")
    date_end = fields.Date(string="Date de fin")
    renewal_date = fields.Date(string="Date de renouvellement", tracking=True)
    renewal_lead_days = fields.Integer(
        string="Jours d'avance pour rappel", default=14
    )
    refills_remaining = fields.Integer(string="Renouvellements restants")
    purpose = fields.Text(string="Raison")
    side_effects = fields.Text(string="Effets secondaires")
    notes = fields.Text(string="Notes")
    state = fields.Selection(
        [
            ("active", "Actif"),
            ("paused", "En pause"),
            ("discontinued", "Cessé"),
        ],
        string="État",
        default="active",
    )
    color = fields.Integer(string="Couleur", compute="_compute_color", store=True)
    days_until_renewal = fields.Integer(
        string="Jours avant renouvellement",
        compute="_compute_renewal_info",
    )
    is_renewal_due = fields.Boolean(
        string="Renouvellement dû",
        compute="_compute_renewal_info",
    )
    log_ids = fields.One2many(
        "health.medication.log", "medication_id", string="Historique de prise"
    )

    @api.depends("frequency")
    def _compute_times_per_day(self):
        for rec in self:
            rec.times_per_day = FREQUENCY_MAP.get(rec.frequency, 0)

    @api.depends("state")
    def _compute_color(self):
        color_map = {"active": 10, "paused": 3, "discontinued": 1}
        for rec in self:
            rec.color = color_map.get(rec.state, 0)

    @api.depends("renewal_date")
    def _compute_renewal_info(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.renewal_date:
                delta = (rec.renewal_date - today).days
                rec.days_until_renewal = delta
                rec.is_renewal_due = delta <= (rec.renewal_lead_days or 14)
            else:
                rec.days_until_renewal = 999
                rec.is_renewal_due = False

    @api.model
    def _cron_check_renewals(self):
        """Create activities for medications needing renewal soon."""
        activity_type = self.env.ref(
            "bf_health.mail_activity_type_prescription_renewal",
            raise_if_not_found=False,
        )
        if not activity_type:
            return

        today = fields.Date.context_today(self)
        meds = self.search([
            ("state", "=", "active"),
            ("renewal_date", "!=", False),
        ])
        for med in meds:
            threshold = med.renewal_date - timedelta(days=med.renewal_lead_days or 14)
            if today < threshold:
                continue
            if today > med.renewal_date + timedelta(days=30):
                continue
            # Duplicate prevention
            existing = med.activity_ids.filtered(
                lambda a: a.activity_type_id == activity_type
            )
            if existing:
                continue
            # Au nom de la personne, assignée à elle.
            fiche, responsable = au_nom_du_proprietaire(med)
            fiche.activity_schedule(
                "bf_health.mail_activity_type_prescription_renewal",
                date_deadline=med.renewal_date,
                # Résumé neutre : il part
                # dans les résumés quotidiens par courriel. Le nom reste dans la note.
                summary=fiche.env._("Healthy Fox : renouvellement à prévoir"),
                # Le nom seul. Le dosage et la pharmacie
                # restent sur la fiche, pas dans les activités.
                note=Markup("<strong>%s</strong>%s") % (med.name, mention_de_la_personne(med)),
                user_id=responsable,
            )

    @api.model
    def _cron_create_daily_med_logs(self):
        """Pre-create medication log entries for today for all active meds."""
        today = fields.Date.context_today(self)
        MedLog = self.env["health.medication.log"]

        slot_map = {
            "daily": ["morning"],
            "twice_daily": ["morning", "evening"],
            "three_daily": ["morning", "noon", "evening"],
            "weekly": ["morning"],
            "biweekly": ["morning"],
            "monthly": ["morning"],
        }

        meds = self.search([("state", "=", "active")])
        for med in meds:
            if med.frequency == "as_needed":
                continue
            # Weekly: only on start day of week
            if med.frequency == "weekly" and med.date_start:
                if today.weekday() != med.date_start.weekday():
                    continue
            # Biweekly: every 14 days from start
            if med.frequency == "biweekly" and med.date_start:
                delta = (today - med.date_start).days
                if delta % 14 != 0:
                    continue
            # Monthly: same day of month
            if med.frequency == "monthly" and med.date_start:
                if today.day != med.date_start.day:
                    continue

            slots = slot_map.get(med.frequency, ["morning"])
            for slot in slots:
                existing = MedLog.search([
                    ("medication_id", "=", med.id),
                    ("date", "=", today),
                    ("time_slot", "=", slot),
                ], limit=1)
                if not existing:
                    # Créé AU NOM de la personne. Créé par le
                    # compte système, le journal du jour lui restait invisible.
                    fiche, _responsable = au_nom_du_proprietaire(med)
                    MedLog.with_env(fiche.env).create({
                        "medication_id": med.id,
                        "date": today,
                        "time_slot": slot,
                        "taken": False,
                    })
