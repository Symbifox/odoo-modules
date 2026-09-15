"""Un gabarit de tournée qui porte une grille la pose sur chacun de ses points."""
from odoo import models


class BfNfcTemplate(models.Model):
    _inherit = "bf.nfc.template"

    def _valeurs_point(self, pose, nom, endroit):
        valeurs = super()._valeurs_point(pose, nom, endroit)
        if self.checklist_id:
            # Comme dans `bf_nfc_inspection` : rien n'est écrit sur la grille, qui est
            # partagée entre toutes les poses.
            valeurs["checklist_id"] = self.checklist_id.id
        return valeurs
