"""How close a folder name is to a client or a project name (18.0.4.2.0).

Only used to SORT the folders offered when linking a record to its folder: the
person always chooses. The name alone is not always enough: a numbered company
such as "1234-5678 Québec Inc." may own the folders "Anne-Sophie Valmorel
Brideaux (perso)" and "Chantier Sérénité", hence the comparison with contacts
and projects too.
"""

import difflib
import re
import unicodedata

# Words that carry no identity in a company or folder name.
EMPTY_WORDS = {
    "inc", "ltee", "ltd", "qc", "quebec", "les", "des", "de", "du", "la", "le",
    "et", "and", "the", "perso", "projet", "projets",
}
NUMBER_PREFIX = re.compile(r"^\s*\d{1,4}\s*[-.]\s*")


def normalize(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def strip_number(name):
    """« 041 - Toitures Valberge » -> « Toitures Valberge »."""
    return NUMBER_PREFIX.sub("", name or "").strip()


def words(text):
    return {
        w for w in normalize(text).split()
        if len(w) > 2 and w not in EMPTY_WORDS and not w.isdigit()
    }


def similarity(a, b, loose=False):
    """0 to 1: the better of the character sequence and the shared words.

    Two shared words or more count against the SHORTER name (« Chantier
    Sérénité » is all of the folder, inside a longer project name). A single
    shared word counts against the LONGER one: « Odoo » alone must not make a
    folder « 055 - Odoo » the twin of a project « Odoo Quincaillerie Lune ».

    `loose` is for the name of a client or of a contact: there, a name held
    whole in the other counts against the shorter one even with one word
    (« 051 - KORVA » is the folder of « Services KORVA »).
    """
    a, b = strip_number(a), strip_number(b)
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    wa, wb = words(a), words(b)
    shared = 0.0
    if wa and wb:
        common = len(wa & wb)
        held = loose and common and (wa <= wb or wb <= wa)
        shared = common / (min if common >= 2 or held else max)(len(wa), len(wb))
    return max(seq, shared)


def best(name, candidates, loose=False):
    """Best similarity of `name` with any of `candidates` (texts)."""
    return max((similarity(name, c, loose) for c in candidates if c), default=0.0)
