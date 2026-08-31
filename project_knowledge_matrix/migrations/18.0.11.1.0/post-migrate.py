"""Rejouer les noms de destinataire figés.

``recipient_name`` est un calcul stocké dont les dépendances ignoraient le nom
de la fiche liée : une distribution gardait le nom du jour de l'envoi, pour
toujours. La production Blue Fox en portait un cas — une fiche renommée depuis
février, dont la distribution affichait encore l'ancien nom.

Corriger les dépendances ne rattrape rien de l'existant : l'ORM ne rejoue un
calcul stocké que si l'une de ses dépendances bouge. D'où ce passage unique.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    distributions = env['project.document.distribution'].search([])
    if not distributions:
        return

    avant = {d.id: d.recipient_name for d in distributions}
    distributions._compute_recipient_name()
    env.flush_all()
    distributions.invalidate_recordset(['recipient_name'])

    rafraichis = [
        (d.id, avant[d.id], d.recipient_name)
        for d in distributions
        if avant[d.id] != d.recipient_name
    ]
    _logger.info(
        'project_knowledge_matrix: %s nom(s) de destinataire rejoué(s) sur %s.',
        len(rafraichis), len(distributions),
    )
    for identifiant, ancien, nouveau in rafraichis:
        _logger.info(
            'project_knowledge_matrix: distribution %s « %s » → « %s ».',
            identifiant, ancien, nouveau,
        )
