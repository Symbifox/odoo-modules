"""18.0.1.13.2, BEFORE the catalogue: protect what the French reader has.

The source moves from French to English. The module's records are noupdate=1:
an upgrade keeps their en_US value, which is French, and the catalogue loaded
right after this step ADDS the delivered French to every French slot that has
no key yet. On a value someone retouched, that delivered French would mask the
retouche for French readers.

So, before the catalogue: where a French language is installed and its slot
is empty, copy the current en_US value into it. That value is French (the
source was French until now), retouched or not. The end-migrate then turns
en_US into English only where it is still the delivered text.
"""
import logging

_logger = logging.getLogger(__name__)

# (table, field, xmlid) of the module's noupdate translatable data.
CIBLES = [
    ("mail_template", "name", "mail_template_cx_invite"),
    ("mail_template", "subject", "mail_template_cx_invite"),
    ("mail_template", "body_html", "mail_template_cx_invite"),
    ("mail_template", "name", "mail_template_cx_reminder"),
    ("mail_template", "subject", "mail_template_cx_reminder"),
    ("mail_template", "body_html", "mail_template_cx_reminder"),
    ("mail_template", "name", "mail_template_complaint_ack"),
    ("mail_template", "subject", "mail_template_complaint_ack"),
    ("mail_template", "body_html", "mail_template_complaint_ack"),
    ("mail_template", "name", "mail_template_cx_360_invite"),
    ("mail_template", "subject", "mail_template_cx_360_invite"),
    ("mail_template", "body_html", "mail_template_cx_360_invite"),
    ("survey_survey", "title", "survey_nps_default"),
    ("survey_question", "title", "question_nps_score"),
    ("survey_question", "scale_min_label", "question_nps_score"),
    ("survey_question", "scale_max_label", "question_nps_score"),
    ("survey_question", "constr_error_msg", "question_nps_score"),
    ("survey_question", "title", "question_nps_reason"),
    ("survey_question", "title", "question_nps_testimonial"),
    ("survey_question_answer", "value", "answer_nps_testimonial_direct"),
    ("survey_question_answer", "value", "answer_nps_testimonial_yes"),
    ("survey_question_answer", "value", "answer_nps_testimonial_no"),
    ("survey_survey", "title", "survey_360_default"),
    ("survey_question", "title", "question_360_score"),
    ("survey_question", "scale_min_label", "question_360_score"),
    ("survey_question", "scale_max_label", "question_360_score"),
    ("survey_question", "constr_error_msg", "question_360_score"),
    ("survey_question", "title", "question_360_keep"),
    ("survey_question", "title", "question_360_change"),
    # Becomes translatable in this version: the column is converted to
    # jsonb ({"en_US": current}) before this step.
    ("bf_cx_program", "name", "program_nps_default"),
    ("bf_cx_program", "name", "program_360_default"),
]


def migrate(cr, version):
    cr.execute("SELECT code FROM res_lang WHERE active AND code LIKE %s", ("fr%",))
    fr_codes = [row[0] for row in cr.fetchall()]
    if not fr_codes:
        return
    copies = 0
    for table, field, xmlid in CIBLES:
        cr.execute(
            "SELECT res_id FROM ir_model_data WHERE module = 'bf_cx' AND name = %s",
            (xmlid,),
        )
        row = cr.fetchone()
        if not row:
            continue
        for code in fr_codes:
            cr.execute(
                f'UPDATE "{table}" SET "{field}" = "{field}" || '
                f'jsonb_build_object(%s, "{field}"->\'en_US\') '
                f'WHERE id = %s AND "{field}" ? \'en_US\' AND NOT "{field}" ? %s',
                (code, row[0], code),
            )
            copies += cr.rowcount
    # The two scheduled jobs: their name lives on the server action.
    for xmlid in ("ir_cron_cx_wave_reminders", "ir_cron_cx_complaint_ack"):
        cr.execute(
            "SELECT c.ir_actions_server_id FROM ir_cron c "
            "JOIN ir_model_data d ON d.res_id = c.id AND d.model = 'ir.cron' "
            "WHERE d.module = 'bf_cx' AND d.name = %s",
            (xmlid,),
        )
        row = cr.fetchone()
        if not row:
            continue
        for code in fr_codes:
            cr.execute(
                'UPDATE ir_act_server SET name = name || '
                "jsonb_build_object(%s, name->'en_US') "
                "WHERE id = %s AND name ? 'en_US' AND NOT name ? %s",
                (code, row[0], code),
            )
            copies += cr.rowcount
    _logger.info("bf_cx 18.0.1.13.2: %s French slot(s) kept from en_US", copies)
