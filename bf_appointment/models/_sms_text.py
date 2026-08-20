"""GSM-7 helpers for appointment reminder SMS.

VoIP.ms bills and validates by septet, not by character. Two facts drive this
module, both measured in production (see the SMS bulk notes from the OVI
go-live, 2026-07-06):

* the effective ceiling is ~150, not the nominal 160 — 155 was refused with
  ``sms_toolong`` while 151 went through;
* leaving the GSM-7 alphabet silently switches the whole message to UCS-2,
  which collapses the budget to 70 septets.

The trap for a French body is that the alphabet is not symmetric: ``é è à ù
ì ò Ç É Ä Ö Ñ Ü ä ö ñ ü`` are all GSM-7, but ``ç ê â î ô û ë ï`` are not — and
neither are the typographic quotes ``« »``, the ellipsis ``…`` or the em dash
``—`` that a French writer reaches for by reflex. Screening at authoring time
is the only way to keep a reminder from dying at send time, since
``bf_securetransfer.sms.send()`` truncates blindly at 160 and reports nothing
back but ``False``.
"""

# GSM 03.38 basic set: one septet each.
GSM7_BASIC = (
    "@£$¥èéùìòÇ\nØø\rÅå"
    "Δ_ΦΓΛΩΠΨΣΘΞÆæßÉ"
    " !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
    "¿abcdefghijklmnopqrstuvwxyzäöñüà"
)

# Extension table: reachable, but each costs two septets (ESC + char).
GSM7_EXT = "^{}\\[~]|€\f"

# Deliberately below the nominal 160: see the module docstring.
MAX_SEPTETS = 150


def septet_len(text):
    """Length of ``text`` in septets, or ``None`` if it is not GSM-7 at all.

    ``None`` is the caller's signal that the message would be sent as UCS-2
    (a 70-septet budget), which no reminder body should rely on.
    """
    total = 0
    for char in text or "":
        if char in GSM7_BASIC:
            total += 1
        elif char in GSM7_EXT:
            total += 2
        else:
            return None
    return total


def non_gsm7_chars(text):
    """The distinct characters of ``text`` outside GSM-7, in order of first use."""
    seen = []
    for char in text or "":
        if char not in GSM7_BASIC and char not in GSM7_EXT and char not in seen:
            seen.append(char)
    return seen


def check(text):
    """Return an operator-facing error for ``text``, or ``None`` when sendable.

    Kept free of Odoo imports so both the authoring constraint and the cron's
    runtime guard can call it on the raw body and on the rendered body.
    """
    length = septet_len(text)
    if length is None:
        bad = "".join(non_gsm7_chars(text))
        return (
            "Ces caractères ne sont pas dans l'alphabet SMS (GSM-7) et "
            "feraient basculer le message en UCS-2, dont la limite tombe à "
            "70 caractères : %s. À noter que é è à Ç "
            "É passent, mais pas ç ê â î ô û "
            "ë ï, ni « » … —." % bad
        )
    if length > MAX_SEPTETS:
        return (
            "Le message fait %d caractères (comptés en septets). "
            "VoIP.ms refuse au-delà de %d en pratique, même si la "
            "limite théorique est 160." % (length, MAX_SEPTETS)
        )
    return None
