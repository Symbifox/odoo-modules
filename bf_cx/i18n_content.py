"""Per-language content for CX emails and the default surveys.

Since 18.0.1.13.2 the data files are written in ENGLISH, the source language: the
English bodies live ONCE in data/mail_template_data.xml and
data/survey_360_data.xml. French is produced by translating the text nodes of
that same markup (the branded shell stays identical) and written into each
installed French slot.

Two callers, two rules:

- the post_init_hook (fresh install) writes every language itself: it runs
  AFTER the catalogues are loaded, and the records are noupdate=1;
- the 18.0.1.13.2 migration (existing databases) only converts content that
  is still the one the module delivered, compared on its TEXT (the markup
  varies between databases: brand colours, importer serialization). A
  template or a question retouched by hand is left exactly as it is. The
  existing markup is kept and only its text nodes change language.

Before 18.0.1.13.2 the French was the source (en_US slot) and English went to the
en_CA slot only: an en_US-only database read French.
"""
import logging
import re

from lxml import etree
from lxml import html as lxml_html

from odoo.modules.module import get_module_resource

_logger = logging.getLogger(__name__)

NBSP = " "

# xmlid -> French subject / French name. English is the data file's.
MAIL_SUBJECTS_FR = {
    "mail_template_cx_invite": "Votre avis compte (2 minutes)",
    "mail_template_cx_reminder": "Petit rappel : votre avis compte",
    "mail_template_complaint_ack":
        "Votre plainte est bien reçue ({{ object.confirmation_code }})",
    "mail_template_cx_360_invite":
        "Votre regard sur {{ object.bf_cx_wave_id.subject_user_id.name "
        "or 'un collègue' }}",
}
MAIL_NAMES_FR = {
    "mail_template_cx_invite": "Expérience client : invitation au sondage",
    "mail_template_cx_reminder": "Expérience client : rappel de sondage",
    "mail_template_complaint_ack":
        "Expérience client : accusé de réception de plainte",
    "mail_template_cx_360_invite":
        "Expérience : invitation à une rétroaction 360",
}

# Keys are whitespace-normalized English text nodes; values are French. The
# English is, word for word, the one written in the en_CA slot since 2026-09
# (an English reader sees nothing change).
MAIL_TEXTS_FR = {
    # Shared header and footer
    "Your feedback": "Votre avis",
    "Hello": "Bonjour",
    "Thank you so much!": "Merci beaucoup" + NBSP + "!",
    "Take the survey": "Répondre au sondage",
    "Prefer not to receive feedback requests?":
        "Vous préférez ne plus recevoir de demandes d'avis" + NBSP + "?",
    "Unsubscribe": "Me désabonner",
    # Invitation
    "Thank you for trusting us. Would you have two minutes to tell us how "
    "things are going on your end? Your answers concretely help us improve.":
        "Merci de nous faire confiance. Auriez-vous deux minutes pour nous "
        "dire comment ça se passe de votre côté" + NBSP + "? Vos réponses "
        "nous aident concrètement à nous améliorer.",
    "The survey is open until": "Le sondage est ouvert jusqu'au",
    # Reminder
    "A quick reminder": "Petit rappel",
    "A quick, no-pressure reminder: your feedback would really help us, and "
    "it only takes two minutes. This is the only reminder you will receive.":
        "Un petit rappel, sans aucune pression" + NBSP + ": votre avis nous "
        "aiderait vraiment, et il ne faut que deux minutes. C'est le seul "
        "rappel que vous recevrez.",
    # Complaint acknowledgement
    "Received": "Bien reçu",
    "We have received your complaint about “":
        "Nous avons bien reçu votre plainte concernant «" + NBSP,
    "” and we take it seriously. It is in good hands as of now.":
        NBSP + "» et nous la prenons au sérieux. Elle est entre bonnes mains "
        "dès maintenant.",
    "Your confirmation code": "Votre code de confirmation",
    "We will get back to you with our analysis and next steps as soon as "
    "possible. Thank you for letting us know: this is what helps us "
    "improve.":
        "Nous vous revenons avec notre analyse et la suite à donner dans les "
        "meilleurs délais. Merci de nous l'avoir signalée" + NBSP + ": c'est "
        "ce qui nous permet de nous améliorer.",
    # Internal 360 invitation
    "Feedback": "Rétroaction",
    "We are collecting feedback on the work of":
        "Nous recueillons de la rétroaction sur le travail de",
    ". You work with this person: your view counts for more than ours.":
        ". Vous travaillez avec cette personne" + NBSP + ": votre regard "
        "vaut plus que le nôtre.",
    "We are collecting internal feedback on the way we work.":
        "Nous recueillons de la rétroaction interne sur notre façon de "
        "travailler.",
    "Allow three minutes. What helps are concrete examples, not round "
    "scores.":
        "Comptez trois minutes. Ce qui est utile, ce sont les exemples "
        "concrets, pas les notes rondes.",
    "Give my feedback": "Donner ma rétroaction",
    "Feedback is collected until": "La collecte est ouverte jusqu'au",
    "Your name is not entered in the feedback register: neither the summary, "
    "nor the lists, nor the exports show it. In a team this size, a comment "
    "can still be recognized by its content.":
        "Votre nom n'est pas inscrit au registre des rétroactions" + NBSP +
        ": ni la synthèse, ni les listes, ni les exports ne l'affichent. "
        "Dans une équipe de cette taille, un commentaire reste toutefois "
        "reconnaissable à son contenu.",
    "Your name comes with your answer.": "Votre nom accompagne votre réponse.",
}

