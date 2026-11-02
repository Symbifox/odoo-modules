"""18.0.1.2.2: the two delivered expiry policies lose their dash.

"Short — 24 h" becomes "Short (24 h)" and "Long — 30 days" "Long (30 days)",
in every language that still holds the delivered text. A name someone has
changed is left alone, language by language: the records are ``noupdate`` and
belong to the organisation."""
import json
import logging

_logger = logging.getLogger(__name__)

RENAMES = {
    "policy_short_24h": {"Short — 24 h": "Short (24 h)", "Courte — 24 h": "Courte (24 h)"},
    "policy_long_30d": {"Long — 30 days": "Long (30 days)", "Longue — 30 jours": "Longue (30 jours)"},
}


def migrate(cr, version):
    renamed = 0
    for xmlid, renames in RENAMES.items():
        cr.execute("""
            SELECT p.id, p.name FROM bf_document_expiry_policy p
              JOIN ir_model_data d ON d.res_id = p.id AND d.model = 'bf.document.expiry.policy'
             WHERE d.module = 'bf_document_request' AND d.name = %s""", (xmlid,))
        row = cr.fetchone()
        if not row or not isinstance(row[1], dict):
            continue
        policy_id, names = row
        new = {lang: renames.get(value, value) for lang, value in names.items()}
        if new != names:
            cr.execute("UPDATE bf_document_expiry_policy SET name = %s::jsonb WHERE id = %s",
                       (json.dumps(new), policy_id))
            renamed += 1
    _logger.info("bf_document_request: %d delivered expiry polic(ies) renamed", renamed)
