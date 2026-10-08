# -*- coding: utf-8 -*-
"""Retenir le demandeur sur les ordres du jour déjà nés d'un rendez-vous.

⚠️ Seulement ceux que la confirmation a fabriqués : `contributions_preopened`
n'est posé que par elle. Un OdJ écrit à la main puis lié à la rencontre d'un
rendez-vous ne doit PAS devenir reprenable : la reprise rouvre sa page de
contribution au demandeur.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {"active_test": False})
    agendas = env["meeting.agenda"].search([
        ("contributions_preopened", "=", True),
        ("bf_booking_partner_id", "=", False),
        ("calendar_event_id", "!=", False),
    ])
    poses = 0
    for agenda in agendas:
        booking = env["resource.booking"].search(
            [("meeting_id", "=", agenda.calendar_event_id.id)], limit=1)
        # Seul à seul seulement : à plusieurs, `partner_id` est le premier par
        # ordre alphabétique, pas le demandeur.
        # Et seulement un rendez-vous sûr : né sur la page publique, son OdJ a
        # montré son jeton à qui a tapé l'adresse.
        booker = booking._bf_sole_requester() \
            if booking and booking._bf_resume_is_safe() else booking.browse()
        if booker:
            cr.execute(
                "UPDATE meeting_agenda SET bf_booking_partner_id = %s WHERE id = %s",
                (booker.id, agenda.id))
            poses += 1
    _logger.info("bf_appointment_meeting 18.0.1.1.0 : demandeur retenu sur %s "
                 "ordre(s) du jour né(s) d'un rendez-vous (%s candidat(s)).",
                 poses, len(agendas))
