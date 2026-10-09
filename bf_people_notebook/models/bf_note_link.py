"""Seules les notes de la propriétaire se lient à sa fiche.

Sans cette garde, un membre liait sa note partagée à la fiche privée d'autrui (par le lien ou par
l'assistant de re-routage, qui n'exige que la lecture) : elle paraissait dans la fiche,
changeait sa « dernière rencontre » et sa recherche. La règle est un invariant
(contrainte), qui tient aussi pour un chemin en sudo, l'API mobile comprise.
"""
from odoo import api, models
from odoo.exceptions import ValidationError

MODELE = "bf.people.person"


class BfNoteLink(models.Model):
    _inherit = "bf.note.link"

    @api.constrains("note_id", "res_model", "res_id")
    def _check_people_card_owner(self):
        for lien in self.sudo().filtered(lambda l: l.res_model == MODELE and l.res_id):
            fiche = self.env[MODELE].sudo().with_context(active_test=False).browse(lien.res_id).exists()
            if fiche and lien.note_id.user_id != fiche.user_id:
                raise ValidationError(self.env._(
                    "Only the notes of the person who wrote this card can be linked to it."))
