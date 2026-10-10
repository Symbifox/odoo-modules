"""Une pièce jointe déplacée sur un rappel par son seul ``res_id``.

``ir.attachment`` ne contrôle la fiche cible que si ``res_model`` ET ``res_id``
sont écrits ensemble : une personne déposait ainsi un fichier dans le fil du
rappel d'une autre. Déplacer vers un rappel exige d'en être propriétaire.
"""
from odoo import models
from odoo.exceptions import AccessError

MODELE = "bf.credit.reminder"


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def write(self, vals):
        if not self.env.su and {"res_model", "res_id"} & set(vals):
            for piece in self.sudo():
                modele = vals.get("res_model", piece.res_model)
                rid = vals.get("res_id", piece.res_id)
                if modele == MODELE and rid:
                    rappel = self.env[MODELE].browse(rid)
                    if not rappel.sudo().exists() or not rappel.has_access("write"):
                        raise AccessError(self.env._(
                            "A file can only be attached to your own credit reminder."))
        return super().write(vals)
