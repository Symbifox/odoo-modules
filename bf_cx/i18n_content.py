"""Per-language content for CX emails and the default NPS survey.

The French bodies live ONCE in data/mail_template_data.xml. English is
produced by translating the text nodes of that same markup (the branded
shell stays identical), then written into the en_CA jsonb slot. Applied by
the post_init_hook on fresh installs AND by migrations on upgrades - the
templates are noupdate=1, so a plain -u never refreshes them.
"""
import logging

from lxml import etree

from odoo.modules.module import get_module_resource

_logger = logging.getLogger(__name__)

MAIL_SUBJECTS_EN = {
    "mail_template_cx_invite": "Your feedback matters (2 minutes)",
    "mail_template_cx_reminder": "A quick reminder: your feedback matters",
    "mail_template_complaint_ack":
        "We have received your complaint ({{ object.confirmation_code }})",
}

# Keys are whitespace-normalized French text nodes; values are English.
MAIL_TEXTS_EN = {
    # Shared footer
    "Vous préférez ne plus recevoir de demandes d'avis ?":
        "Prefer not to receive feedback requests?",
    "Me désabonner": "Unsubscribe",
    "Bonjour": "Hello",
    "Merci beaucoup !": "Thank you so much!",
    "Répondre au sondage": "Take the survey",
    # Invitation
    "Votre avis": "Your feedback",
    "Merci de nous faire confiance. Auriez-vous deux minutes pour nous "
    "dire comment ça se passe de votre côté ? Vos réponses nous aident "
    "concrètement à nous améliorer.":
        "Thank you for trusting us. Would you have two minutes to tell us "
        "how things are going on your end? Your answers concretely help "
        "us improve.",
    "Le sondage est ouvert jusqu'au": "The survey is open until",
    # Reminder
    "Petit rappel": "A quick reminder",
    "Un petit rappel, sans aucune pression : votre avis nous aiderait "
    "vraiment, et il ne faut que deux minutes. C'est le seul rappel que "
    "vous recevrez.":
        "A quick, no-pressure reminder: your feedback would really help "
        "us, and it only takes two minutes. This is the only reminder "
        "you will receive.",
    # Complaint acknowledgement
    "Bien reçu": "Received",
    "Nous avons bien reçu votre plainte concernant «":
        "We have received your complaint about “",
    "» et nous la prenons au sérieux. Elle est entre bonnes mains dès "
    "maintenant.":
        "” and we take it seriously. It is in good hands as of now.",
    "Votre code de confirmation": "Your confirmation code",
    "Nous vous revenons avec notre analyse et la suite à donner dans les "
    "meilleurs délais. Merci de nous l'avoir signalée : c'est ce qui nous "
    "permet de nous améliorer.":
        "We will get back to you with our analysis and next steps as "
        "soon as possible. Thank you for letting us know: this is what "
        "helps us improve.",
}

# xmlid -> {field: (fr, en)} - FR is rewritten too (fixes legacy wording on
# upgraded databases, e.g. the em-dash in the original survey title).
SURVEY_TERMS = {
    "survey_nps_default": {
        "title": (
            "Sondage NPS : votre avis compte",
            "NPS survey: your feedback matters",
        ),
    },
    "question_nps_score": {
        "title": (
            "Quelle est la probabilité que vous nous recommandiez à un "
            "collègue ou à un partenaire d'affaires ?",
            "How likely are you to recommend us to a colleague or a "
            "business partner?",
        ),
        "scale_min_label": ("Très improbable", "Very unlikely"),
        "scale_max_label": ("Très probable", "Very likely"),
        "constr_error_msg": (
            "Cette question est obligatoire.",
            "This question is mandatory.",
        ),
    },
    "question_nps_reason": {
        "title": (
            "Qu'est-ce qui explique principalement votre note ?",
            "What is the main reason for your score?",
        ),
    },
    "question_nps_testimonial": {
        "title": (
            "Pourrions-nous citer vos commentaires comme témoignage ?",
            "Could we quote your comments as a testimonial?",
        ),
    },
    "answer_nps_testimonial_yes": {
        "value": (
            "Oui, contactez-moi pour confirmer",
            "Yes, contact me to confirm",
        ),
    },
    "answer_nps_testimonial_no": {
        "value": ("Non merci", "No thank you"),
    },
}