# xmlid -> {field: (fr, en)}. Order kept as (fr, en): the 1.12.0 migration
# reads it.
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
    "answer_nps_testimonial_direct": {
        "value": (
            "Oui, vous pouvez me citer avec mon nom, ma fonction et mon "
            "organisation, sans me recontacter",
            "Yes, you may quote me with my name, title and organization, "
            "no need to contact me",
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
    "survey_360_default": {
        "title": (
            "Rétroaction 360 : le travail d'un collègue",
            "360 feedback: a colleague's work",
        ),
    },
    "question_360_score": {
        "title": (
            "Globalement, travailler avec cette personne rend votre propre "
            "travail plus facile ?",
            "Overall, does working with this person make your own work "
            "easier?",
        ),
        "scale_min_label": ("Pas du tout", "Not at all"),
        "scale_max_label": ("Énormément", "Enormously"),
        "constr_error_msg": (
            "Cette question est obligatoire.",
            "This question is mandatory.",
        ),
    },
    "question_360_keep": {
        "title": (
            "Qu'est-ce que cette personne fait bien et devrait continuer de "
            "faire ? Un exemple précis vaut mieux qu'un compliment général.",
            "What does this person do well and should keep doing? A specific "
            "example is worth more than a general compliment.",
        ),
    },
    "question_360_change": {
        "title": (
            "Qu'est-ce qui vous aiderait si cette personne le faisait "
            "autrement ?",
            "What would help you if this person did it differently?",
        ),
    },
    # The default programs (name translatable since 18.0.1.13.2).
    "program_nps_default": {"name": ("NPS relationnel", "Relationship NPS")},
    "program_360_default": {"name": ("Rétroaction 360", "360 feedback")},
}

TEMPLATE_FILES = ("mail_template_data.xml", "survey_360_data.xml")


def _norm(text):
    return " ".join((text or "").replace("&nbsp;", " ").split())


def _translate_markup(body, texts):
    """Translate text nodes and alt/title attrs, keep the markup identical.

    For the module's OWN data-file markup (well-formed XML). ``texts`` maps
    whitespace-normalized source text to its translation; a node whose text
    is not a key is left untouched.
    """
    root = etree.fromstring("<div>%s</div>" % body)
    for node in root.iter():
        for attr in ("text", "tail"):
            value = getattr(node, attr)
            if not value:
                continue
            translated = texts.get(_norm(value))
            if translated is None:
                continue
            lead = " " if value[:1].isspace() else ""
            trail = " " if value[-1:].isspace() else ""
            setattr(node, attr, lead + translated + trail)
        for attr_name in ("alt", "title"):
            value = node.get(attr_name)
            if value and _norm(value) in texts:
                node.set(attr_name, texts[_norm(value)])
    return (root.text or "") + "".join(
        etree.tostring(child, encoding="unicode") for child in root
    )


def _text_only(body):
    """The words a reader sees, whitespace-normalized; markup ignored.

    Parsed as HTML: a body stored in the database went through the sanitizer
    and is not always well-formed XML.
    """
    if not body or not str(body).strip():
        return ""
    root = lxml_html.fragment_fromstring(str(body), create_parent="div")
    return _norm(" ".join(root.itertext()))


_SEP = r"(?:\s|&nbsp;|&#160;|&#xa0;|\xa0|&amp;nbsp;)+"
_VARIANTES = {
    "'": "(?:'|&#39;|&#x27;|&apos;|\u2019)",
    "«": "(?:«|&laquo;|&#171;)",
    "»": "(?:»|&raquo;|&#187;)",
    "“": "(?:“|&ldquo;|&#8220;)",
    "”": "(?:”|&rdquo;|&#8221;)",
}


def _motif(text):
    """Regex for a text as it may be written in stored HTML: any run of
    spaces or non-breaking spaces between words, entity forms of quotes."""
    mots = [m for m in _norm(text.replace(NBSP, " ")).split(" ") if m]

    def mot(m):
        return "".join(_VARIANTES.get(c, re.escape(c)) for c in m)
    return re.compile(_SEP.join(mot(m) for m in mots))


def _convert_body(body, pairs):
    """Replace each text segment of a STORED body, markup untouched.

    ``pairs`` maps source text to target text. Longest first, so a short
    segment ("Feedback") never bites into a longer one.
    """
    out = str(body)
    for source in sorted(pairs, key=len, reverse=True):
        target = pairs[source]
        out = _motif(source).sub(
            lambda _m, t=target: t.replace("&", "&amp;").replace(NBSP, "&#160;"),
            out,
        )
    return out


def _read_template_sources(module, filename):
    """xmlid -> (name_en, subject_en, body_en) parsed from a data XML."""
    path = get_module_resource(module, "data", filename)
    tree = etree.parse(path)
    sources = {}
    for record in tree.findall(".//record[@model='mail.template']"):
        xmlid = record.get("id")
        subject = record.findtext("field[@name='subject']")
        name = record.findtext("field[@name='name']")
        body_el = record.find("field[@name='body_html']")
        body = (body_el.text or "") + "".join(
            etree.tostring(child, encoding="unicode") for child in body_el
        )
        sources[xmlid] = (name, subject, body)
    return sources


def _langs(env):
    codes = [code for code, _name in env["res.lang"].get_installed()]
    fr_codes = [code for code in codes if code.startswith("fr")]
    en_codes = [c for c in codes if c.startswith("en") and c != "en_US"]
    return fr_codes, en_codes


def _slots(record, field_name):
    """The jsonb value as stored: {lang: text}, without any fallback."""
    record.flush_recordset([field_name])
    record.env.cr.execute(
        'SELECT "%s" FROM "%s" WHERE id = %%s' % (field_name, record._table),
        (record.id,),
    )
    row = record.env.cr.fetchone()
    return (row and row[0]) or {}


def _write_langs(record, field_name, value_en, translations):
    """English in the source slot, then the other slots given.

    en_US is written first, with no language in the context: on a database
    where en_US is not active, writing another language would ALSO copy that
    value into en_US. The other slots then go through
    update_field_translations, which leaves en_US alone.
    """
    # Without tracking: a delivered name turning English is not an event
    # for the people following the record (a program's chatter showed
    # "NPS relationnel → Relationship NPS").
    record = record.with_context(tracking_disable=True, mail_notrack=True)
    record.with_context(lang=None).write({field_name: value_en})
    if translations:
        record.update_field_translations(field_name, translations)


def apply_cx_i18n(env, only_delivered=False):
    """Write English (source) and French for the module's templates and
    default surveys. Idempotent; safe whatever languages are installed.

    only_delivered: the migration path. A field is converted only when its
    source slot still carries what the module delivered (in French before
    18.0.1.13.2, or already in English); anything else was edited by someone and
    is kept as it is. A French slot that already holds a French text of its
    own is kept too.
    """
    fr_codes, en_codes = _langs(env)
    fr_to_en = {fr: en for en, fr in MAIL_TEXTS_FR.items()}
    converted = kept = 0

    def plan(record, field_name, value_en, value_fr, html):
        """(english, {lang: text}) to write, or None to leave alone."""
        if not only_delivered:
            translations = {code: value_fr for code in fr_codes}
            translations.update({code: value_en for code in en_codes})
            return value_en, translations
        slots = _slots(record, field_name)
        current = slots.get("en_US") or ""
        same = _text_only if html else _norm
        if same(current) == same(value_fr):
            # Delivered French in the source slot: its words go English,
            # this database's markup stays.
            english = (_convert_body(current, fr_to_en) if html
                       else value_en)
            if html and same(english) != same(value_en):
                _logger.warning(
                    "bf_cx i18n: %s.%s #%s not fully converted, kept",
                    record._name, field_name, record.id)
                return None
            french_src = current
        elif same(current) == same(value_en):
            english = current
            french_src = (_convert_body(current, MAIL_TEXTS_FR) if html
                          else value_fr)
        else:
            return None
        fr_values = {}
        for code in fr_codes:
            existing = slots.get(code)
            if existing and same(existing) not in (same(value_en), ""):
                continue  # a French text of its own: keep it
            fr_values[code] = french_src
        # Another English slot (en_CA) is written only when it is empty,
        # still the delivered English, or French by mistake: an English
        # wording of the database's own is kept.
        for code in en_codes:
            existing = slots.get(code)
            if not existing or same(existing) in (
                    same(value_en), same(value_fr), same(french_src)):
                fr_values[code] = english
        return english, fr_values

    for filename in TEMPLATE_FILES:
        for xmlid, (name_en, subject_en, body_en) in _read_template_sources(
            "bf_cx", filename
        ).items():
            template = env.ref("bf_cx.%s" % xmlid, raise_if_not_found=False)
            if not template:
                continue
            fields_map = {
                "name": (name_en, MAIL_NAMES_FR[xmlid], False),
                "subject": (subject_en, MAIL_SUBJECTS_FR[xmlid], False),
                "body_html": (
                    body_en, _translate_markup(body_en, MAIL_TEXTS_FR), True),
            }
            for field_name, (value_en, value_fr, html) in fields_map.items():
                todo = plan(template, field_name, value_en, value_fr, html)
                if todo is None:
                    kept += 1
                    continue
                _write_langs(template, field_name, todo[0], todo[1])
                converted += 1

    for xmlid, fields_map in SURVEY_TERMS.items():
        record = env.ref("bf_cx.%s" % xmlid, raise_if_not_found=False)
        if not record:
            continue
        for field_name, (value_fr, value_en) in fields_map.items():
            if field_name not in record._fields:
                continue
            todo = plan(record, field_name, value_en, value_fr, False)
            if todo is None:
                kept += 1
                continue
            _write_langs(record, field_name, todo[0], todo[1])
            converted += 1

    if not only_delivered:
        # Backfill: complaints created before the confirmation-code field.
        complaints = env["bf.cx.complaint"].search(
            [("confirmation_code", "=", False)]
        )
        for complaint in complaints:
            complaint.confirmation_code = (
                complaint._generate_confirmation_code()
            )
    _logger.info(
        "bf_cx i18n applied (French: %s, English slots: %s): %s field(s) "
        "written, %s kept as edited",
        fr_codes, en_codes, converted, kept,
    )
    return converted, kept
