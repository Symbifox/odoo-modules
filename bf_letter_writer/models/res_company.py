from odoo import fields, models

from .letter_template import LETTERHEAD_MODES


class ResCompany(models.Model):
    _inherit = "res.company"

    letter_signatory_id = fields.Many2one(
        "res.partner",
        string="Default signatory",
        help="Person whose name and title appear in the signature block "
             "of letters.",
    )
    letter_signatory_function = fields.Char(
        string="Signatory title",
        help="Title shown under the signatory's name. If empty, the "
             "contact's job position is used.",
    )
    letter_signature_image = fields.Binary(
        string="Signature image",
        attachment=True,
        help="Scanned signature inserted above the signatory's name.",
    )
    letter_header_note = fields.Char(
        string="Letterhead note",
        help="Short note shown under the company name in the letterhead "
             "(e.g. tagline, business number).",
    )
    letter_footer_html = fields.Html(
        string="Letter footer",
        sanitize=False,
        help="Footer content. If empty, the company name and address are used.",
    )

    # --- « Utiliser mon propre en-tête » ---------------------------------
    letter_default_mode = fields.Selection(
        LETTERHEAD_MODES,
        string="Default letterhead",
        default="banner",
        help="Letterhead style applied by default to new letters.",
    )
    letterhead_image = fields.Binary(
        string="Letterhead image (PNG/JPG)",
        attachment=True,
        help="Full-page image used as the background in \"Uploaded "
             "image\" mode.",
    )
    letterhead_image_filename = fields.Char()
    letterhead_pdf = fields.Binary(
        string="Letterhead PDF",
        attachment=True,
        help="Letterhead PDF onto which the letter body is overlaid in "
             "\"Uploaded PDF\" mode.",
    )
    letterhead_pdf_filename = fields.Char()
    letter_body_top_margin = fields.Integer(
        string="Body top margin (mm)",
        default=45,
        help="Body offset from the top of the page for modes without a "
             "generated letterhead (image, PDF, pre-printed paper).",
    )
    letter_body_bottom_margin = fields.Integer(
        string="Body bottom margin (mm)",
        default=25,
        help="Space kept at the bottom of the page for modes without a "
             "generated letterhead.",
    )
