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
    _description = "LinkedIn (manual posting)"

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
            "Manual channel: no credentials are required and no session "
            "is opened. Posting is done on LinkedIn."
        )

    def _publish(self, post):
        raise UserError(_(
            "This channel is manual: nothing is sent from here.\n\nCopy "
            "the post text, publish it on LinkedIn, then use \"Mark as "
            "published\" and paste the URL of the post.\n\nPosting to a "
            "LinkedIn page through the API requires the Community "
            "Management API product, which LinkedIn must approve."
        ))
