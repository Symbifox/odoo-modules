# -*- coding: utf-8 -*-
"""Les groupes de destinataires du composeur comme source de signataires.

Le point d'extension est `_destinataires_signataires` de Célébrations, appelé
sur le tableau NON sudo : la résolution des groupes se fait donc avec les
droits de la personne qui invite, ce qui est la règle du module d'origine
(`_resolve_partners` refuse le sudo par principe).
"""

from odoo import fields, models


class CelebrationBoard(models.Model):
    _inherit = "bf.celebration.board"

    recipient_group_ids = fields.Many2many(
        "bf.recipient.group", "bf_celebration_board_recipient_group_rel",
        "board_id", "group_id",
        string="Groupes de destinataires",
        groups="bf_celebrations.group_organizer",
        help="Les groupes du composeur de courriels, les vôtres et ceux "
             "partagés avec vous. Résolus au moment de l'envoi, avec vos "
             "droits.")

    def _destinataires_signataires(self):
        destinataires = super()._destinataires_signataires()
        Groupe = self.env["bf.recipient.group"]
        if not Groupe._groups_enabled():
            # La fonction est éteinte sur ce locataire : le pont se tait,
            # plutôt que de faire par la bande ce que le composeur refuse.
            return destinataires
        # ⚠️ Les identifiants se lisent en sudo, puis on ne garde que les
        # groupes que la personne qui invite a le DROIT de lire (les siens et
        # les partagés). Lire `self.recipient_group_ids` directement lève une
        # erreur d'accès dès qu'un groupe privé d'un collègue a été posé sur
        # le tableau, et l'ouverture de la carte échouerait pour ça ; ici le
        # groupe hors de portée ne contribue rien, sans un mot, ce qui est
        # exactement la règle du composeur.
        groupes = Groupe.browse(
            self.sudo().recipient_group_ids.ids)._filter_access_rules("read")
        if not groupes:
            return destinataires
        # Sans le plafond des groupes : c'est celui des invitations de
        # Célébrations qui borne, et il connaît déjà les adresses exclues.
        for partenaire in groupes._resolve_partners(enforce_cap=False):
            adresse = (partenaire.email or "").strip()
            if adresse and "@" in adresse:
                destinataires.setdefault(
                    adresse.lower(), (partenaire.name or adresse, adresse))
        return destinataires
