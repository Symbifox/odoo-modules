"""18.0.4.8.0 : gabarit maître des courriels, sujet de fil figé, gabarits bilingues.

- Sujet de fil (email_thread_subject) : le nom actuel des billets existants.
- Langue du client (client_lang) : celle du client, sinon celle de la société.
- Gabarits de courriel : ils sont en noupdate, un -u ne les réécrit donc pas.
  On recharge data/mail_templates.xml une fois, en mode init, SI aucun des
  gabarits du module n'a été retouché depuis sa création : une retouche à la
  main ne s'écrase pas en silence. Les cases de traduction autres que la
  source sont vidées : les deux langues vivent dans le gabarit lui-même, et
  une case périmée l'emporterait sur le nouveau texte.
Écritures de rattrapage en SQL : ni suivi ni write_date.
"""
import logging

from odoo import SUPERUSER_ID, api
from odoo.tools import convert_file

_logger = logging.getLogger(__name__)

TEMPLATES = (
    "mail_template_ticket_ack",
    "mail_template_waiting_reminder",
    "mail_template_waiting_autoclose",
    "mail_template_csat_v2",
    "mail_template_client_update",
)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE helpdesk_ticket
           SET email_thread_subject = name
         WHERE email_thread_subject IS NULL
    """)
    sujets = cr.rowcount
    cr.execute("""
        UPDATE helpdesk_ticket t
           SET client_lang = COALESCE(
                   (SELECT p.lang FROM res_partner p WHERE p.id = t.partner_id),
                   (SELECT cp.lang FROM res_company c
                      JOIN res_partner cp ON cp.id = c.partner_id
                     WHERE c.id = t.company_id))
         WHERE t.client_lang IS NULL
    """)
    langues = cr.rowcount

    cr.execute("""
        SELECT d.name, t.create_date, t.write_date
          FROM ir_model_data d
          JOIN mail_template t ON t.id = d.res_id
         WHERE d.module = 'bf_helpdesk' AND d.model = 'mail.template'
           AND d.name IN %s
    """, (TEMPLATES,))
    retouches = [
        name for name, created, written in cr.fetchall()
        if created and written and (written - created).total_seconds() > 60
    ]
    if retouches:
        _logger.warning(
            "bf_helpdesk 18.0.4.8.0 : gabarits retouchés à la main (%s), "
            "rechargement SAUTÉ. Les versions bilingues sont dans "
            "data/mail_templates.xml, à reporter à la main.",
            ", ".join(retouches),
        )
    else:
        env = api.Environment(cr, SUPERUSER_ID, {})
        convert_file(env, "bf_helpdesk", "data/mail_templates.xml", {},
                     mode="init", noupdate=True)
        cr.execute("""
            UPDATE mail_template t
               SET body_html = jsonb_build_object('en_US', t.body_html->'en_US'),
                   subject = jsonb_build_object('en_US', t.subject->'en_US')
              FROM ir_model_data d
             WHERE d.res_id = t.id AND d.model = 'mail.template'
               AND d.module = 'bf_helpdesk' AND d.name IN %s
        """, (TEMPLATES,))
    _logger.info(
        "bf_helpdesk 18.0.4.8.0 : %s sujets de fil, %s langues de client, "
        "gabarits %s.", sujets, langues,
        "non rechargés" if retouches else "rechargés",
    )
