"""18.0.11.52.0 : « Traité » vaut aussi « Pas de relance ».

Sur une boîte réelle, la quasi-totalité des lignes de « Relance à faire »
étaient déjà traitées, et rien ne les en sortait avant 120 jours. Désormais
le geste « Traité » pose `awaiting_dismissed` ; ce rattrapage le pose sur le
dernier message de chaque fil quand il est de nous et déjà traité, puis le cron
recalcule la liste sur-le-champ plutôt qu'à son prochain passage.

SQL : une colonne simple, et l'ORM passerait par `write`, qui notifie la boîte
de chaque titulaire pour rien.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    # La cible est celle du cron : le dernier message ACTIF de chaque fil, s'il
    # est de nous ET traité. Pas seulement les lignes déjà au drapeau : un fil
    # traité dont notre envoi a moins de cinq jours serait entré dans la liste
    # après la montée malgré son « Traité » (relecture adverse).
    cr.execute(
        """
        WITH derniers AS (
            SELECT DISTINCT ON (user_id, thread_root_id)
                   id, direction, is_handled
              FROM bf_email
             WHERE active = TRUE
               AND thread_root_id IS NOT NULL
             ORDER BY user_id, thread_root_id, date DESC, id DESC
        )
        UPDATE bf_email
           SET awaiting_dismissed = TRUE
         WHERE id IN (SELECT id FROM derniers
                       WHERE direction = 'out' AND is_handled IS TRUE)
        """)
    _logger.info("bf_email 11.52.0 : « Pas de relance » posé sur %s lignes déjà traitées",
                 cr.rowcount)
    env = api.Environment(cr, SUPERUSER_ID, {})
    restantes = env["bf.email"]._cron_flag_awaiting_reply()
    _logger.info("bf_email 11.52.0 : %s relances à faire après recalcul", restantes)
