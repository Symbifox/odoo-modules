"""🔴 Joindre un fichier à une note doit faire bouger le `write_date` de la note.

Sans cela, un client qui suit `since` ne voit jamais la pièce arriver : la
`ir.attachment` est neuve, mais la `bf.note` est intacte. La carte reste
affichée sans son fichier jusqu'à ce que la note change pour une autre raison.
Mesuré à l'essai.

⚠️ Cette surcharge est sur un modèle de l'instance ENTIÈRE : elle est traversée
par chaque pièce jointe de chaque module. Elle doit donc sortir immédiatement
quand rien ne concerne les notes, et ne faire aucune requête dans ce cas.
"""
from odoo import models


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def _bf_note_ids(self):
        """Les notes visées par ces pièces. Vide dès que rien ne les concerne."""
        ids = {
            piece.res_id for piece in self
            if piece.res_model == "bf.note" and piece.res_id
        }
        return self.env["bf.note"].sudo().browse(sorted(ids)).exists() if ids \
            else self.env["bf.note"]

    def _bf_note_touch(self):
        """⚠️ Touche aussi la note quand la pièce appartient à un MESSAGE de son
        fil : une pièce de chatter porte le même `res_model`/`res_id` que la
        note, et rien dans la table ne les sépare (`res_field` est vide dans les
        deux cas). Le client redemande alors une note dont le contenu n'a pas
        changé — un aller-retour de trop, sans conséquence, et nettement
        préférable à une pièce jointe qui n'apparaît jamais.
        """
        notes = self._bf_note_ids()
        if notes:
            notes._mobile_touch()

    def create(self, vals_list):
        pieces = super().create(vals_list)
        pieces._bf_note_touch()
        return pieces

    def write(self, vals):
        # Renommer une pièce change ce que la carte affiche. ⚠️ Et il faut
        # toucher les notes d'AVANT comme celles d'APRÈS : déplacer une pièce
        # d'une note à l'autre laisse la première avec un fichier fantôme.
        avant = self._bf_note_ids() if {"res_model", "res_id", "name"} & set(vals) \
            else self.env["bf.note"]
        resultat = super().write(vals)
        (avant | self._bf_note_ids())._mobile_touch()
        return resultat

    def unlink(self):
        # Les notes se lisent AVANT la suppression : après, `res_id` est parti.
        notes = self._bf_note_ids()
        resultat = super().unlink()
        notes._mobile_touch()
        return resultat
