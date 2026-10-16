"""The XML of a Charlemagne transmission: structures, checks and writing.

Charlemagne is the Ministry of Education's system for the student headcount and the sanction
of studies. A school sends one XML file (a "commande") holding requests (demandes) that hold
transactions, encoded in ISO-8859-1, encrypted and signed, dropped by SFTP. The structures
below come from the Ministry's teletransmission guide (P560C, version 1.1 of 2026-09-21).

This module knows nothing of Odoo: it takes plain dicts and returns bytes, so it can be
tested against the guide line by line.

What it checks on its own, before anything leaves: that a required element is there, that a
value fits the length and kind the guide gives it, that a date is a date, that the characters
exist in Latin-1, that no unknown element sneaks in. What it cannot check is what the Ministry
checks with its own tables (codes of languages, programs, municipalities, the 2,500 lines of
section 7.2.3.3): a transaction passing here can still be refused there, and the answer says why.
"""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

#: ISO-8859-1 cannot hold these: they are replaced by their usual ASCII look-alike rather than
#: refused, the way a word processor's smart quotes would be.
LATIN1_REPLACEMENTS = {
    "‘": "'", "’": "'", "‚": ",", "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "…": "...", "œ": "oe", "Œ": "OE", " ": " ",
    " ": " ", " ": " ", "€": "EUR",
}

DATE_FORMAT = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DIGITS = re.compile(r"^\d+$")

#: Format-error message numbers of the guide: 610101 for the declarations, 600801 for results.
FORMAT_ERROR = {"DJ": "CHA-610101", "DA": "CHA-610101", "DP": "CHA-610101", "DS": "CHA-610101",
                "SA": "CHA-610101", "SP": "CHA-610101"}
DEFAULT_FORMAT_ERROR = "CHA-600801"


@dataclass(frozen=True)
class E:
    """A data element: kind A (alphanumeric), N (numeric) or D (date aaaa-mm-jj)."""

    tag: str
    kind: str
    length: int | None
    required: bool


@dataclass(frozen=True)
class S:
    """A structure of elements, once or repeated."""

    tag: str
    required: bool
    repeat: bool
    children: list


@dataclass(frozen=True)
class Transaction:
    """The root structure of a system transaction and what it holds, in the order the guide requires."""

    root_tag: str
    children: list


def to_latin1(value):
    """The text as ISO-8859-1 can carry it; None when a character has no place there."""
    text = "".join(LATIN1_REPLACEMENTS.get(c, c) for c in str(value))
    try:
        text.encode("latin-1")
    except UnicodeEncodeError:
        return None
    return text


def _is_empty(value):
    return value is None or value is False or (isinstance(value, str) and not value.strip())


#: The messages of the local checks, by key. The parameters are in the error; the application translates them.
MESSAGES = {
    "missing": "The element %(tag)s is required.",
    "missing_structure": "The structure %(tag)s is required.",
    "unknown": "The element %(tag)s is not defined for this transaction.",
    "latin1": "The value %(value)s for %(tag)s holds a character ISO-8859-1 cannot carry.",
    "once": "The structure %(tag)s appears once at most.",
    "format": "The value %(value)s for the element %(tag)s does not respect the expected format: %(format)s.",
}
KIND_NAMES = {"A": "alphanumeric", "N": "numeric", "D": "date yyyy-mm-dd"}


def error(number, tag, key, **params):
    """(number, tag, text, key, params): the text in English for whoever reads it raw, the key and the parameters
    for the application to say it in the reader's language."""
    params["tag"] = tag
    return (number, tag, MESSAGES[key] % params, key, params)


def _check_value(element, value, errors, format_code):
    """The value as it will be written, or None; errors are appended."""
    text = str(value).strip()
    if element.kind == "D":
        ok = bool(DATE_FORMAT.match(text))
        if ok:
            try:
                date.fromisoformat(text)
            except ValueError:
                ok = False
    elif element.kind == "N":
        ok = bool(DIGITS.match(text)) and (element.length is None or len(text) <= element.length)
    else:
        converted = to_latin1(text)
        if converted is None:
            errors.append(error("LOCAL-004", element.tag, "latin1", value=text))
            return None
        text = converted
        ok = element.length is None or len(text) <= element.length
    if not ok:
        errors.append(error(format_code, element.tag, "format", value=text, format="%s%s" % (
            KIND_NAMES[element.kind], " (%s)" % element.length if element.length else "")))
        return None
    return text


