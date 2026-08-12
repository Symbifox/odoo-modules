from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    report_brand_primary = fields.Char(
        related="company_id.report_brand_primary",
        readonly=False,
    )
    report_brand_dark = fields.Char(
        related="company_id.report_brand_dark",
        readonly=False,
    )
    report_brand_logo = fields.Binary(
        related="company_id.report_brand_logo",
        readonly=False,
    )
    # Native Odoo per-company colors — these (not report_brand_*) drive PDF
    # report headers/accents via the standard document layout. Surfaced here so
    # the whole brand identity lives in one panel.
    primary_color = fields.Char(
        related="company_id.primary_color",
        readonly=False,
    )
    secondary_color = fields.Char(
        related="company_id.secondary_color",
        readonly=False,
    )
    brand_email_tagline = fields.Char(
        related="company_id.brand_email_tagline",
        readonly=False,
    )
    brand_email_footer_html = fields.Html(
        related="company_id.brand_email_footer_html",
        readonly=False,
        sanitize=False,
    )
    brand_email_signature_default = fields.Html(
        related="company_id.brand_email_signature_default",
        readonly=False,
        sanitize=False,
    )
    brand_privacy_url = fields.Char(
        related="company_id.brand_privacy_url",
        readonly=False,
    )
    brand_terms_url = fields.Char(
        related="company_id.brand_terms_url",
        readonly=False,
    )
    company_font = fields.Selection(
        related="company_id.font",
        readonly=False,
    )
    company_logo = fields.Binary(
        related="company_id.logo",
        readonly=False,
    )
    company_favicon = fields.Binary(
        related="company_id.favicon",
        readonly=False,
    )
    company_report_header = fields.Html(
        related="company_id.report_header",
        readonly=False,
        sanitize=False,
    )
    company_website = fields.Char(
        related="company_id.website",
        readonly=False,
    )
