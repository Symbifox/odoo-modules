"""Post-migration: Inject Loi 25 compliance note into consent request email.

The template has noupdate="1", so XML changes alone won't update the DB.
This script injects the "Vos droits / Your Rights" section before the
CTA button in both en_US and fr_CA JSONB body_html keys.
"""
import logging

_logger = logging.getLogger(__name__)

# The compliance note HTML to inject before the CTA button.
# Uses Jinja {{ }} for the validity days (rendered at send time).
COMPLIANCE_NOTE_HTML = """<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%%" style="margin:16px 0; border-top:1px solid #e5e7eb; padding-top:16px;">
<tr>
<td style="font-family:'Lexend','Segoe UI',Arial,sans-serif; font-size:11px; color:#6B7280; line-height:18px;">
<strong style="color:#22303B; font-size:12px;">Vos droits / Your Rights</strong>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%%" style="margin-top:8px;">
<tr>
<td style="font-family:'Lexend','Segoe UI',Arial,sans-serif; font-size:11px; color:#6B7280; line-height:18px; padding-bottom:12px;">
&#8226; Ce consentement est valide %(validity_jinja_fr)s \u00e0 compter de votre acceptation.<br/>
&#8226; Vous pouvez refuser sans cons\u00e9quence sur vos services existants.<br/>
&#8226; Vous pouvez retirer votre consentement \u00e0 tout moment en nous \u00e9crivant \u00e0 <a href="mailto:%(privacy_email)s" style="color:#29ABE2; text-decoration:underline;">%(privacy_email)s</a> ou via votre portail de pr\u00e9f\u00e9rences.<br/>
&#8226; Vous avez le droit d'acc\u00e9der \u00e0 vos renseignements personnels et de les rectifier.<br/>
&#8226; Responsable de la protection des renseignements personnels (RPRP)\u00a0: <a href="mailto:%(privacy_email)s" style="color:#29ABE2; text-decoration:underline;">%(privacy_email)s</a><br/>
&#8226; Loi applicable\u00a0: Loi sur la protection des renseignements personnels dans le secteur priv\u00e9 (RLRQ, c.\u00a0P-39.1), modifi\u00e9e par la Loi\u00a025 (2021, c.\u00a025).
</td>
</tr>
<tr>
<td style="font-family:'Lexend','Segoe UI',Arial,sans-serif; font-size:11px; color:#9CA3AF; line-height:18px; font-style:italic;">
&#8226; This consent is valid for %(validity_jinja_en)s from acceptance.<br/>
&#8226; You may refuse without impact on your existing services.<br/>
&#8226; You may withdraw consent at any time: <a href="mailto:%(privacy_email)s" style="color:#29ABE2; text-decoration:underline;">%(privacy_email)s</a><br/>
&#8226; You have the right to access and rectify your personal information.
</td>
</tr>
</table>
</td>
</tr>
</table>"""

# Jinja expressions for validity days (these are the final form inserted into DB)
VALIDITY_JINJA_FR = (
    "{{ 'pour une durée de %s jours' % object.purpose_id.default_validity_days "
    "if object.purpose_id.default_validity_days "
    "else 'pour une durée indéterminée (sans expiration)' }}"
)
VALIDITY_JINJA_EN = (
    "{{ '%s days' % object.purpose_id.default_validity_days "
    "if object.purpose_id.default_validity_days "
    "else 'an indefinite period (no expiration)' }}"
)

# The CTA button anchor text used to locate the insertion point.
# We look for the table containing the CTA button and insert before it.
CTA_MARKER = 'Répondre à cette demande'


def migrate(cr, version):
    if not version:
        return

    _logger.info(
        "privacy_consent post-migrate 2.5.0: "
        "injecting Loi 25 compliance note into consent request email"
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

    # Resolve privacy officer email: config param → company email → skip
    cr.execute(
        """
        SELECT value FROM ir_config_parameter
        WHERE key = 'privacy_consent.privacy_officer_email'
        """
    )
    param = cr.fetchone()
    privacy_email = (param[0] if param and param[0] else "").strip()
    if not privacy_email:
        cr.execute("SELECT email FROM res_company WHERE id = 1")
        comp = cr.fetchone()
        privacy_email = (comp[0] if comp and comp[0] else "").strip()
    if not privacy_email:
        _logger.warning(
            "  No privacy officer email configured "
            "(ir.config_parameter 'privacy_consent.privacy_officer_email' "
            "or main company email), skipping compliance note injection"
        )
        return

    # Build the compliance note with Jinja expressions
    note_html = COMPLIANCE_NOTE_HTML % {
        "validity_jinja_fr": VALIDITY_JINJA_FR,
        "validity_jinja_en": VALIDITY_JINJA_EN,
        "privacy_email": privacy_email,
    }

    # Check if compliance note already exists (idempotent)
    cr.execute(
        """
        SELECT body_html->>'en_US' FROM mail_template WHERE id = %s
        """,
        (template_id,),
    )
    row = cr.fetchone()
    if row and row[0] and 'Vos droits / Your Rights' in row[0]:
        _logger.info("  Compliance note already present, skipping")
        return

    for lang in ("en_US", "fr_CA"):
        # Get current body for this lang
        cr.execute(
            "SELECT body_html->>%s FROM mail_template WHERE id = %s",
            (lang, template_id),
        )
        body_row = cr.fetchone()
        if not body_row or not body_row[0]:
            _logger.warning("  No body_html for %s, skipping", lang)
            continue

        body = body_row[0]

        # Find the CTA button table and insert before it.
        # The CTA button anchor contains "Répondre à cette demande".
        # We look for the <table that precedes it (the CTA table wrapper).
        marker_pos = body.find(CTA_MARKER)
        if marker_pos == -1:
            _logger.warning(
                "  CTA marker not found in %s, skipping", lang
            )
            continue

        # Walk backwards from the marker to find the start of the <table
        search_area = body[:marker_pos]
        table_start = search_area.rfind('<table')
        if table_start == -1:
            _logger.warning(
                "  Could not find CTA <table in %s, skipping", lang
            )
            continue

        # Insert the compliance note right before the CTA table
        new_body = body[:table_start] + note_html + body[table_start:]

        cr.execute(
            """
            UPDATE mail_template
            SET body_html = jsonb_set(body_html, %s, to_jsonb(%s::text))
            WHERE id = %s
            """,
            ([lang], new_body, template_id),
        )
        _logger.info("  Injected compliance note into %s", lang)

    _logger.info("  Done injecting compliance note")
