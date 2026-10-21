"""Mesures sur une source externe : filtre des lignes, clés de client, rapprochement des noms.

Le tableur et le rapport filtrent chaque mesure de base par un domaine Odoo sur son champ
date et son champ client. Une mesure externe garde ce contrat : elle annonce deux champs
fictifs (DATE_FIELD, CUSTOMER_FIELD), et ses lignes sont filtrées ici, en Python, par ce
même domaine. Le champ client d'une ligne est le contact que la table de correspondance de
la connexion associe à sa valeur client ; une valeur sans correspondance n'a pas de client.
"""
import datetime
import math
import re
import unicodedata

from . import bf_bi_tables as tables

DATE_FIELD = "date"
CUSTOMER_FIELD = "partner_id"

# Mots qui ne distinguent pas une société d'une autre : « Érablière inc. » est « Érablière ».
_LEGAL = {"inc", "incorporated", "ltee", "limitee", "ltd", "limited", "enr", "senc", "sencrl", "srl",
          "corp", "corporation", "llc", "cie", "compagnie", "co"}
# Ni « != » sur la date ni « ! » : Odoo y ajoute ou y retire les cases vides, et ni le tableur
# ni le rapport ne les produisent. Refusés plutôt qu'à peu près justes.
_DATE_OPS = {">=", ">", "<=", "<", "="}
_CUSTOMER_OPS = {"in", "not in", "=", "!=", "child_of"}
MAX_TERMS = 200  # un domaine de filtre en compte une poignée ; au-delà, refus (récursion, coût)
# Types de colonne lisibles comme nombres ou comme dates (ceux des tables, plus ceux de Grist).
NUMBER_TYPES = ("Numeric", "Int")


class InvalidFilter(Exception):
    """Le domaine n'est pas un filtre de période ou de client que la mesure sait appliquer."""


def customer_key(value):
    """Clé de rapprochement d'une valeur client : casse et espaces ignorés ; None si vide.

    Un nombre entier lu d'un classeur (12.0) vaut « 12 », comme dans un CSV.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = " ".join(str(value).split()).casefold()
    return text or None


def normalized_name(text):
    """Nom réduit pour le rapprochement : sans accents, ponctuation ni forme juridique."""
    if not text:
        return None
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    words = [w for w in re.split(r"[^0-9a-z]+", text) if w and w not in _LEGAL]
    return " ".join(words) or None


def is_date_type(kind):
    return kind == "Date" or (kind or "").startswith("DateTime")


def serial_to_date(serial):
    """Serial de tableur (Date ou DateTime) → date ; None si la case est vide."""
    if serial is None or isinstance(serial, bool) or not isinstance(serial, (int, float)):
        return None
    return (tables._EPOCH + datetime.timedelta(days=serial)).date()


def _date_value(value):
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value
    if isinstance(value, str) and len(value) == 10:
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            pass
    raise InvalidFilter()


def _ids(value):
    """Ids de contacts d'un terme client ; False (pas de client) devient None."""
    values = value if isinstance(value, (list, tuple)) else [value]
    out = set()
    for v in values:
        if v is False:
            out.add(None)
        elif isinstance(v, int) and not isinstance(v, bool):
            out.add(v)
        else:
            raise InvalidFilter()
    return out


def compile_filter(domain, children=None):
    """Prédicat (date, contact) → bool équivalent au domaine Odoo `domain`.

    Seuls les termes sur DATE_FIELD et CUSTOMER_FIELD sont admis, joints par « & » et « | ».
    Comme dans Odoo, une ligne sans date échoue à tout terme de date, et « not in » ou « != »
    sur le client gardent les lignes sans client. `children(ids)` rend les ids et leurs enfants (child_of).
    """
    if not isinstance(domain, (list, tuple)) or len(domain) > MAX_TERMS:
        raise InvalidFilter()
    stack = []
    for term in reversed(list(domain)):
        if term in ("&", "|"):
            if len(stack) < 2:
                raise InvalidFilter()
            left, right = stack.pop(), stack.pop()
            if term == "&":
                stack.append(lambda d, p, a=left, b=right: a(d, p) and b(d, p))
            else:
                stack.append(lambda d, p, a=left, b=right: a(d, p) or b(d, p))
        else:
            stack.append(_term(term, children))
    if not stack:
        return lambda d, p: True
    # Des termes juxtaposés sont joints par ET, comme dans Odoo.
    while len(stack) > 1:
        left, right = stack.pop(), stack.pop()
        stack.append(lambda d, p, a=left, b=right: a(d, p) and b(d, p))
    return stack[0]


def _term(term, children):
    if not isinstance(term, (list, tuple)) or len(term) != 3:
        raise InvalidFilter()
    field, op, value = term
    if field == DATE_FIELD and op in _DATE_OPS:
        bound = _date_value(value)
        compare = {
            ">=": lambda d: d >= bound, ">": lambda d: d > bound, "<=": lambda d: d <= bound,
            "<": lambda d: d < bound, "=": lambda d: d == bound,
        }[op]
        return lambda d, p: d is not None and compare(d)
    if field == CUSTOMER_FIELD and op in _CUSTOMER_OPS:
        ids = _ids(value)
        if op == "child_of":
            if None in ids or children is None:
                raise InvalidFilter()
            ids = set(children(sorted(ids)))
        if op in ("in", "=", "child_of"):
            return lambda d, p: p in ids
        return lambda d, p: p not in ids
    raise InvalidFilter()


class TooLarge(Exception):
    """Le résultat ne tient pas dans un nombre fini."""


def fold(values, aggregator):
    """Agrégat des valeurs, comme read_group : somme vide = 0, moyenne, min ou max vides = None.

    Ce qui n'est pas un nombre (case vide, texte d'erreur d'une colonne Grist) est laissé de
    côté, comme le fait un tableur. TooLarge si le résultat n'est pas fini.
    """
    present = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    try:
        if aggregator == "sum":
            result = float(sum(present))
        elif not present:
            return None
        elif aggregator == "avg":
            result = float(sum(present)) / len(present)
        elif aggregator == "min":
            result = float(min(present))
        elif aggregator == "max":
            result = float(max(present))
        else:
            raise ValueError(aggregator)
    except OverflowError:
        raise TooLarge() from None
    if not math.isfinite(result):
        raise TooLarge()
    return result
