"""Recalculer les empreintes, pour qu'aucune carte ne reparte sans raison.

L'empreinte ne se calcule plus sur trois champs nommés mais sur les clés stables
de la carte. Sans ce passage, la première écriture sur chaque objet fédéré verrait
une empreinte différente et renverrait une carte identique au pair : bruit chez
lui, et une file de 47 envois inutiles chez nous.

Et 🔴 le lien de l'émetteur suivait l'archivage du miroir sans l'être : le compte
des objets fédérés sur la fiche du pair sur-comptait les archivés. On aligne ce
qui est déjà archivé.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Link = env["federation.link"].with_context(active_test=False)

    refaites = 0
    for link in Link.search([]):
        record = link._record().exists()
        if not record:
            continue
        try:
            fingerprint = Link._card_fingerprint(record._federation_card())
        except Exception:  # noqa: BLE001 -- un modèle absent ne doit pas bloquer la montée
            _logger.warning("bf_federation: empreinte non recalculée pour le lien %s", link.id)
            continue
        if link.fingerprint != fingerprint:
            link.fingerprint = fingerprint
            refaites += 1

    eteints = 0
    for link in Link.search([("active", "=", True), ("origin", "=", "local")]):
        record = link._record().exists()
        if record and "active" in record._fields and not record.active:
            link.active = False
            eteints += 1

    _logger.info("bf_federation: %s empreinte(s) recalculée(s), %s lien(s) d'émetteur aligné(s) "
                 "sur un objet archivé", refaites, eteints)
