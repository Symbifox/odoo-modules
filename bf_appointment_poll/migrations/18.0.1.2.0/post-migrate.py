# -*- coding: utf-8 -*-
"""Reprend `hold_slots` (booléen) en `hold_mode` (trois niveaux).

L'ancien booléen ne connaissait qu'un comportement : un événement marqué
`show_as='free'`, visible dans l'agenda mais qui ne bloque rien. C'est
exactement le niveau « visible » du nouveau réglage, donc la reprise est
littérale : coché devient « visible », décoché devient « aucune retenue ».

Le troisième niveau, « réserver réellement », n'existait pas : personne ne peut
l'avoir demandé, et on ne l'attribue donc à aucun sondage existant. Le
promouvoir d'office fermerait des plages que l'organisateur croyait ouvertes.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'appointment_poll' AND column_name = 'hold_slots'"
    )
    if not cr.fetchone():
        return
    cr.execute(
        "UPDATE appointment_poll SET hold_mode = CASE "
        "WHEN hold_slots THEN 'visible' ELSE 'none' END "
        "WHERE hold_mode IS NULL OR hold_mode = 'none'"
    )
    _logger.info("hold_slots repris en hold_mode sur %d sondage(s)", cr.rowcount)
    cr.execute("ALTER TABLE appointment_poll DROP COLUMN hold_slots")
