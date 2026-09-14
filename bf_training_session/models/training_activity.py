from odoo import _, api, fields, models


class TrainingActivity(models.Model):
    _inherit = "bf.training.activity"

    event_ids = fields.One2many(
        "event.event", "training_activity_id", string="Séances")
    event_count = fields.Integer(
        string="Nombre de séances", compute="_compute_event_count")

    @api.depends("event_ids")
    def _compute_event_count(self):
        for activite in self:
            activite.event_count = len(activite.event_ids)

    def action_open_events(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Séances"),
            "res_model": "event.event",
            # ⚠️ `views` est obligatoire : une action sans lui s'ouvre sur une
            # liste vide côté client, sans la moindre erreur côté serveur.
            "views": [(False, "list"), (False, "form")],
            "domain": [("training_activity_id", "=", self.id)],
            "context": {"default_training_activity_id": self.id},
        }
