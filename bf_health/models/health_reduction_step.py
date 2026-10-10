from markupsafe import Markup

from odoo import api, fields, models
from .gen_portees import LIBELLE_SANTE, PORTEE_SANTE
from .parent_guard import au_nom_du_proprietaire


class HealthReductionStep(models.Model):
    _name = "health.reduction.step"
    # Verrou de Gen (voir models/gen_portees.py).
    _gen_scope = PORTEE_SANTE
    _gen_scope_label = LIBELLE_SANTE
    _description = "Étape de réduction"
    _inherit = ["bf.health.note.only", "mail.thread", "mail.activity.mixin"]
    _order = "sequence, date_start, id"

    code = fields.Char(string="Code", required=True, index=True)
    name = fields.Char(string="Étape", required=True, tracking=True)
    phase = fields.Selection(
        [
            ("1_stabilisation", "Phase 1 \u2014 Stabilisation"),
            ("2_reduction", "Phase 2 \u2014 Réduction"),
            ("3_sevrage", "Phase 3 \u2014 Sevrage"),
            ("4_arret", "Phase 4 \u2014 Arrêt"),
            ("5_zero", "Phase 5 \u2014 Zéro"),
        ],
        string="Phase",
        required=True,
        tracking=True,
    )
    sequence = fields.Integer(default=10)
    date_start = fields.Date(string="Date début", tracking=True)
    date_end = fields.Date(string="Date fin", tracking=True)
    description = fields.Text(string="Description")
    dependency_ids = fields.Many2many(
        "health.reduction.step",
        "health_reduction_step_dep_rel",
        "step_id",
        "dep_id",
        string="Dépendances",
    )
    state = fields.Selection(
        [
            ("todo", "À faire"),
            ("in_progress", "En cours"),
            ("done", "Fait"),
            ("skipped", "Ignoré"),
        ],
        string="État",
        default="todo",
        required=True,
        tracking=True,
    )
    substance_type = fields.Selection(
        [
            ("cannabis", "Cannabis"),
            ("alcohol", "Alcool"),
            ("tobacco", "Tabac"),
            ("caffeine", "Caféine"),
        ],
        string="Substance",
        required=True,
    )
    notes = fields.Text(string="Notes")
    color = fields.Integer(compute="_compute_color")

    @api.depends("state")
    def _compute_color(self):
        color_map = {"todo": 0, "in_progress": 4, "done": 10, "skipped": 2}
        for rec in self:
            rec.color = color_map.get(rec.state, 0)

    def _track_subtype(self, init_values):
        """Force all tracking messages to internal notes (private)."""
        self.ensure_one()
        return self.env.ref("mail.mt_note")

    def _message_auto_subscribe_followers(self, updated_values, subtype_ids):
        """Prevent automatic follower subscription for privacy."""
        return []

    @api.model
    def _cron_check_reduction_steps(self):
        """Create activities for steps starting within 2 days."""
        today = fields.Date.context_today(self)
        horizon = fields.Date.add(today, days=2)
        activity_type = self.env.ref(
            "bf_health.mail_activity_type_reduction_step", raise_if_not_found=False
        )
        if not activity_type:
            return
        steps = self.search(
            [
                ("state", "=", "todo"),
                ("date_start", "<=", horizon),
                ("date_start", ">=", today),
            ]
        )
        for step in steps:
            existing = step.activity_ids.filtered(
                lambda a: a.activity_type_id == activity_type
            )
            if not existing:
                # Posée AU NOM de la personne, plus du compte système.
                fiche, responsable = au_nom_du_proprietaire(step)
                fiche.activity_schedule(
                    "bf_health.mail_activity_type_reduction_step",
                    date_deadline=step.date_start,
                    # Résumé neutre : il part
                    # dans les résumés quotidiens par courriel. Le nom reste dans la note.
                    summary=fiche.env._("Healthy Fox : étape à venir"),
                    note=Markup("<strong>%s</strong>") % f"{step.code} : {step.name}",
                    user_id=responsable,
                )
