"""Le gabarit de fermeture de Blue Fox prend la place de celui de l'OCA.

« Ticket fermé » (helpdesk_mgmt.closed_ticket_template) dit « ticket », sort
avec l'adresse nue de la société et doublait le sondage de satisfaction. Choix
retenu : un courriel Blue Fox, un seul à la fermeture. Seules
les étapes qui portent ENCORE le gabarit de l'OCA changent ; un choix fait à la
main reste en place.
"""
import logging

_logger = logging.getLogger(__name__)


def use_bf_closing_template(env):
    oca = env.ref("helpdesk_mgmt.closed_ticket_template", raise_if_not_found=False)
    ours = env.ref("bf_helpdesk.mail_template_ticket_closed", raise_if_not_found=False)
    if not oca or not ours:
        return env["helpdesk.ticket.stage"]
    stages = env["helpdesk.ticket.stage"].sudo().with_context(active_test=False).search(
        [("mail_template_id", "=", oca.id)])
    stages.write({"mail_template_id": ours.id})
    _logger.info("bf_helpdesk : gabarit de fermeture Blue Fox posé sur %s étape(s) : %s",
                 len(stages), ", ".join(stages.mapped("name")))
    return stages
