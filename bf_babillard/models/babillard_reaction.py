# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

# Huit caractères : de quoi porter une séquence d'émojis composée (un drapeau,
# une famille, un pouce avec teinte de peau) sans laisser coller une phrase dans
# un bouton de carte.
LONGUEUR_MAX_SYMBOLE = 8


class BabillardReaction(models.Model):
    """Ce que la maison OFFRE comme réaction. Pas ce qu'une personne a posé.

    Le catalogue arrive garni et se coche : une douzaine de réactions
    prédéfinies, dont cinq offertes d'emblée, et l'administration en ajoute
    autant qu'elle veut. C'est une liste éditable, pas un écran de réglages :
    cocher « Offerte » suffit, et la ligne du bas crée une réaction maison.

    🔴 Les réactions prédéfinies sont semées en `noupdate="1"`, et c'est ce qui
    rend le décochage DURABLE. Odoo ne remet pas à jour un enregistrement dont
    l'identifiant externe existe déjà, mais il RECRÉE celui dont l'identifiant
    a disparu. Supprimer une réaction prédéfinie la ferait donc revenir au
    prochain `-u`, cochée comme au premier jour. D'où le refus de `unlink` :
    une prédéfinie se décoche, elle ne se supprime pas.
    """

    _name = "bf.babillard.reaction"
    _description = "Réaction offerte sur le babillard"
    _order = "sequence, id"

    name = fields.Char(
        "Nom", required=True, translate=True,
        help="Ce que le survol annonce : « Bravo », « Merci ».")
    symbole = fields.Char(
        "Symbole", required=True,
        help="L'émoji affiché sur la publication.")
    sequence = fields.Integer("Séquence", default=10)
    active = fields.Boolean(
        "Offerte", default=True,
        help="Décochée, la réaction n'est plus proposée. Celles déjà posées "
             "restent affichées : l'historique ne se réécrit pas tout seul.")
    predefinie = fields.Boolean(
        "Prédéfinie", readonly=True, default=False,
        help="Fournie avec le module. Elle se décoche, elle ne se supprime pas.")
    company_id = fields.Many2one(
        "res.company", string="Société",
        help="Vide : offerte dans toutes les sociétés.")
    nb_gestes = fields.Integer("Posée", compute="_compute_nb_gestes")

    _sql_constraints = [
        ("symbole_non_vide", "CHECK(symbole <> '')",
         "Une réaction a besoin d'un symbole."),
    ]

    def _compute_nb_gestes(self):
        # ⚠️ En sudo et en cardinal : le compte sert à l'administration pour
        # savoir si décocher une réaction va vider un écran, pas à mesurer qui
        # que ce soit. `read_group` plutôt qu'une requête par ligne.
        comptes = dict(self.env["bf.babillard.geste"].sudo()._read_group(
            [("reaction_id", "in", self.ids)], ["reaction_id"], ["__count"]))
        for reaction in self:
            reaction.nb_gestes = comptes.get(reaction, 0)

    @api.constrains("symbole")
    def _verifier_symbole(self):
        for reaction in self:
            if len(reaction.symbole or "") > LONGUEUR_MAX_SYMBOLE:
                raise ValidationError(self.env._(
                    "Un symbole tient en %(max)s caractères. "
                    "« %(symbole)s » en fait %(long)s.",
                    max=LONGUEUR_MAX_SYMBOLE, symbole=reaction.symbole,
                    long=len(reaction.symbole)))

    @api.constrains("symbole", "company_id", "active")
    def _verifier_unicite(self):
        """Deux boutons identiques sur une carte ne veulent rien dire.

        ⚠️ Le contrôle porte sur ce qui est OFFERT : deux réactions décochées
        peuvent porter le même symbole sans gêner personne. Une société ne peut
        pas non plus offrir son propre « 🎉 » à côté du « 🎉 » partagé : la
        carte afficherait deux boutons identiques, et personne ne saurait
        lequel cliquer.
        """
        for reaction in self:
            if not reaction.active:
                continue
            portees = [False] if not reaction.company_id else [
                False, reaction.company_id.id]
            jumelle = self.sudo().search([
                ("id", "!=", reaction.id),
                ("symbole", "=", reaction.symbole),
                # ⚠️ Redondant avec `active_test`, et gardé exprès : une
                # mutation l'a montré en survivant. `search` écarte déjà les
                # archivées, mais seulement tant que personne n'appelle ce
                # contrôle depuis un environnement `active_test=False`. Écrit,
                # il ne dépend plus du contexte de l'appelant.
                ("active", "=", True),
                ("company_id", "in", portees),
            ], limit=1)
            if jumelle:
                raise ValidationError(self.env._(
                    "« %(symbole)s » est déjà offert sous le nom "
                    "« %(nom)s ».", symbole=reaction.symbole, nom=jumelle.name))

    def unlink(self):
        # 🔴 Deux refus, deux raisons différentes.
        prealables = self.filtered("predefinie")
        if prealables:
            raise UserError(self.env._(
                "« %(noms)s » vient avec le module : décochez « Offerte » "
                "plutôt que de la supprimer. Supprimée, elle revient à la "
                "prochaine mise à jour, cochée.",
                noms=", ".join(prealables.mapped("name"))))
        # `ondelete="restrict"` sur le geste rendrait une erreur de base de
        # données ; celle-ci dit quoi faire.
        posees = self.filtered(lambda r: r.nb_gestes)
        if posees:
            raise UserError(self.env._(
                "« %(noms)s » a déjà été posée sur des publications. "
                "Décochez « Offerte » : les réactions déjà là restent "
                "lisibles, et personne n'en pose de nouvelle.",
                noms=", ".join(posees.mapped("name"))))
        return super().unlink()