def _norm(text):
    return " ".join((text or "").split())


def _translate_markup(body_fr, texts_en):
    """Translate text nodes and alt/title attrs, keep the markup identical."""
    root = etree.fromstring("<div>%s</div>" % body_fr)
    for node in root.iter():
        for attr in ("text", "tail"):
            value = getattr(node, attr)
            if not value:
                continue
            english = texts_en.get(_norm(value))
            if english is None:
                continue
            lead = " " if value[:1].isspace() else ""
            trail = " " if value[-1:].isspace() else ""
            setattr(node, attr, lead + english + trail)
        for attr_name in ("alt", "title"):
            value = node.get(attr_name)
            if value and _norm(value) in texts_en:
                node.set(attr_name, texts_en[_norm(value)])
    return "".join(
        etree.tostring(child, encoding="unicode") for child in root
    )


def _read_template_sources(module, filename):
    """xmlid -> (subject_fr, body_fr) parsed from a module's data XML."""
    path = get_module_resource(module, "data", filename)
    tree = etree.parse(path)
    sources = {}
    for record in tree.findall(".//record[@model='mail.template']"):
        xmlid = record.get("id")
        subject = record.findtext("field[@name='subject']")
        body_el = record.find("field[@name='body_html']")
        body = "".join(
            etree.tostring(child, encoding="unicode") for child in body_el
        )
        sources[xmlid] = (subject, body)
    return sources


def apply_mail_templates_i18n(env, module, filename, subjects_en, texts_en):
    """Refresh FR from the data XML and write en_CA translations.

    Shared by bf_cx and its bridges: templates are noupdate=1, so this is
    the only path that updates them on existing databases.
    """
    langs = env["res.lang"].get_installed()
    codes = [code for code, _name in langs]
    has_en = "en_CA" in codes
    fr_codes = [code for code in codes if code.startswith("fr")]

    for xmlid, (subject_fr, body_fr) in _read_template_sources(
        module, filename
    ).items():
        template = env.ref(
            "%s.%s" % (module, xmlid), raise_if_not_found=False
        )
        if not template:
            continue
        vals_fr = {"subject": subject_fr, "body_html": body_fr}
        template.with_context(lang=None).write(vals_fr)
        for code in fr_codes:
            template.with_context(lang=code).write(vals_fr)
        if has_en:
            template.with_context(lang="en_CA").write(
                {
                    "subject": subjects_en.get(xmlid, subject_fr),
                    "body_html": _translate_markup(body_fr, texts_en),
                }
            )
    return has_en


def apply_cx_i18n(env):
    """Write FR (source refresh) and EN (en_CA slot) for templates and the
    default survey. Idempotent; safe when en_CA is not active."""
    has_en = apply_mail_templates_i18n(
        env, "bf_cx", "mail_template_data.xml", MAIL_SUBJECTS_EN,
        MAIL_TEXTS_EN,
    )
    langs = env["res.lang"].get_installed()
    codes = [code for code, _name in langs]
    fr_codes = [code for code in codes if code.startswith("fr")]

    for xmlid, fields_map in SURVEY_TERMS.items():
        record = env.ref("bf_cx.%s" % xmlid, raise_if_not_found=False)
        if not record:
            continue
        for field_name, (value_fr, value_en) in fields_map.items():
            if field_name not in record._fields:
                continue
            record.with_context(lang=None).write({field_name: value_fr})
            for code in fr_codes:
                record.with_context(lang=code).write({field_name: value_fr})
            if has_en:
                record.with_context(lang="en_CA").write(
                    {field_name: value_en}
                )

    # Backfill: complaints created before the confirmation-code field.
    complaints = env["bf.cx.complaint"].search(
        [("confirmation_code", "=", False)]
    )
    for complaint in complaints:
        complaint.confirmation_code = (
            complaint._generate_confirmation_code()
        )
    _logger.info(
        "bf_cx i18n applied (en_CA=%s, %s complaint codes backfilled)",
        has_en,
        len(complaints),
    )
