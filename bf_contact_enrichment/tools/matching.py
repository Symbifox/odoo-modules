"""Fuzzy partner-matching helpers for contact dedup.

Pure-stdlib; safe to import inside the Odoo container. Used to find an
existing ``res.partner`` before creating a new one (card / vCard / signature
flows) and by the duplicate-detector wizard.
"""
import re
import unicodedata

_NOISE_WORDS = frozenset({
    "inc", "ltd", "ltée", "ltee", "enr", "corp", "pbc", "sa", "srl",
    "services", "service", "solutions", "solution", "consulting", "consultants",
    "groupe", "group", "technologies", "technology", "tech",
    "canada", "québec", "quebec", "international", "global", "digital",
    "and", "et", "the", "les", "des", "du", "de", "la", "le",
})

_LEGAL_SUFFIXES = re.compile(
    r"\b(Inc\.?|Ltd\.?|PBC|S\.?E\.?N\.?C\.?|S\.?A\.?|Ltée|Ltee|Enr\.?|"
    r"Corp\.?|S\.?R\.?L\.?|L\.?L\.?C\.?|Co\.?|Cie\.?|Limitée|Limited)\b",
    re.IGNORECASE,
)

# Free / consumer email providers — a shared domain here means nothing.
GENERIC_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.fr", "hotmail.ca",
    "outlook.com", "outlook.fr", "live.com", "live.ca", "msn.com",
    "yahoo.com", "yahoo.fr", "yahoo.ca", "ymail.com",
    "icloud.com", "me.com", "mac.com",
    "videotron.ca", "sympatico.ca", "bell.net", "globetrotter.net",
    "proton.me", "protonmail.com",
})


def normalize(text):
    """Strip accents, lowercase, trim — for fuzzy comparison."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_text.lower().strip()


def significant_words(name):
    """Distinctive words (3+ chars, not noise/legal) from a name."""
    if not name:
        return []
    clean = _LEGAL_SUFFIXES.sub("", name)
    clean = re.sub(r"[.,;:!?/\\()\[\]{}\"']", " ", clean)
    words = normalize(clean).split()
    return [w for w in words if len(w) >= 3 and w not in _NOISE_WORDS]


def extract_domain(email_or_url):
    """Domain from an email or URL, or None."""
    if not email_or_url:
        return None
    val = email_or_url.strip()
    if "@" in val:
        return val.split("@")[-1].lower().strip()
    domain = re.sub(r"^https?://", "", val).split("/")[0].lower().strip()
    return domain if "." in domain else None


def find_partner_match(env, name=None, email=None, company=None):
    """Find the existing ``res.partner`` most likely to be name/email.

    Conservative: prefers precise signals (exact email, exact name,
    corporate-domain + a shared distinctive name token). Returns a partner
    record or ``False``. Never does loose single-token name matching — for
    contacts, a false merge is worse than a duplicate.
    """
    Partner = env["res.partner"]

    # Step 0: exact email (strongest signal)
    if email and "@" in email:
        p = Partner.search([("email", "=ilike", email.strip())], limit=1)
        if p:
            return p

    # Step 1: exact (case-insensitive) full name
    if name and name.strip():
        p = Partner.search([("name", "=ilike", name.strip())], limit=1)
        if p:
            return p

    # Step 2: corporate email domain + a shared distinctive name token
    domain = extract_domain(email)
    if domain and domain not in GENERIC_DOMAINS:
        candidates = Partner.search([("email", "=ilike", "%@" + domain)], limit=30)
        if candidates and name:
            toks = set(significant_words(name))
            if toks:
                for c in candidates:
                    if toks & set(significant_words(c.name or "")):
                        return c

    # Step 3: company match (card's company → an existing company partner)
    if company and company.strip():
        sig = significant_words(company)
        if len(sig) >= 2:
            p = Partner.search(
                [("is_company", "=", True), ("name", "ilike", " ".join(sig[:2]))],
                limit=1,
            )
            if p:
                return p

    return False


def find_duplicate_groups(env, limit=200):
    """Scan ``res.partner`` for likely duplicates.

    Returns a list of dicts ``{"key", "reason", "partner_ids"}``. Groups by
    exact email and by normalized significant-words name. Name-only groups
    already fully covered by an email group are skipped.
    """
    partners = env["res.partner"].search_read(
        [("active", "=", True)],
        ["id", "name", "email"],
        limit=limit * 20,
    )

    by_email = {}
    by_name = {}
    for p in partners:
        email = (p.get("email") or "").strip().lower()
        if email and "@" in email:
            by_email.setdefault(email, []).append(p["id"])
        sig = significant_words(p.get("name") or "")
        if len(sig) >= 2:
            key = " ".join(sorted(sig))
            by_name.setdefault(key, []).append(p["id"])

    groups = []
    seen = set()
    for email, ids in by_email.items():
        if len(ids) > 1:
            groups.append({"key": email, "reason": "Même courriel", "partner_ids": ids})
            seen.update(ids)
    for key, ids in by_name.items():
        if len(ids) > 1:
            fresh = [i for i in ids if i not in seen]
            if len(fresh) > 1:
                groups.append({"key": key, "reason": "Même nom", "partner_ids": ids})
    return groups[:limit]
