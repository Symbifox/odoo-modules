"""Une photo déposée sur un papier, par n'importe quel chemin, s'y affiche.

Le formulaire d'un papier montre ses photos par un Many2many. Une pièce jointe
créée directement sur la fiche (l'import du déménagement, une API) ne serait pas
dans ce Many2many : on l'y ajoute. Créer une pièce jointe sur une fiche exige déjà
le droit d'écrire sur la fiche (contrôle d'accès d'``ir.attachment``) : seuls ses
titulaires y arrivent.
"""
from odoo import api, models

DOCUMENT = "bf.household.document"


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    @api.model_create_multi
    def create(self, vals_list):
        pieces = super().create(vals_list)
        for piece in pieces:
            if piece.res_model == DOCUMENT and piece.res_id and not piece.res_field:
                papier = self.env[DOCUMENT].sudo().browse(piece.res_id).exists()
                if papier and piece not in papier.attachment_ids:
                    papier.attachment_ids = [(4, piece.id)]
        return pieces
