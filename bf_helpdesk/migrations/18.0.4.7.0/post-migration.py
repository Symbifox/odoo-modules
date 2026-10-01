"""18.0.4.7.0 : parcours client (statut client, canal, accusé).

- L'accusé de réception passe de la case `public_form_auto_ack` aux canaux
  de l'équipe. Une équipe qui avait la case cochée reçoit le canal Web, et
  rien d'autre : la montée ne met aucun nouveau courriel en route.
- Consigne le canal Courriel sur les billets nés d'un courriel entrant.
- Archive l'ancien gabarit d'accusé (noupdate, remplacé par
  mail_template_ticket_ack).
- Satisfaction : la colonne `csat_mode` naît avec la valeur par défaut
  « native », qu'Odoo pose sur les équipes existantes. On la remet à ce
  qu'elles faisaient : « survey » si un sondage était réglé, sinon rien.
- Relances : désactivées (valeur par défaut), rien à faire.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {"tracking_disable": True})
    Team = env["helpdesk.ticket.team"].with_context(active_test=False)
    web = env.ref("helpdesk_mgmt.helpdesk_ticket_channel_web", raise_if_not_found=False)
    email = env.ref("helpdesk_mgmt.helpdesk_ticket_channel_email", raise_if_not_found=False)

    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'helpdesk_ticket_team'
           AND column_name = 'public_form_auto_ack'
    """)
    avec_accuse = []
    if cr.fetchone():
        cr.execute(
            "SELECT id FROM helpdesk_ticket_team WHERE public_form_auto_ack IS TRUE"
        )
        avec_accuse = [r[0] for r in cr.fetchall()]
    teams = Team.search([])
    # Les équipes existantes partent toutes de zéro canal : la valeur par
    # défaut (Web et Courriel) ne vaut que pour une équipe créée après.
    teams.write({"ack_channel_ids": [(5, 0, 0)]})
    if web and avec_accuse:
        Team.browse(avec_accuse).exists().write({"ack_channel_ids": [(6, 0, web.ids)]})

    avec_sondage = teams.filtered("csat_survey_id")
    avec_sondage.write({"csat_mode": "survey"})
    (teams - avec_sondage).write({"csat_mode": "none"})

    consignes = 0
    if email:
        Ticket = env["helpdesk.ticket"].with_context(active_test=False)
        for ticket in Ticket.search([("channel_id", "=", False)]):
            premier = ticket.message_ids.filtered(
                lambda m: m.message_type in ("email", "comment")
            ).sorted(lambda m: (m.date, m.id))[:1]
            if premier.message_type == "email" and not any(
                not u.share for u in premier.author_id.user_ids
            ):
                ticket.channel_id = email
                consignes += 1

    ancien = env.ref("bf_helpdesk.mail_template_public_form_ack", raise_if_not_found=False)
    if ancien:
        ancien.active = False

    _logger.info(
        "bf_helpdesk 18.0.4.7.0 : %s équipes, %s avec accusé Web, "
        "%s billets consignés au canal Courriel, %s équipes en sondage survey.",
        len(teams), len(avec_accuse), consignes, len(avec_sondage),
    )
