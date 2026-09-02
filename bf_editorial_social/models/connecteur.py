# -*- coding: utf-8 -*-
"""L'interface que chaque réseau implémente.

Le cadre ne connaît aucun réseau. Il connaît ce contrat, et rien d'autre :
un module de connecteur hérite de ce modèle abstrait, se déclare dans la
sélection du canal, et le cadre le résout par son nom.
"""

from odoo import _, api, models
from odoo.exceptions import UserError


class SocialConnector(models.AbstractModel):
    _name = "bf.social.connector"
    _description = "Contrat de connecteur de réseau social"

    # --- à implémenter par chaque réseau ---------------------------------
    def _publish(self, post):
        """Diffuser le billet. Rendre un dict.

        Attendu : ``{"remote_id": str, "url": str}``. Lever une UserError
        explicite en cas de refus du réseau. Ne JAMAIS avaler une erreur :
        un échec silencieux se relit comme une réussite au prochain passage.
        """
        raise NotImplementedError

    def _fetch_metrics(self, post):
        """Rendre les mesures du billet distant.

        Attendu : un dict de clés parmi impressions, likes, reposts, replies.
        Les clés absentes signifient « ce réseau ne les donne pas », pas zéro.
        """
        return {}

    def _validate_credentials(self, channel):
        """Vérifier que les identifiants ouvrent bien une session.

        Rendre ``(True, message)`` ou ``(False, message)``. Le message est
        montré à l'usager, donc il doit dire quoi corriger.
        """
        raise NotImplementedError

    def _limits(self):
        """Les limites que le réseau impose, pour information.

        Attendu : ``{"body_chars": int, "posts_per_hour": int|None}``.
        """
        return {"body_chars": 300, "posts_per_hour": None}

    def _link_in_body(self):
        """Le lien doit-il être écrit DANS le texte du billet ?

        Faux par défaut : un réseau qui sait faire une carte de lien à partir
        de ``link_url`` l'affiche mieux ainsi, et sans consommer de caractères
        là où la limite est serrée.

        Vrai pour un réseau qu'on alimente à la main : personne ne lit
        ``link_url`` en collant un texte, donc un lien qui n'est pas dans le
        corps n'existe pas.
        """
        return False

    # --- résolution -------------------------------------------------------
    @api.model
    def _for_network(self, network):
        """Le connecteur du réseau demandé, ou une erreur qui dit lequel manque."""
        nom = "bf.social.connector.%s" % network
        if nom not in self.env:
            raise UserError(_(
                "Aucun connecteur installé pour « %(reseau)s ». Le module"
                " correspondant n'est pas installé sur cette base.",
                reseau=network,
            ))
        return self.env[nom]
