from odoo import api, fields, models
from .gen_portees import LIBELLE_SANTE, PORTEE_SANTE


class HealthCondition(models.Model):
    _name = "health.condition"
    # Verrou de Gen (voir models/gen_portees.py).
    _gen_scope = PORTEE_SANTE
    _gen_scope_label = LIBELLE_SANTE
    _description = "Condition de santé"
    _inherit = ["bf.health.note.only", "mail.thread"]
    _order = "date_onset desc, id desc"

    name = fields.Char(string="Nom", required=True)
    condition_type = fields.Selection(
        [
            ("acute", "Aiguë"),
            ("chronic", "Chronique"),
            ("recurring", "Récurrente"),
        ],
        string="Type",
    )
    date_onset = fields.Date(string="Date d'apparition")
    date_resolved = fields.Date(string="Date de résolution")
    state = fields.Selection(
        [
            ("active", "Active"),
            ("resolved", "Résolue"),
            ("managed", "Gérée"),
        ],
        string="État",
        default="active",
    )
    symptom_ids = fields.One2many(
        "health.symptom.log", "condition_id", string="Symptômes"
    )
    notes = fields.Text(string="Notes")
    color = fields.Integer(string="Couleur", compute="_compute_color", store=True)

    @api.depends("state")
    def _compute_color(self):
        color_map = {"active": 1, "managed": 4, "resolved": 10}
        for rec in self:
            rec.color = color_map.get(rec.state, 0)
