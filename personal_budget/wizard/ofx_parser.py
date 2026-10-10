"""Dependency-free OFX/QFX parser for the personal_budget import wizard.

Pure Python — no ORM, no third-party deps — so it runs inside the Odoo
container and can be unit-tested on the host. Handles both OFX 1.x SGML
(unclosed leaf tags, the format some Canadian banks export) and OFX 2.x XML.

Public API:
    parse_ofx(raw: bytes) -> list[dict]
        Each dict is a statement:
            {
                'kind': 'bank' | 'creditcard',
                'acctid': str,
                'accttype': str,
                'currency': str,
                'transactions': [
                    {'trntype': str, 'date': datetime.date,
                     'amount': float (signed), 'fitid': str,
                     'name': str, 'memo': str},
                    ...
                ],
                'warnings': [str, ...],
            }
"""

import re
from datetime import date

try:
    # Messages traduits paresseusement : la langue est celle de la personne
    # qui lit le rapport, résolue au moment de l'affichage dans l'assistant.
    from odoo.tools.translate import LazyTranslate
    _lt = LazyTranslate(__name__)
except ImportError:  # hors d'Odoo (essais sur l'hôte) : la source anglaise
    def _lt(source, *args, **kwargs):
        return source % (args or kwargs) if (args or kwargs) else source

# Tokenizer: matches an opening or closing tag plus any text that follows it
# up to the next '<'. Works for SGML (leaf tags never closed) and XML alike.
_TAG_RE = re.compile(r"<(/?)([A-Za-z0-9_.]+)>([^<]*)")

# SGML entities that may appear in OFX values.
_ENTITIES = {
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&apos;": "'",
    "&quot;": '"',
}

# Aggregates we care about (push/pop on the stack). Any other aggregate is
# still pushed/popped generically so the stack stays balanced.
_TXN_TAG = "STMTTRN"
_BANK_ACCT_TAGS = ("BANKACCTFROM", "CCACCTFROM")
_STMT_TAGS = {"STMTRS": "bank", "CCSTMTRS": "creditcard"}

# NAME/MEMO substrings (upper-cased) that mark a credit-card payment or an
# inter-account transfer. Tuned after inspecting real OFX / QFX exports from a Canadian bank.
# Kept here as the single source of truth so the wizard imports it.
SKIP_PAYMENT_PATTERNS = (
    "PAIEMENT",
    "PAYMENT",
    "MERCI",
    "THANK YOU",
)
SKIP_TRANSFER_PATTERNS = (
    "VIREMENT",
    "TRANSFER",
    "TRANSFERT",
)


def _decode(raw):
    """Decode OFX bytes, tolerating the cp1252 some banks emit."""
    if isinstance(raw, str):
        return raw
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _unescape(val):
    if "&" not in val:
        return val
    for ent, char in _ENTITIES.items():
        val = val.replace(ent, char)
    return val


def _parse_date(val):
    """DTPOSTED → datetime.date. Format: YYYYMMDD[HHMMSS][.XXX][TZ]."""
    m = re.match(r"\s*(\d{4})(\d{2})(\d{2})", val)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _parse_amount(val):
    """TRNAMT → signed float. Tolerates comma decimals."""
    val = val.strip().replace(",", ".")
    try:
        return float(val)
    except ValueError:
        return None


def parse_ofx(raw):
    """Parse OFX/QFX bytes into a list of statement dicts. See module docstring."""
    text = _decode(raw)

    # Strip the OFX 1.x "OFXHEADER:100" key:value preamble or the <?xml ...?>
    # declaration by cutting to the first <OFX> aggregate.
    lower = text.lower()
    start = lower.find("<ofx>")
    if start == -1:
        raise ValueError(_lt("Invalid OFX file: <OFX> tag not found."))
    text = text[start:]

    statements = []
    stack = []  # list of open aggregate tag names (upper-cased)
    cur_stmt = None
    cur_txn = None

    for mo in _TAG_RE.finditer(text):
        closing, tag, value = mo.group(1), mo.group(2).upper(), mo.group(3)
        value = _unescape(value.strip())

        if closing:
            # Pop only if it matches the top of the stack. In OFX 1.x SGML the
            # leaves are never closed (so we never see their close tags), while
            # aggregates ARE closed; in OFX 2.x XML the leaf close tags arrive
            # but find no matching aggregate on the stack and are ignored.
            if stack and stack[-1] == tag:
                stack.pop()
                if tag in _STMT_TAGS:
                    if cur_stmt is not None:
                        statements.append(cur_stmt)
                    cur_stmt = None
                elif tag == _TXN_TAG:
                    if cur_stmt is not None and cur_txn is not None:
                        _finalize_txn(cur_stmt, cur_txn)
                    cur_txn = None
            continue

        if not value:
            # Aggregate open.
            stack.append(tag)
            if tag in _STMT_TAGS:
                cur_stmt = {
                    "kind": _STMT_TAGS[tag],
                    "acctid": "",
                    "accttype": "",
                    "currency": "",
                    "transactions": [],
                    "warnings": [],
                }
            elif tag == _TXN_TAG and cur_stmt is not None:
                cur_txn = {
                    "trntype": "",
                    "date": None,
                    "amount": None,
                    "fitid": "",
                    "name": "",
                    "memo": "",
                }
            continue

        # Leaf element with a value.
        if cur_stmt is None:
            continue
        in_acct = bool(stack) and stack[-1] in _BANK_ACCT_TAGS

        if cur_txn is not None:
            if tag == "TRNTYPE":
                cur_txn["trntype"] = value.upper()
            elif tag == "DTPOSTED":
                cur_txn["date"] = _parse_date(value)
            elif tag == "TRNAMT":
                cur_txn["amount"] = _parse_amount(value)
            elif tag == "FITID":
                cur_txn["fitid"] = value
            elif tag == "NAME":
                cur_txn["name"] = value
            elif tag == "MEMO":
                cur_txn["memo"] = value
        elif in_acct:
            if tag == "ACCTID":
                cur_stmt["acctid"] = value
            elif tag == "ACCTTYPE":
                cur_stmt["accttype"] = value.upper()
        elif tag == "CURDEF" and not cur_stmt["currency"]:
            cur_stmt["currency"] = value.upper()

    # Close any dangling statement (malformed file missing a close tag).
    if cur_stmt is not None and cur_stmt not in statements:
        statements.append(cur_stmt)

    if not statements:
        raise ValueError(_lt("OFX file without a usable statement (no STMTRS/CCSTMTRS)."))

    return statements


def _finalize_txn(stmt, txn):
    """Validate a transaction and append it, or record a warning."""
    if not txn["fitid"]:
        stmt["warnings"].append(_lt(
            "Transaction without FITID skipped (date %(date)s, amount %(amount)s).",
            date=txn["date"], amount=txn["amount"],
        ))
        return
    if txn["date"] is None:
        stmt["warnings"].append(_lt("Transaction FITID %s without a valid date.", txn["fitid"]))
        return
    if txn["amount"] is None:
        stmt["warnings"].append(_lt("Transaction FITID %s without a valid amount.", txn["fitid"]))
        return
    stmt["transactions"].append(txn)
