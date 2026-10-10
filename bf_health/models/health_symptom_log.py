from odoo import api, fields, models
from .gen_portees import LIBELLE_SANTE, PORTEE_SANTE


class HealthSymptomLog(models.Model):
    _name = "health.symptom.log"
    # Verrou de Gen (voir models/gen_portees.py).
    _gen_scope = PORTEE_SANTE
    _gen_scope_label = LIBELLE_SANTE
    _inherit = ["bf.health.parent.guard"]
    _bf_champs_parents = ("condition_id",)
    _description = "Journal de symptômes"
    _order = "date desc, id desc"

    condition_id = fields.Many2one(
        "health.condition",
        string="Condition",
        ondelete="cascade",
    )
    date = fields.Date(
        string="Date", required=True, default=fields.Date.context_today
    )
    name = fields.Char(string="Symptôme", required=True)
    severity = fields.Selection(
        [
            ("1", "1 - Léger"),
            ("2", "2 - Modéré"),
            ("3", "3 - Important"),
            ("4", "4 - Sévère"),
            ("5", "5 - Intense"),
        ],
        string="Sévérité",
    )
    body_area = fields.Selection(
        [
            ("head", "Tête"),
            ("chest", "Poitrine"),
            ("abdomen", "Abdomen"),
            ("back", "Dos"),
            ("limbs", "Membres"),
            ("skin", "Peau"),
            ("general", "Général"),
            ("mental", "Mental"),
        ],
        string="Zone",
    )
    duration = fields.Char(string="Durée")
    notes = fields.Char(string="Notes")

    @api.constrains("condition_id")
    def _check_condition_de_la_meme_personne(self):
        """On ne rattache son journal qu'à la condition qu'on peut lire.

        Les règles d'enregistrement ne sont rejouées ni après une écriture ni
        sur les champs reliés : sans ce contrôle, une personne du ménage
        pointait son journal vers la fiche d'une autre (ids séquentiels) et en
        relisait le nom ou les valeurs, calculés en superutilisateur. En
        superutilisateur (crons), `check_access` laisse passer.
        """
        for rec in self:
            rec.condition_id.check_access("read")
