"""18.0.11.57.0 : les adresses des courriels déjà dans la boîte.

La table ``bf_email_participant`` naît vide à la montée ; ``create`` et
``write`` la tiennent à jour ensuite. Sans ce rétro-remplissage, la fiche
contact ne trouverait par adresse que le courrier arrivé après la pose.

Par lots, en SQL : l'ORM chargerait des dizaines de milliers de lignes de
`bf.email` et leurs champs calculés pour lire trois colonnes. Les lignes archivées sont comprises (le
dossier « Tous les courriels » et les fiches les montrent selon `active`).
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

LOT = 5000


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Email = env["bf.email"]
    dernier, total = 0, 0
    while True:
        cr.execute(
            """
            SELECT id, email_from, email_to, email_cc
              FROM bf_email
             WHERE id > %s
             ORDER BY id
             LIMIT %s
            """, [dernier, LOT])
        lignes = cr.fetchall()
        if not lignes:
            break
        Email._bf_ecrire_participants(lignes)
        dernier = lignes[-1][0]
        total += len(lignes)
    cr.execute("SELECT count(*) FROM bf_email_participant")
    _logger.info("bf_email 11.57.0 : adresses rétro-remplies pour %s courriel(s), "
                 "%s ligne(s) de participants", total, cr.fetchone()[0])
