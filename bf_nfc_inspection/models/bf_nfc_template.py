"""Un gabarit de relevé porte sa grille, posée sur chaque pastille créée."""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BfNfcTemplate(models.Model):
    _inherit = "bf.nfc.template"

    checklist_id = fields.Many2one("bf.nfc.checklist", string="Grille de relevé", ondelete="restrict")

    @api.constrains("gesture_id", "checklist_id")
    def _check_grille(self):
        for gabarit in self:
            if gabarit.gesture_id.kind == "reading" and not gabarit.checklist_id:
                raise ValidationError(_("« %s » : un gabarit de relevé porte sa grille.", gabarit.name))

    def _valeurs_pastille(self, pose, nom, endroit):
        valeurs = super()._valeurs_pastille(pose, nom, endroit)
        if self.checklist_id and self.gesture_id.kind == "reading":
            valeurs["checklist_id"] = self.checklist_id.id
            # ⚠️ Poser un gabarit n'écrit RIEN sur la grille. Elle est partagée entre
            # toutes les poses (et souvent entre les sociétés) : y inscrire le
            # responsable choisi ici donnerait à cette personne les anomalies de tous
            # les bâtiments, sans que personne l'ait demandé. Le responsable se choisit
            # sur la grille, une fois.
        return valeurs
