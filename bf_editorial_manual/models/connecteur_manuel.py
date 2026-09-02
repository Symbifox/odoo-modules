# -*- coding: utf-8 -*-
"""Les réseaux qu'on alimente à la main.

Un connecteur qui refuse d'envoyer n'est pas un connecteur inachevé : c'est la
description honnête d'un réseau dont la porte d'API n'est pas ouverte. Il porte
quand même la limite de caractères réelle, parce que c'est elle qui décide si
un blurb est publiable, et l'ignorer ferait écrire des textes à retailler.
"""

from odoo import _, models
from odoo.exceptions import UserError


class ConnecteurLinkedInManuel(models.AbstractModel):
    _name = "bf.social.connector.linkedin_manual"
    _inherit = "bf.social.connector"
    _description = "LinkedIn (publication manuelle)"

    _network_label = "LinkedIn (manuel)"

    def _limits(self):
        """3 000 caractères, la limite d'un billet LinkedIn."""
        return {"body_chars": 3000, "posts_per_hour": None}

    def _link_in_body(self):
        """Vrai : personne ne lit link_url en collant un texte.

        C'est la différence de fond avec un réseau qui publie par API. Bluesky
        reçoit link_url à part et en fait une carte ; ici, ce qui part est
        exactement ce qui est dans le presse-papiers.
        """
        return True

    def _validate_credentials(self, channel):
        """Rien à valider : ce canal n'ouvre aucune session.

        Rendre « valide » serait mentir, rendre « refusé » ferait croire à un
        problème à corriger. On dit ce qui est.
        """
        return True, _(
            "Canal manuel : aucun identifiant n'est requis, et aucune session"
            " n'est ouverte. La publication se fait sur LinkedIn."
        )

    def _publish(self, post):
        raise UserError(_(
            "Ce canal est manuel : rien ne part d'ici.\n\n"
            "Copiez le texte du billet, publiez-le sur LinkedIn, puis"
            " utilisez « Marquer comme diffusé » en collant l'adresse de la"
            " publication.\n\n"
            "La publication par API sur une page LinkedIn demande le produit"
            " Community Management API, soumis à l'approbation de LinkedIn."
        ))
