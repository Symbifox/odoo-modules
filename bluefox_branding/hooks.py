import logging
import os

from lxml import etree

_logger = logging.getLogger(__name__)

# ─── Blue Fox branded "Avis de retard sur facture" body ──────────────────
# Template 141 has no XML ID (created manually in UI), so we update by name+model
_LATE_INVOICE_BODY = """\
<body style="margin:0;padding:0;background-color:#F8FAFC;font-family:'Lexend','Segoe UI',Arial,sans-serif;">
<t t-set="company" t-value="object.company_id or user.company_id"/>
<t t-set="brand_primary" t-value="(company and company.report_brand_primary) or '#714B67'"/>
<t t-set="brand_dark" t-value="(company and company.report_brand_dark) or '#212529'"/>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
       style="background-color:#F8FAFC;">
<tbody><tr><td align="center" style="padding:32px 16px;">

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600"
       style="width:600px;max-width:600px;margin:0 auto;background-color:#ffffff;border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,0.08);">
<tbody>

<tr>
<td t-attf-style="background-color:{{ brand_dark }};padding:20px 32px;border-radius:16px 16px 0 0;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody><tr>
<td align="left">
<a t-att-href="company.website or '#'" style="text-decoration:none;">
<img t-attf-src="/web/image/res.company/{{ company.id }}/logo"
     t-att-alt="company.name" style="height:44px;width:auto;display:block;border:0;" height="44"/>
</a>
</td>
<td align="right" style="color:#E6EDF3;font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:14px;font-weight:400;letter-spacing:0.3px;">
<t t-out="company.name"/>
</td>
</tr></tbody></table>
</td>
</tr>

<tr><td t-attf-style="height:4px;line-height:4px;background-color:{{ brand_primary }};">&#160;</td></tr>

<tr>
<td style="padding:28px 32px;font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:15px;line-height:24px;color:#374151;font-weight:300;">

<p style="margin:0 0 16px 0;">Bonjour <t t-out="object.partner_id.name"/>,</p>

<p style="margin:0 0 16px 0;">
Nous souhaitons porter &#224; votre attention que la facture
<strong t-attf-style="color:{{ brand_dark }};"><t t-out="object.name"/></strong>
d'un montant de
<strong t-attf-style="color:{{ brand_dark }};"><t t-out="format_amount(object.amount_total, object.currency_id)"/></strong>
est actuellement en retard de paiement.
</p>

<p style="margin:0 0 16px 0;">
La date d'&#233;ch&#233;ance &#233;tait le
<strong t-attf-style="color:{{ brand_dark }};"><t t-out="format_date(object.invoice_date_due)"/></strong>.
</p>

<p style="margin:0 0 16px 0;">
Nous vous prions de bien vouloir proc&#233;der au r&#232;glement dans les meilleurs d&#233;lais.
Si le paiement a d&#233;j&#224; &#233;t&#233; effectu&#233;, veuillez ne pas tenir compte de cet avis.
</p>

<p style="margin:0 0 8px 0;">
Pour toute question, n'h&#233;sitez pas &#224; nous contacter.
</p>

<p style="margin:24px 0 0 0;font-size:14px;">
Cordialement,<br/>
<strong t-attf-style="color:{{ brand_dark }};">L'&#233;quipe <t t-out="company.name"/></strong>
</p>

</td>
</tr>

<tr>
<td style="padding:0 32px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tbody><tr><td style="height:1px;line-height:1px;background-color:#E5E7EB;">&#160;</td></tr></tbody>
</table>
</td>
</tr>

<tr>
<td style="padding:24px 32px 28px 32px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tbody>
<tr>
<td style="font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:13px;color:#6B7280;line-height:20px;">
<t t-set="brand_tagline" t-value="(company.brand_email_tagline or company.report_header) or ''"/>
<strong t-attf-style="color:{{ brand_dark }};font-size:14px;"><t t-out="company.name"/></strong>
<t t-if="brand_tagline"><br/><span t-out="brand_tagline"/></t>
</td>
</tr>
<tr>
<td style="padding-top:12px;font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:12px;color:#9CA3AF;line-height:18px;">
<t t-if="not is_html_empty(company.brand_email_footer_html)" t-out="company.brand_email_footer_html"/>
<t t-else="">
<a t-if="company.email" t-attf-href="mailto:{{ company.email }}" t-attf-style="color:{{ brand_primary }};text-decoration:none;" t-out="company.email"/>
<t t-if="company.email and company.phone"><span style="color:#D1D5DB;"> &#183; </span></t>
<a t-if="company.phone" t-attf-href="tel:{{ company.phone }}" t-attf-style="color:{{ brand_primary }};text-decoration:none;" t-out="company.phone"/>
<t t-if="(company.email or company.phone) and company.website"><span style="color:#D1D5DB;"> &#183; </span></t>
<a t-if="company.website" t-att-href="company.website" t-attf-style="color:{{ brand_primary }};text-decoration:none;" t-out="company.website"/>
</t>
</td>
</tr>
<tr t-if="company.brand_privacy_url or company.brand_terms_url">
<td style="padding-top:12px;font-family:'Lexend','Segoe UI',Arial,sans-serif;font-size:11px;">
<a t-if="company.brand_privacy_url" t-att-href="company.brand_privacy_url" style="color:#9CA3AF;text-decoration:underline;">Confidentialit&#233;</a>
<t t-if="company.brand_privacy_url and company.brand_terms_url"><span style="color:#D1D5DB;"> | </span></t>
<a t-if="company.brand_terms_url" t-att-href="company.brand_terms_url" style="color:#9CA3AF;text-decoration:underline;">Conditions</a>
</td>
</tr>
</tbody></table>
</td>
</tr>

</tbody>
</table>

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600"
       style="width:600px;max-width:600px;margin:12px auto 0;">
<tbody><tr>
<td t-attf-style="height:3px;line-height:3px;background-color:{{ brand_primary }};width:50%;border-radius:2px 0 0 2px;">&#160;</td>
<td t-attf-style="height:3px;line-height:3px;background-color:{{ brand_dark }};width:50%;border-radius:0 2px 2px 0;">&#160;</td>
</tr></tbody>
</table>

</td></tr></tbody>
</table>
</body>"""


