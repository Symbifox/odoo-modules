"""La recette « tournée » : une tournée, un point par ligne, une pastille par point."""
from odoo import _, fields, models


class BfNfcTemplate(models.Model):
    _inherit = "bf.nfc.template"

    recette = fields.Selection(selection_add=[("tournee", "Une tournée et ses points")],
                               ondelete={"tournee": "set default"})
    round_schedule = fields.Selection(
        [("none", "Sans horaire"), ("daily", "Chaque jour"), ("weekdays", "Du lundi au vendredi")],
        string="Horaire de la tournée", default="none")
    round_start_hour = fields.Float(string="Commence à", default=8.0)
    round_duration = fields.Integer(string="Durée maximale (min)", default=60)
    round_strict_order = fields.Boolean(string="Dans l'ordre")

    def _valeurs_point(self, pose, nom, endroit):
        """Les valeurs d'un point créé par ce gabarit. Un satellite ajoute les siennes (la grille)."""
        self.ensure_one()
        return {"name": nom, "place": endroit or False}

    def _recette_tournee(self, pose, lignes):
        self.ensure_one()
        tournee = self.env["bf.nfc.round"].create({
            "name": pose.prefixe.strip() if pose.prefixe else self.name,
            "responsible_id": (pose.responsible_id or self.env.user).id,
            "schedule": self.round_schedule or "none",
            "start_hour": self.round_start_hour,
            "duration_minutes": self.round_duration or 60,
            "strict_order": self.round_strict_order,
            "checkpoint_ids": [(0, 0, dict(self._valeurs_point(pose, nom, endroit), sequence=rang))
                               for rang, (nom, endroit) in enumerate(lignes, start=1)],
        })
        geste = self.env.ref("bf_nfc_round.gesture_round_checkpoint")
        Tag = self.env["bf.nfc.tag"]
        return Tag.concat(*[Tag.create({
            "name": "%s · %s" % (tournee.name, point.name),
            "place": point.place or False,
            "gesture_id": geste.id,
            "res_model": point._name, "res_id": point.id,
        }) for point in tournee.checkpoint_ids])
