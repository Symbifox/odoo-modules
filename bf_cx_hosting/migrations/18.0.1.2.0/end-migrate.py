"""18.0.1.2.0: the feedback email is written in English in the source.

Odoo never translates into en_US, the source language: while the source was
French, a contact set to English received this email in French. The previous
install hook only wrote English into the en_CA key.

The template is `noupdate`: an upgrade leaves the stored value alone, and the
catalogue never overwrites an existing key. So en_US is switched here, and only
when the body still holds the delivered text, word for word once the markup is
set aside (the markup stays the database's own: the brand colours are not the
same everywhere). Name and subject follow the body: a body edited by hand
leaves the whole template as is, rather than half in each language.

Before switching, each installed French language without its own key receives
the previous French: without a key it would read en_US, which becomes English.
"""

import logging
import re

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

MODULE = "bf_cx_hosting"
XMLID = "mail_template_hosting_maintenance_rating"
NAME = ("Expérience client : feedback post-maintenance", "Customer experience: post-maintenance feedback")
SUBJECT = ("Comment s'est passée la maintenance de votre service ?", "How did your service maintenance go?")
# Text of the body, markup set aside: as delivered in French, and in English.
DELIVERED_TEXT = "Votre avis &amp;nbsp; Bonjour , Nous venons de compléter une maintenance planifiée sur votre service . En un clic, comment évaluez-vous nos services d'hébergement ? Un commentaire libre peut être ajouté après le clic. Merci ! Vous préférez ne plus recevoir de demandes d'avis ? Me désabonner . &amp;nbsp; &amp;nbsp;"
ENGLISH_TEXT = "Your feedback &amp;nbsp; Hello , We have just completed a scheduled maintenance on your service . In one click, how would you rate our hosting services? You can add a free-form comment after clicking. Thank you! Prefer not to receive feedback requests? Unsubscribe . &amp;nbsp; &amp;nbsp;"
# (delivered French, English): a whole element text of the body.
SEGMENTS = [
    (
        "Nous venons de compléter une maintenance planifiée sur votre service",
        "We have just completed a scheduled maintenance on your service",
    ),
    (
        "En un clic, comment évaluez-vous nos services d'hébergement ?",
        "In one click, how would you rate our hosting services?",
    ),
    ("Votre avis", "Your feedback"),
    ("Bonjour", "Hello"),
    (
        "Un commentaire libre peut être ajouté après le clic. Merci !",
        "You can add a free-form comment after clicking. Thank you!",
    ),
    (
        "Vous préférez ne plus recevoir de demandes d'avis ?",
        "Prefer not to receive feedback requests?",
    ),
    ("Me désabonner", "Unsubscribe"),
]
ATTRIBUTES = [
    ("Satisfait", "Satisfied"),
    ("Correct", "Okay"),
    ("Insatisfait", "Dissatisfied"),
]
NBSP = r"(?:&nbsp;|&#160;|\xa0)"
SPACE = r"(?:\s|&nbsp;|&#160;|\xa0)+"
EDGE = r"(?:\s|&nbsp;|&#160;|\xa0)*"


def _text(body):
    body = re.sub(r"<[^>]+>", " ", body or "")
    return " ".join(re.sub(NBSP, " ", body).split())


def _pattern(text):
    words = [re.escape(w) for w in text.split()]
    return re.compile(r"(>" + EDGE + r")" + SPACE.join(words) + r"(" + EDGE + r"<)")


def _english_body(body):
    """The body in English, or None if it is not exactly the delivered one."""
    if _text(body) != DELIVERED_TEXT:
        return None
    for french, english in SEGMENTS:
        body, count = _pattern(french).subn(
            lambda m: re.sub(NBSP, "", m.group(1)) + english + re.sub(NBSP, "", m.group(2)),
            body,
        )
        if not count:
            return None
    for french, english in ATTRIBUTES:
        body, count = re.subn(
            r'((?:alt|title)=")' + re.escape(french) + '"', r"\g<1>" + english + '"', body
        )
        if count != 2:
            return None
    return body if _text(body) == ENGLISH_TEXT else None


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    template = env.ref(f"{MODULE}.{XMLID}", raise_if_not_found=False)
    raw = {}
    if template:
        # Read before the catalogue is loaded: it would add the keys we look for.
        cr.execute("SELECT name, subject, body_html FROM mail_template WHERE id = %s", [template.id])
        raw = dict(zip(("name", "subject", "body_html"), cr.fetchone()))
    langs = [code for code, _name in env["res.lang"].get_installed() if code != "en_US"]
    if langs:
        env["ir.module.module"]._load_module_terms([MODULE], langs, overwrite=True)
    if not template:
        return
    body = (raw["body_html"] or {}).get("en_US") or ""
    if _text(body) == ENGLISH_TEXT:
        return  # already switched
    english_body = _english_body(body)
    if english_body is None:
        _logger.warning("%s: %s edited by hand, left in its language", MODULE, XMLID)
        return
    switch = {"body_html": (english_body, body)}
    for field, (french, english) in (("name", NAME), ("subject", SUBJECT)):
        current = ((raw[field] or {}).get("en_US") or "").strip()
        if current == french:
            switch[field] = (english, french)
        elif current != english:
            _logger.warning("%s: %s.%s edited by hand, left as is", MODULE, XMLID, field)
    french_langs = [code for code in langs if code.startswith("fr")]
    for field, (_english, french) in switch.items():
        for code in french_langs:
            if code not in (raw[field] or {}):
                template.update_field_translations(field, {code: french})
    template.with_context(lang="en_US").write(
        {field: english for field, (english, _french) in switch.items()}
    )
    _logger.info("%s: %s switched to English for en_US (%s)", MODULE, XMLID, ", ".join(sorted(switch)))
