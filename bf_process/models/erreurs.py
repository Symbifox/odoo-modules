# -*- coding: utf-8 -*-
"""Traduire un refus de la bibliothèque en message d'interface.

`generateur/mesure.py` et `generateur/geometrie.py` restent en bibliothèque
standard pure : ils tournent aussi hors serveur, pour l'étalonnage et les
contrôles. Ils ne peuvent donc pas lever un `UserError`, et leur refus, qui est
délibéré et porte déjà l'explication, sortait en trace de 500.

Le décorateur vit ici plutôt que dans l'un des deux modèles qui s'en servent :
`serialisation` et `edition` en ont besoin tous les deux, et faire dépendre
l'un de l'autre pour un utilitaire aurait été une dépendance de travers.

⚠️ Décorer les points d'entrée « évidents » ne suffit pas. L'éditeur refait la
disposition — donc mesure — AVANT d'atteindre le rendu final, si bien qu'un
glisser sur une carte non mesurable renvoyait une trace alors que le même
défaut donnait un message partout ailleurs. Toute méthode qui appelle
`geo.plan()`, directement ou non, doit porter cette garde.
"""
import functools

from odoo import _
from odoo.exceptions import UserError

from ..generateur import geometrie as geo
from ..generateur import mesure


def refus_lisible(methode):
    """Rend `MesureImpossible` et `GeometrieIncomplete` présentables.

    Le message est reconstruit ici, et non repris tel quel, pour qu'il passe
    par `_()` : la chaîne de la bibliothèque est du français en dur, et une
    fois recopiée dans un `UserError` elle n'aurait plus jamais été traduisible.

    `GeometrieIncomplete` n'est plus levée aujourd'hui — la mesure côté serveur
    a remplacé le refus qui s'appuyait dessus. Elle reste attrapée parce
    qu'elle appartient toujours au contrat de `geometrie`, et qu'un futur refus
    de géométrie doit arriver lisible sans qu'on y repense.
    """
    @functools.wraps(methode)
    def enveloppe(self, *args, **kwargs):
        try:
            return methode(self, *args, **kwargs)
        except mesure.MesureImpossible as exc:
            if not exc.caractere:
                # refus de mesure d'une autre nature : son propre message dit
                # déjà quoi, et l'inventer ici donnerait un diagnostic faux.
                raise UserError(str(exc)) from exc
            raise UserError(_(
                "Le caractère %s n'est pas dans la table de largeurs de Lexend."
                " La carte ne peut pas être mesurée sans deviner."
            ) % exc.caractere) from exc
        except geo.GeometrieIncomplete as exc:
            raise UserError(_("La carte ne peut pas être tracée : %s") % exc) from exc
    return enveloppe
