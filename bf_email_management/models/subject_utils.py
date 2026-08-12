"""Subject-line prefix helpers shared across the reply / forward flows.

Kept as plain functions (no Odoo model) so both ``bf.email`` and the
``mail.message`` reply override can collapse stacked ``Re:`` / ``Fwd:``
prefixes the same way.
"""

import re

# Leading reply/forward prefixes seen in the wild: EN "RE:/FW:/FWD:",
# Outlook FR "TR:" (transférer) and "RÉP:", with or without a space before
# the colon ("Re : Objet" is common in French clients).
SUBJECT_PREFIX_RE = re.compile(r"^\s*(re|ré|rép|fwd|fw|tr)\s*:\s*", re.IGNORECASE)

_FORWARD_PREFIXES = {"fwd", "fw", "tr"}


def dedup_subject_prefix(subject, force=None):
    """Collapse stacked Re:/Fwd: prefixes into a single canonical one.

    ``"Re: Re: Objet"`` -> ``"Re: Objet"``; ``"TR: Objet"`` -> ``"Fwd: Objet"``;
    ``"Re : Objet"`` (French spacing) -> ``"Re: Objet"``.

    ``force`` ("Re:" / "Fwd:") pins the canonical prefix when the caller
    already knows the intent (e.g. an explicit Forward), regardless of what
    the original subject carried. Without ``force`` the canonical prefix is
    derived from the first prefix found; a subject with no prefix is returned
    untouched.
    """
    text = (subject or "").strip()
    seen = []
    match = SUBJECT_PREFIX_RE.match(text)
    while match:
        seen.append(match.group(1).lower())
        text = text[match.end():].lstrip()
        match = SUBJECT_PREFIX_RE.match(text)

    if force:
        prefix = force
    elif seen:
        prefix = "Fwd:" if seen[0] in _FORWARD_PREFIXES else "Re:"
    else:
        return subject

    return f"{prefix} {text}".strip() if text else prefix
