from odoo import _, api, models
from odoo.exceptions import AccessError

from ..utils import cache_decouverte


class BfCollaboraHelper(models.Model):
    _name = "bf.collabora.helper"
    _description = "Collabora Online, correctifs Blue Fox"

    @api.model
    def pieces_modifiables(self, attachment_ids):
        """Rendre, parmi les pièces demandées, celles que l'appelant peut écrire.

        Remplace l'appel amont ``collabora.odoo.can_write_doc``, qui a deux
        défauts : il rend une CHAÎNE JSON là où le client lit un objet, et il
        appelle ``check_access``, qui lève au lieu de rendre faux. Ici, une
        seule requête pour toute la liste, et une vraie réponse.
        """
        if not attachment_ids:
            return []
        pieces = self.env["ir.attachment"].browse(attachment_ids).exists()
        if not pieces or not pieces.has_access("write"):
            return []
        permises = []
        for piece in pieces.sudo():
            # ⚠️ `has_access` NE SUFFIT PAS sur `ir.attachment`. La vraie porte
            # est `check()`, que le modèle appelle lui-même dans `write()` :
            # c'est elle qui refuse une pièce sans `res_id` créée par
            # quelqu'un d'autre, et elle qui reporte le contrôle sur
            # l'enregistrement lié. Mesuré : sans elle, une personne se voyait
            # offrir le bouton « Modifier » sur la pièce privée d'une autre.
            # Le sudo n'est que pour le préchargement, comme le fait Odoo dans
            # `_filter_attachment_access` ; le contrôle se fait sans.
            try:
                piece.sudo(False).check("write")
            except AccessError:
                continue
            permises.append(piece.id)
        return permises

    @api.model
    def vider_cache_decouverte(self):
        """À lancer après une mise à niveau du serveur Collabora.

        Sans ça, l'adresse gardée porte encore l'ancien numéro de compilation
        pendant le reste de la durée de vie du cache, et l'éditeur ne se charge
        pas.

        ⚠️ Réservé à l'administration. La méthode est appelable par RPC comme
        toute méthode publique : sans ce garde, n'importe quel usager interne
        pourrait vider le cache en boucle et faire retélécharger la découverte à
        chaque ouverture de document, ce qui rend exactement le défaut que ce
        module corrige.
        """
        if not self.env.su and not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Seule l'administration peut vider ce cache."))
        return cache_decouverte.vider()
