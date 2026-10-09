"""Une pièce jointe déplacée sur une fiche par son seul res_id.

``ir.attachment.check`` ne contrôle la fiche cible que si ``res_model`` ET ``res_id``
sont écrits ensemble : sans cette garde, un membre déplaçait sa pièce jointe dans la fiche privée d'autrui.
Déplacer vers une fiche de personne exige d'en être la propriétaire.
"""
from odoo import models

MODELE = "bf.people.person"


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def write(self, vals):
        if not self.env.su and {"res_model", "res_id"} & set(vals):
            for piece in self.sudo():
                modele = vals.get("res_model", piece.res_model)
                rid = vals.get("res_id", piece.res_id)
                if modele == MODELE and rid:
                    self.env[MODELE].browse(rid).check_access("write")
        return super().write(vals)
