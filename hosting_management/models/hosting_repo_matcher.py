# License LGPL-3.0 or later.
"""Auto-match `hosting.backup.repository` records to `hosting.service` records.

Tenants typically name backup repos and services according to a predictable
convention. For each repo the matcher extracts a `(software, client)` pair
and links services whose name produces the same pair.

Configuration is fully driven by ``ir.config_parameter`` so the module ships
brand-agnostic. Out of the box the matcher links nothing — tenants seed
their own conventions:

    hosting.repo_match_bucket_prefixes
        Pipe-separated ``<prefix>=<client>`` entries. A repo whose name
        starts with ``<prefix>`` resolves to client ``<client>`` and the
        remainder of the path becomes the software identifier. Example:
        ``internal backups/=internal|tenant backups/=mytenant``.

    hosting.repo_match_clients_prefix
        Prefix preceding a ``<client>/<software>`` path. Default:
        ``clients - backups/``.

    hosting.repo_match_client_synonyms
    hosting.repo_match_software_synonyms
        Pipe-separated ``<alias>:<canonical>`` entries used to fold
        spelling variants (accents, abbreviations, marketing names) onto a
        single canonical token. Both keys are normalized before lookup.

Shared-container cases (one Vaultwarden, one PrivateBin serving many
domains) require a manual link on the service form — this module never
removes existing links.
"""

import logging
import re
import unicodedata

_logger = logging.getLogger(__name__)


_SOFTWARE_KEYWORDS = (
    "cryptpad",
    "privatebin",
    "docuseal",
    "cubebackup",
    "vaultwarden",
    "nextcloud",
    "mastodon",
    "authentik",
    "rustdesk",
    "humhub",
    "onlyoffice",
    "backrest",
    "calcom",
    "migadu",
    "shlink",
    "hostinger",
    "odoo",
    "grist",
    "ntfy",
    "n8n",
)


def _normalize(text):
    if not text:
        return ""
    n = unicodedata.normalize("NFKD", text)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = n.lower()
    n = re.sub(r"[^a-z0-9]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def _parse_synonyms_param(raw):
    """Parse ``alias1:canonical1|alias2:canonical2`` into a dict."""
    out = {}
    if not raw:
        return out
    for pair in raw.split("|"):
        if ":" in pair:
            alias, canon = pair.split(":", 1)
            alias_n = _normalize(alias)
            canon_n = _normalize(canon)
            if alias_n and canon_n:
                out[alias_n] = canon_n
    return out


def _parse_bucket_prefixes(raw):
    """Parse ``prefix1=client1|prefix2=client2`` into a list of (prefix, client)."""
    out = []
    if not raw:
        return out
    for pair in raw.split("|"):
        if "=" in pair:
            prefix, client = pair.split("=", 1)
            prefix_n = prefix.lower().strip()
            client_n = _normalize(client)
            if prefix_n and client_n:
                out.append((prefix_n, client_n))
    return out


def _resolve(raw, synonyms):
    raw = _normalize(raw)
    if not raw:
        return None
    if raw in synonyms:
        return synonyms[raw]
    for alias in sorted(synonyms.keys(), key=len, reverse=True):
        if alias and alias in raw:
            return synonyms[alias]
    return raw


def _resolve_software(raw, sw_synonyms):
    n = _normalize(raw)
    if not n:
        return None
    if n in sw_synonyms:
        return sw_synonyms[n]
    if "cal com" in n:
        return sw_synonyms.get("calcom", "calcom")
    for kw in _SOFTWARE_KEYWORDS:
        if kw in n.split() or kw in n:
            return kw
    return None


def parse_service(name, client_synonyms, sw_synonyms):
    """Return ``(software, client)`` from a service name, or ``(None, None)``."""
    n = _normalize(name)
    if not n:
        return None, None
    sw = _resolve_software(n, sw_synonyms)
    parts = re.split(r"\s+-\s+|\s+\|\s+", name, maxsplit=1)
    cl_raw = parts[1] if len(parts) == 2 else n
    cl_raw = re.sub(r"\(.*?\)", "", cl_raw).strip()
    if sw:
        cl_raw = cl_raw.replace(sw, "").strip()
    cl = _resolve(cl_raw, client_synonyms) if cl_raw else None
    if not cl and len(parts) == 1:
        cl = _resolve(n.replace(sw or "", ""), client_synonyms)
    return sw, cl


def parse_repo(name, client_synonyms, sw_synonyms, bucket_prefixes, clients_prefix):
    """Return ``(software, client)`` from a repo name, or ``(None, None)``."""
    n = name.lower().strip()
    sw_raw = None
    cl_raw = None
    for prefix, client_token in bucket_prefixes:
        if n.startswith(prefix):
            path = n[len(prefix):]
            if "/" in path:
                sub, sw_raw = path.split("/", 1)
                cl_raw = sub
            else:
                sw_raw = path
                cl_raw = client_token
            sw_raw = re.sub(r"\s*\(.*?\)\s*", "", sw_raw)
            return _resolve_software(sw_raw, sw_synonyms), _resolve(
                cl_raw, client_synonyms
            )
    if clients_prefix and n.startswith(clients_prefix):
        rest = n[len(clients_prefix):]
        if "/" in rest:
            cl_raw, sw_raw = rest.split("/", 1)
            return _resolve_software(sw_raw, sw_synonyms), _resolve(
                cl_raw, client_synonyms
            )
    return None, None


def get_config(env):
    """Read matcher configuration from ``ir.config_parameter``."""
    Param = env["ir.config_parameter"].sudo()
    return {
        "client_synonyms": _parse_synonyms_param(
            Param.get_param("hosting.repo_match_client_synonyms") or ""
        ),
        "sw_synonyms": _parse_synonyms_param(
            Param.get_param("hosting.repo_match_software_synonyms") or ""
        ),
        "bucket_prefixes": _parse_bucket_prefixes(
            Param.get_param("hosting.repo_match_bucket_prefixes") or ""
        ),
        "clients_prefix": (
            Param.get_param("hosting.repo_match_clients_prefix") or ""
        ).lower().strip(),
    }
