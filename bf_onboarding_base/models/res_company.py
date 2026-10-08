from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    # Core brand colours/logo consumed by branded emails, reports and public
    # pages across the bf_* suite (bf_appointment, bf_meeting, bf_hour_bank,
    # bf_sign, …). Defined here — in the shared onboarding base every bf_*
    # module already depends on — so those modules render correctly WITHOUT the
    # optional white-label module `bluefox_branding`. The white-label panel
    # (bluefox_branding) surfaces and styles these fields, but no longer owns
    # them.
    report_brand_primary = fields.Char(
        string="Primary colour (brand)",
        default="#714B67",
        help="Accent colour for the navbar, buttons and branded emails "
             "(banner, bubbles, bars). PDF reports use \"Primary colour "
             "(PDF)\" instead.",
    )
    report_brand_dark = fields.Char(
        string="Dark colour (brand)",
        default="#212529",
        help="Dark background colour for branded email headers and the "
             "navbar. PDF reports use \"Secondary colour (PDF)\" instead.",
    )
    report_brand_logo = fields.Binary(
        string="Logo on dark background (brand)",
        attachment=True,
        help="Logo, ideally white or light, used on DARK backgrounds: "
             "branded email headers and public pages. The company's "
             "standard logo (often in colour) is still used on light "
             "documents. If empty, the standard logo is used everywhere.",
    )
