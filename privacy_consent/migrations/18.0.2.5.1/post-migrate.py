"""Post-migration: Replace Jinja validity expressions with placeholders.

Odoo 18's HTML sanitizer escapes {{ }} inside elements, so the Jinja
expressions injected by the 2.5.0 migration render as literal text.
Replace them with static placeholders that _send_consent_request_email()
substitutes server-side.
"""
import logging

_logger = logging.getLogger(__name__)

# Jinja expressions to replace (may appear with or without <span> wrapper)
REPLACEMENTS = [
    # FR: the full Jinja expression → placeholder
    (
        "{{ 'pour une durée de %s jours' % object.purpose_id.default_validity_days "
        "if object.purpose_id.default_validity_days "
        "else 'pour une durée indéterminée (sans expiration)' }}",
        "<span>VALIDITY_FR_PLACEHOLDER</span>",
    ),
    # EN: the full Jinja expression → placeholder
    (
        "{{ '%s days' % object.purpose_id.default_validity_days "
        "if object.purpose_id.default_validity_days "
        "else 'an indefinite period (no expiration)' }}",
        "<span>VALIDITY_EN_PLACEHOLDER</span>",
    ),
]


def migrate(cr, version):
    if not version:
        return

    _logger.info(
        "privacy_consent post-migrate 2.5.1: "
        "replacing Jinja validity expressions with placeholders"
    )

    cr.execute(
        """
        SELECT res_id FROM ir_model_data
        WHERE module = 'privacy_consent'
          AND name = 'mail_template_consent_request'
        """
    )
    row = cr.fetchone()
    if not row:
        _logger.warning("  mail_template_consent_request not found, skipping")
        return

    template_id = row[0]

    for lang in ("en_US", "fr_CA"):
        for old_expr, new_expr in REPLACEMENTS:
            cr.execute(
                """
                UPDATE mail_template
                SET body_html = jsonb_set(
                    body_html, %s,
                    to_jsonb(
                        replace(body_html->>%s, %s, %s)
                    )
                )
                WHERE id = %s
                  AND body_html->>%s LIKE %s
                """,
                (
                    [lang], lang, old_expr, new_expr,
                    template_id, lang, f"%{old_expr[:40]}%",
                ),
            )
            if cr.rowcount:
                _logger.info(
                    "  Replaced Jinja expression in %s for template %d",
                    lang, template_id,
                )

    _logger.info("  Done replacing validity expressions")
