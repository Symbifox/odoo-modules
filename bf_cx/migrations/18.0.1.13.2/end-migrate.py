"""18.0.1.13.2: the labels move from a French source to an English one.

Odoo never translates INTO en_US, the source language: while the source was
French, a user set to English read this module in French (an en_US-only database
read it all in French). The French now comes from i18n/fr_CA.po.

What an upgrade does not do by itself, done here:

1. The catalogue is reloaded OVERWRITING: an upgrade never overwrites an
   existing translation.
2. The noupdate templates, default surveys and default programs (whose name
   becomes translatable) turn English in their source slot ONLY where they
   still carry the delivered text (i18n_content, compared on the text, this
   database's markup kept). The French slots keep their French (post-migrate
   copied it there before the catalogue); an en_CA slot is written only when
   empty, still the delivered English, or French by mistake.
3. The two scheduled jobs (their name lives on the server action), same rule.
4. Names that are not translatable: the record rules, renamed when they still
   carry the delivered French; the complaint sequence (a core field: making
   it translatable would convert every sequence of the database), renamed
   ONLY where no French is installed.

⚠️ In `end`: Odoo loads the module's translations right after `post`.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

CRONS = {
    "ir_cron_cx_wave_reminders": (
        "Expérience client : entretien des vagues",
        "Customer experience: wave upkeep",
    ),
    "ir_cron_cx_complaint_ack": (
        "Expérience client : plaintes sans accusé de réception",
        "Customer experience: complaints without acknowledgement",
    ),
}

# The templates' language: the contact's, else the company's. Every caller
# is covered (bf_cx_crm and bf_cx_fundraising send the invitation too): with
# the contact's alone, a contact without a language, called by XML-RPC or by
# an English-speaking user, received the English source.
LANG_LIVRE = "{{ object.partner_id.lang }}"
LANGUES_GABARITS = {
    "mail_template_cx_invite": "{{ object.partner_id.lang or user.company_id.partner_id.lang }}",
    "mail_template_cx_reminder": "{{ object.partner_id.lang or user.company_id.partner_id.lang }}",
    "mail_template_cx_360_invite": "{{ object.partner_id.lang or user.company_id.partner_id.lang }}",
    "mail_template_complaint_ack": "{{ object.partner_id.lang or object.company_id.partner_id.lang }}",
}

# Earlier delivered French names (some databases still carry them).
ANCIENS = {
    "ir_cron_cx_wave_reminders": ("Expérience client : rappels des vagues",),
}

# xmlid -> (French delivered, English). Not translatable fields.
REGLES = {
    "rule_cx_program_company": ("Programmes : multi-société", "Programs: multi-company"),
    "rule_cx_wave_company": ("Vagues : multi-société", "Waves: multi-company"),
    "rule_cx_feedback_company": ("Feedbacks : multi-société", "Feedback: multi-company"),
    "rule_cx_complaint_company": ("Plaintes : multi-société", "Complaints: multi-company"),
    "rule_cx_testimonial_company": ("Témoignages : multi-société",
                                    "Testimonials: multi-company"),
    "rule_cx_feedback_operator_no_internal": (
        "Feedbacks : opérateur sans le 360 interne",
        "Feedback: operator without the internal 360"),
    "rule_cx_feedback_manager_all": ("Feedbacks : gestionnaire voit tout",
                                     "Feedback: manager sees everything"),
}
# ir.sequence.name belongs to the core and is not translatable: converting it
# would change the column of EVERY sequence of the database. The complaint
# sequence (seen in technical settings only) is renamed where no French is
# installed. The default programs became translatable: i18n_content.
CONTENU = {
    "seq_cx_complaint": ("Plaintes clients", "Customer complaints"),
}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    langs = [code for code, _name in env["res.lang"].get_installed()]
    fr_codes = [code for code in langs if code.startswith("fr")]
    en_codes = [code for code in langs if code.startswith("en") and code != "en_US"]

    # 1. Catalogue, overwriting.
    env["ir.module.module"]._load_module_terms(["bf_cx"], langs, overwrite=True)

    # 2. Templates and default surveys.
    from odoo.addons.bf_cx.i18n_content import apply_cx_i18n
    converted, kept = apply_cx_i18n(env, only_delivered=True)

    # 3. Scheduled jobs: write the server action, never ir.cron (NOWAIT lock).
    for xmlid, (fr, en) in CRONS.items():
        cron = env.ref("bf_cx.%s" % xmlid, raise_if_not_found=False)
        if not cron:
            continue
        action = cron.ir_actions_server_id
        cr.execute("SELECT name FROM ir_act_server WHERE id = %s", (action.id,))
        slots = cr.fetchone()[0] or {}
        if slots.get("en_US") not in (fr, en) + ANCIENS.get(xmlid, ()):
            continue  # renamed by someone: kept
        action.with_context(lang=None).write({"name": en})
        # French slot, same rule as en_CA: an earlier delivered name is
        # replaced, a wording of the database's own is kept.
        translations = {
            code: fr for code in fr_codes
            if not slots.get(code)
            or slots.get(code) in (fr, en) + ANCIENS.get(xmlid, ())
        }
        # en_CA: only empty, the delivered English, or French by mistake.
        translations.update({
            code: en for code in en_codes
            if not slots.get(code)
            or slots.get(code) in (fr, en) + ANCIENS.get(xmlid, ())
        })
        if translations:
            action.update_field_translations("name", translations)

    # 3b. Templates' language expression, only where still the delivered one.
    for xmlid, expression in LANGUES_GABARITS.items():
        template = env.ref("bf_cx.%s" % xmlid, raise_if_not_found=False)
        if template and (template.lang or "").strip() == LANG_LIVRE:
            template.with_context(tracking_disable=True).lang = expression

    # 4. Not translatable.
    renamed = 0
    for xmlid, (fr, en) in REGLES.items():
        record = env.ref("bf_cx.%s" % xmlid, raise_if_not_found=False)
        if record and record.name == fr:
            record.name = en
            renamed += 1
    if not fr_codes:
        for xmlid, (fr, en) in CONTENU.items():
            record = env.ref("bf_cx.%s" % xmlid, raise_if_not_found=False)
            if record and record.name == fr:
                record.name = en
                renamed += 1
    _logger.info(
        "bf_cx 18.0.1.13.2: %s template/survey field(s) turned English, %s "
        "kept as edited, %s name(s) renamed",
        converted, kept, renamed,
    )
