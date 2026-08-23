"""Retire les pièces .ics résiduelles des réservations.

`_send_appointment_email` créait une `ir.attachment` .ics à chaque envoi et ne
la retirait jamais : une par confirmation, par rappel, par copie à
l'organisateur. Aucun gabarit ni aucune page ne les référence — le fichier
part dans le courriel, il n'est jamais rouvert depuis Odoo. Le code ne les
laisse plus derrière lui; ici on balaie ce qui s'est déjà accumulé.

⚠️ On ne touche QU'À ce qui n'est rattaché à aucun message. Un courriel resté
en file d'attente a encore besoin de sa pièce jointe, et rien ne dit qu'il n'y
en a pas un au moment où cette migration tourne.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        DELETE FROM ir_attachment
        WHERE res_model = 'resource.booking'
          AND mimetype = 'text/calendar'
          AND id NOT IN (SELECT attachment_id FROM message_attachment_rel)
        """
    )
    if cr.rowcount:
        _logger.info(
            "bf_appointment 2.51.0 : %s pièce(s) .ics résiduelle(s) retirée(s)",
            cr.rowcount,
        )
