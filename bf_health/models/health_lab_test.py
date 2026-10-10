from markupsafe import Markup

from odoo import api, fields, models
from datetime import timedelta
from .gen_portees import LIBELLE_SANTE, PORTEE_SANTE
from .parent_guard import au_nom_du_proprietaire
from .health_dependent import mention_de_la_personne


class HealthLabTest(models.Model):
    _name = "health.lab.test"
    # Verrou de Gen (voir models/gen_portees.py).
    _gen_scope = PORTEE_SANTE
    _gen_scope_label = LIBELLE_SANTE
    _description = "Analyse de laboratoire"
    _inherit = ["bf.health.note.only", "mail.thread", "mail.activity.mixin", "bf.health.dependent.mixin"]
    _order = "date_performed desc, id desc"

    name = fields.Char(string="Nom de l'analyse", required=True)
    test_type = fields.Selection(
        [
            ("blood_work", "Prise de sang"),
            ("urine", "Analyse d'urine"),
            ("imaging", "Imagerie"),
            ("ecg", "ECG"),
            ("other", "Autre"),
        ],
        string="Type",
    )
    date_requisition = fields.Date(string="Date de la réquisition")
    date_performed = fields.Date(string="Date de l'analyse")
    date_results = fields.Date(string="Date des résultats")
    ordering_doctor = fields.Char(string="Médecin prescripteur")
    lab_name = fields.Char(string="Laboratoire")
    state = fields.Selection(
        [
            ("requisition", "Réquisition"),
            ("scheduled", "Planifiée"),
            ("performed", "Effectuée"),
            ("results_received", "Résultats reçus"),
        ],
        string="État",
        default="requisition",
    )
    results_summary = fields.Text(string="Résumé des résultats")
    results_normal = fields.Boolean(string="Résultats normaux")
    notes = fields.Text(string="Notes")
    next_due_date = fields.Date(string="Prochaine échéance", tracking=True)
    next_due_lead_days = fields.Integer(
        string="Jours d'avance pour rappel", default=30
    )

    @api.model
    def _cron_check_lab_tests(self):
        """Create activities for lab tests coming due."""
        activity_type = self.env.ref(
            "bf_health.mail_activity_type_lab_test",
            raise_if_not_found=False,
        )
        if not activity_type:
            return

        today = fields.Date.context_today(self)
        tests = self.search([
            ("next_due_date", "!=", False),
        ])
        for test in tests:
            threshold = test.next_due_date - timedelta(
                days=test.next_due_lead_days or 30
            )
            if today < threshold:
                continue
            if today > test.next_due_date + timedelta(days=60):
                continue
            existing = test.activity_ids.filtered(
                lambda a: a.activity_type_id == activity_type
            )
            if existing:
                continue
            # Au nom de la personne, assignée à elle.
            fiche, responsable = au_nom_du_proprietaire(test)
            fiche.activity_schedule(
                "bf_health.mail_activity_type_lab_test",
                date_deadline=test.next_due_date,
                # Résumé neutre : il part
                # dans les résumés quotidiens par courriel. Le nom reste dans la note.
                summary=fiche.env._("Healthy Fox : analyse à prévoir"),
                # Le nom seul, ni médecin ni laboratoire.
                note=Markup("<strong>%s</strong>%s") % (test.name, mention_de_la_personne(test)),
                user_id=responsable,
            )
