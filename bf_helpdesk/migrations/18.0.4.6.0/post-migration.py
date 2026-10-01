"""18.0.4.6.0 : SLA sur horaire d'affaires, pause en attente client,
première réponse limitée aux messages publics, état SLA stocké.

- Rattrape `first_response_date` depuis le chatter existant.
- Ouvre la pause des billets déjà en attente du client.
- Recalcule échéances et état sur tous les billets.
- Passe la tâche planifiée à une heure (la donnée est en noupdate).
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Ticket = env["helpdesk.ticket"].with_context(active_test=False)
    tickets = Ticket.search([])

    rattrapes = 0
    for ticket in tickets:
        messages = ticket.message_ids.sorted(lambda m: (m.date, m.id))
        for message in messages:
            if Ticket._bf_is_staff_public_reply(message):
                ticket.first_response_date = message.date
                rattrapes += 1
                break

    now = env.cr.now()
    waiting = tickets.filtered(
        lambda t: t.waiting_state == "client" and not t.stage_id.closed
    )
    waiting.write({"sla_paused_since": now})

    env.add_to_compute(Ticket._fields["sla_response_deadline"], tickets)
    env.add_to_compute(Ticket._fields["sla_resolve_deadline"], tickets)
    env.add_to_compute(Ticket._fields["sla_state"], tickets)
    tickets.flush_recordset()

    cron = env.ref("bf_helpdesk.ir_cron_sla_breach", raise_if_not_found=False)
    if cron:
        cron.write({"interval_number": 1, "interval_type": "hours"})

    _logger.info(
        "bf_helpdesk 18.0.4.6.0 : %s billets, %s premières réponses "
        "rattrapées, %s pauses ouvertes.",
        len(tickets), rattrapes, len(waiting),
    )
