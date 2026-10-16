"""Relever le français des surfaces client AVANT que la source passe à l'anglais.

Les deux courriels (objet et corps) et les libellés « Discuter à » étaient
écrits en français dans la source. Le chargement des données de cette version
réécrit leur clé `en_US` en anglais et laisse les autres clés telles quelles
(traduction EN BLOC pour les courriels, voir 18.0.3.59.0 et 18.0.3.63.0).
Post-migrate a besoin de l'ancienne source pour décider de chaque clé ; elle
n'existe plus une fois les données chargées. On la range dans une table
temporaire : pre et post partagent le curseur de la montée.
"""

import logging

_logger = logging.getLogger(__name__)

GABARITS = ("meeting_agenda_mail_template", "meeting_report_mail_template")
CHAMPS_GABARIT = ("subject", "body_html")


def relever(cr):
    cr.execute("DROP TABLE IF EXISTS bf_meeting_t25549_source")
    cr.execute("""
        CREATE TEMP TABLE bf_meeting_t25549_source (
            tbl varchar, res_id int, champ varchar, source text)
    """)
    for champ in CHAMPS_GABARIT:
        cr.execute(f"""
            INSERT INTO bf_meeting_t25549_source (tbl, res_id, champ, source)
            SELECT 'mail_template', t.id, %s, t.{champ} ->> 'en_US'
              FROM mail_template t
              JOIN ir_model_data d ON d.model = 'mail.template' AND d.res_id = t.id
                   AND d.module = 'bf_meeting' AND d.name IN %s
        """, [champ, GABARITS])
    cr.execute("""
        INSERT INTO bf_meeting_t25549_source (tbl, res_id, champ, source)
        SELECT 'ir_model_fields_selection', s.id, 'name', s.name ->> 'en_US'
          FROM ir_model_fields_selection s
          JOIN ir_model_fields f ON f.id = s.field_id
         WHERE f.model = 'project.task' AND f.name = 'bf_discuss_tag'
    """)
    cr.execute("SELECT count(*) FROM bf_meeting_t25549_source")
    _logger.info("bf_meeting 18.0.3.68.0 : %s valeur(s) source relevée(s)", cr.fetchone()[0])


def migrate(cr, version):
    if not version:
        return
    relever(cr)
