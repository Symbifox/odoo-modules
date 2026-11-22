"""🔴 Joindre un fichier à une note doit faire bouger le `write_date` de la note.

Sans cela, un client qui suit `since` ne voit jamais la pièce arriver : la
`ir.attachment` est neuve, mais la `bf.note` est intacte. La carte reste
affichée sans son fichier jusqu'à ce que la note change pour une autre raison.
Mesuré à l'essai.

⚠️ Cette surcharge est sur un modèle de l'instance ENTIÈRE : elle est traversée
par chaque pièce jointe de chaque module. Elle doit donc sortir immédiatement
quand rien ne concerne les notes, et ne faire aucune requête dans ce cas.

🔴 C'est aussi ici que se pose la garde
« joindre, et ne retirer que les siennes ». Elle vivait au seul chemin du
téléphone (`bf.note._mobile_detach`), donc la décision ne tenait qu'au
téléphone : mesuré, un destinataire en écriture retirait la pièce
de l'auteur par le bouton X du chatter (`_delete_and_notify`, route
`/mail/attachment/delete`), par `unlink()`, et la déplaçait hors de la note par
un `write()`. `ir.attachment.check()` ne juge que la fiche PARENTE
(`access_mode = 'write' if mode in ('create', 'unlink') else mode`), et
l'écriture neuve du partage donne ce droit : élargir l'écriture d'un modèle
ouvre ses pièces jointes. Les trois chemins passent par `unlink()` ou `write()`,
et une garde se juge là où elle est APPELÉE.
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

    def _bf_note_garde_de_retrait(self):
        """Refuse de retirer, de déplacer ou de renommer la pièce d'un autre.

        🔴 La règle ne se réécrit pas ici : elle vit à UN endroit,
        `bf.note._mobile_exige_de_retirer`, avec ses deux phrases distinctes
        (une pièce de l'auteur, une pièce d'un message du fil) et son exemption
        de `peut_gerer` (« l'auteur, ou l'administration »). Deux écritures de
        la même règle, c'est deux vérités qui divergent au premier changement.

        ⚠️ La note est browsée sous l'usager RÉEL, jamais en sudo :
        `_mobile_exige_de_retirer` passe la main dès que `self.env.su`, donc une
        note lue en sudo désarmerait la garde en silence.
        """
        if self.env.su:
            return
        visees = {}
        for piece in self:
            if piece.res_model == "bf.note" and piece.res_id:
                visees.setdefault(piece.res_id, []).append(piece.id)
        if not visees:
            return
        Note = self.env["bf.note"].with_user(self.env.user)
        for note_id, ids in visees.items():
            note = Note.browse(note_id).exists()
            if not note:
                continue  # pièce orpheline : rien à protéger
            for piece in self.browse(ids):
                note._mobile_exige_de_retirer(piece)

    def create(self, vals_list):
        pieces = super().create(vals_list)
        pieces._bf_note_touch()
        return pieces

    def write(self, vals):
        # Renommer une pièce change ce que la carte affiche. ⚠️ Et il faut
        # toucher les notes d'AVANT comme celles d'APRÈS : déplacer une pièce
        # d'une note à l'autre laisse la première avec un fichier fantôme.
        bouge = bool({"res_model", "res_id", "name"} & set(vals))
        if bouge:
            # Déplacer ou renommer la pièce d'un autre, c'est la lui
            # retirer de sa note — sans la supprimer, et sans bruit. Les trois
            # clés sont celles qui changent ce que la note montre, ou la note
            # qui la porte.
            self._bf_note_garde_de_retrait()
        avant = self._bf_note_ids() if bouge else self.env["bf.note"]
        resultat = super().write(vals)
        (avant | self._bf_note_ids())._mobile_touch()
        return resultat

    def unlink(self):
        # TOUS les chemins de la suppression passent par ici — le
        # bouton X du chatter (`_delete_and_notify`), l'ORM nu, et le geste du
        # téléphone (`_mobile_detach`).
        self._bf_note_garde_de_retrait()
        # Les notes se lisent AVANT la suppression : après, `res_id` est parti.
        notes = self._bf_note_ids()
        resultat = super().unlink()
        notes._mobile_touch()
        return resultat
