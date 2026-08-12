"""Post-migration: Replace Jinja expressions in email template with placeholder.

Odoo 18's HTML sanitizer escapes {{ }} inside <div> elements during
send_mail() and also strips HTML comments.  The server-side
_send_consent_request_email() method injects the notice body by replacing
a plain-text placeholder (<span>NOTICE_BODY_PLACEHOLDER</span>) after
template rendering.
"""
import logging

_logger = logging.getLogger(__name__)

NEW_PLACEHOLDER = "<span>NOTICE_BODY_PLACEHOLDER</span>"

# Patterns to replace (old → new) in both locale keys
REPLACEMENTS = [
    # HTML comment placeholder (stripped by sanitizer — didn't work)
    ("<!--NOTICE_BODY_PLACEHOLDER-->", NEW_PLACEHOLDER),
    # The computed field expression that was escaped by the sanitizer
    ("{{ object.email_notice_body or '' }}", NEW_PLACEHOLDER),
    # Older variant with notice_version_id fallback
    (
        "{{ object.notice_version_id.body if object.notice_version_id"
        " else object.purpose_id.plain_language_summary or '' }}",
        NEW_PLACEHOLDER,
    ),
]


def migrate(cr, version):
    if not version:
        return

    _logger.info(
        "privacy_consent post-migrate 2.4.0: "
        "replacing Jinja expressions with placeholder in email template"
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
                    template_id, lang, f"%{old_expr}%",
                ),
            )
            if cr.rowcount:
                _logger.info(
                    "  Replaced expression in %s for template %d",
                    lang, template_id,
                )

    _logger.info("  Done updating email template")
