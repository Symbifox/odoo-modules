from odoo import api, fields, models
from .gen_portees import LIBELLE_SANTE, PORTEE_SANTE


class HealthMedicationLog(models.Model):
    _name = "health.medication.log"
    # Verrou de Gen (voir models/gen_portees.py).
    _gen_scope = PORTEE_SANTE
    _gen_scope_label = LIBELLE_SANTE
    _inherit = ["bf.health.parent.guard"]
    _bf_champs_parents = ("medication_id",)
    _description = "Journal de prise de médicament"
    _order = "date desc, time_slot asc, medication_id asc"

    medication_id = fields.Many2one(
        "health.medication",
        string="Médicament",
        required=True,
        ondelete="cascade",
    )
    medication_name = fields.Char(
        related="medication_id.name", string="Nom", store=True
    )
    date = fields.Date(
        string="Date", required=True, default=fields.Date.context_today
    )
    time_slot = fields.Selection(
        [
            ("morning", "Matin"),
            ("noon", "Midi"),
            ("evening", "Soir"),
            ("night", "Nuit"),
            ("as_needed", "Au besoin"),
        ],
        string="Moment",
    )
    taken = fields.Boolean(string="Pris", default=False)
    skipped_reason = fields.Char(string="Raison du saut")
    notes = fields.Char(string="Notes")

    @api.constrains("medication_id")
    def _check_medicament_de_la_meme_personne(self):
        """On ne rattache son journal qu'à le médicament qu'on peut lire.

        Les règles d'enregistrement ne sont rejouées ni après une écriture ni
        sur les champs reliés : sans ce contrôle, une personne du ménage
        pointait son journal vers la fiche d'une autre (ids séquentiels) et en
        relisait le nom ou les valeurs, calculés en superutilisateur. En
        superutilisateur (crons), `check_access` laisse passer.
        """
        for rec in self:
            rec.medication_id.check_access("read")