def _extract_templates_from_xml():
    """Parse mail_template_overrides.xml and extract field values per record XML ID.

    Returns dict: {xml_id: {field_name: value, ...}}
    """
    xml_path = os.path.join(os.path.dirname(__file__), 'data', 'mail_template_overrides.xml')
    if not os.path.exists(xml_path):
        _logger.warning("bluefox_branding: %s not found", xml_path)
        return {}

    tree = etree.parse(xml_path)
    result = {}

    for record in tree.xpath('//record[@model="mail.template"]'):
        xml_id = record.get('id')
        if not xml_id:
            continue

        fields = {}
        for field in record.findall('field'):
            fname = field.get('name')
            ftype = field.get('type')
            if ftype == 'html':
                # Serialize child elements as HTML string
                inner = ''.join(
                    etree.tostring(child, encoding='unicode', method='html')
                    for child in field
                )
                if not inner:
                    inner = field.text or ''
                fields[fname] = inner
            else:
                fields[fname] = field.text or ''

        result[xml_id] = fields

    return result


def _get_active_langs(env):
    """Return list of active language codes."""
    langs = env['res.lang'].search([]).mapped('code')
    if not langs:
        langs = ['en_US']
    return langs


def post_init_hook(env):
    """Apply Blue Fox branding to all mail templates.

    Handles two categories:
    1. Templates with XML IDs (noupdate=True) — read from mail_template_overrides.xml
    2. Template 141 (no XML ID) — hardcoded body

    Writes in ALL active languages so JSONB translated fields (body_html, name,
    subject) are updated for every language key, not just en_US.
    """
    _logger.info("bluefox_branding: post_init_hook — applying Blue Fox email branding")

    active_langs = _get_active_langs(env)
    _logger.info("bluefox_branding: Active languages: %s", active_langs)

    # ── Category 1: Templates with XML IDs (noupdate=True in their origin module) ──
    templates_data = _extract_templates_from_xml()
    updated = 0

    for xml_id, fields in templates_data.items():
        tmpl = env.ref(xml_id, raise_if_not_found=False)
        if not tmpl:
            _logger.warning("bluefox_branding: Template %s not found — skipping", xml_id)
            continue

        for lang in active_langs:
            tmpl.with_context(lang=lang).write(fields)
        updated += 1
        _logger.info("bluefox_branding: Updated %s (ID %s) in %d languages — %s",
                      xml_id, tmpl.id, len(active_langs),
                      fields.get('name', '(no name change)'))

    _logger.info("bluefox_branding: Updated %d templates from XML overrides", updated)

    # ── Category 2: Template 141 — no XML ID, find by name + model ──
    late_templates = env['mail.template'].search([
        ('model', '=', 'account.move'),
        ('name', 'ilike', 'Avis de retard'),
    ])

    for tmpl in late_templates:
        for lang in active_langs:
            tmpl.with_context(lang=lang).body_html = _LATE_INVOICE_BODY
        _logger.info("bluefox_branding: Updated '%s' (ID %s) in %d languages",
                      tmpl.name, tmpl.id, len(active_langs))

    if not late_templates:
        _logger.warning("bluefox_branding: Template 'Avis de retard' not found — skipping")

    _logger.info("bluefox_branding: post_init_hook complete")