def build_structure(children, data, parent, errors, format_code, path="", blank=False):
    """Append the elements of `data` under `parent` in the guide's order; collect the errors.

    With `blank`, an optional element whose value is the empty string is written as an empty tag: that is how the guide
    takes an element off a result (section 6.29). Without it, an empty value is an absent element.
    """
    data = data or {}
    known = {child.tag for child in children}
    for tag in data:
        if tag not in known:
            errors.append(error("LOCAL-003", tag, "unknown"))
    for child in children:
        value = data.get(child.tag)
        if isinstance(child, S):
            items = value if isinstance(value, list) else ([value] if value else [])
            items = [item for item in items if item]
            if child.required and not items:
                errors.append(error("LOCAL-001", child.tag, "missing_structure"))
            if len(items) > 1 and not child.repeat:
                errors.append(error("LOCAL-005", child.tag, "once"))
            for item in items:
                node = ET.SubElement(parent, child.tag)
                build_structure(child.children, item, node, errors, format_code, path + child.tag + "/", blank)
            continue
        if blank and isinstance(value, str) and value == "" and not child.required:
            ET.SubElement(parent, child.tag)
            continue
        if _is_empty(value):
            if child.required:
                errors.append(error("LOCAL-001", child.tag, "missing"))
            continue
        text = _check_value(child, value, errors, format_code)
        if text is not None:
            ET.SubElement(parent, child.tag).text = text


#: The transactions in which an empty tag takes an element off (guide 6.29: "transmettre une étiquette vide"): the
#: modification of an exam result. Elsewhere an empty value is simply not written.
CLEARING = {("UJ", "MOD")}


def build_transaction(spec, data, kind_type=None, code=None):
    """(xml element of the parameters, errors) for one system transaction."""
    errors = []
    root = ET.Element(spec.root_tag)
    format_code = FORMAT_ERROR.get(kind_type, DEFAULT_FORMAT_ERROR)
    build_structure(spec.children, data, root, errors, format_code, blank=(kind_type, code) in CLEARING)
    return root, errors


def _text(parent, tag, value):
    ET.SubElement(parent, tag).text = value if value is not None else ""


def build_command(transmitter_code, report_format, demands, destination=""):
    """The bytes of a transmission file (ISO-8859-1), from its requests.

    `demands`: list of {"service": "TRA" or "FIC", "organism": code of the school the data belong to,
    "type": "DJ"/"RJ"/"IJ"/"UJ"/"FI", "lot": {"nolot": ..., "responsible": ...},
    "transactions": [{"code": "CRE", "id": ..., "organism": ..., "root": element from build_transaction}]}.
    """
    if len(demands) > 999:
        raise ValueError("A transmission holds 999 requests at most (ECH-209101).")
    command = ET.Element("commande")
    _text(command, "sigleSysteme", "Charlemagne")
    _text(command, "codeOrganismeTransmetteur", transmitter_code)
    _text(command, "formatDiffusionElectroniqueRapport", report_format)
    _text(command, "destination", destination)
    for demand in demands:
        node = ET.SubElement(command, "demande", {"codeTransactionDemandeService": demand["service"]})
        if demand.get("organism"):
            _text(node, "codeOrganismeTransmis", demand["organism"])
        _text(node, "typeTransaction", demand["type"])
        _text(node, "modeTraitementMaj", "P")
        _text(node, "nbTransactionsSystemeTransmises", str(len(demand["transactions"])))
        other = ET.SubElement(node, "autresParametresDemandeService")
        lot = ET.SubElement(other, "lot")
        _text(lot, "nolot", str(demand["lot"].get("nolot", "")))
        if demand["lot"].get("responsible"):
            responsible = to_latin1(demand["lot"]["responsible"]) or "Responsable"
            _text(lot, "nomResponsableDonneesTransmises", responsible)
        for transaction in demand["transactions"]:
            tx = ET.SubElement(node, "transactionSysteme", {
                "codeTransactionSysteme": transaction["code"], "idTransaction": transaction["id"][:80]})
            if transaction.get("organism"):
                _text(tx, "codeOrganismeTransmis", transaction["organism"])
            parameters = ET.SubElement(tx, "parametreTransaction")
            parameters.append(transaction["root"])
    ET.indent(command, space=" ")
    body = ET.tostring(command, encoding="unicode")
    return ("<?xml version=\"1.0\" encoding=\"ISO-8859-1\"?>\n" + body + "\n").encode("latin-1", errors="xmlcharrefreplace")
