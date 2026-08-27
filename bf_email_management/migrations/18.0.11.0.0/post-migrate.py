"""Sème les identités d'expédition que le module peut prouver.

Une personne n'avait qu'une adresse d'envoi, celle de sa fiche. Le modèle
`bf.email.identity` en autorise plusieurs, mais un écran vide au premier
démarrage n'aiderait personne : on fabrique donc, pour chaque usager interne,
l'identité de son adresse Odoo et celle de chacun de ses comptes IMAP.

Ces deux sources sont des possessions démontrées — l'adresse du compte Odoo,
et un login IMAP dont la personne détient le mot de passe — donc les rangées
naissent `verified`. Toute autre adresse se déclare à la main et attend un
administrateur courriel : c'est cette case qui autorise à écrire sous un nom,
et se l'accorder à soi-même la viderait de son sens.

Idempotent : `_sync_from_accounts` ne crée que ce qui manque et ne retouche
jamais une rangée existante.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    created = env["bf.email.identity"]._sync_from_accounts()
    _logger.info(
        "bf_email_management 18.0.11.0.0 : %s identité(s) d'expédition "
        "semée(s).", len(created))
