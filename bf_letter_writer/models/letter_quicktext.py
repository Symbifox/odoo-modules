from odoo import fields, models


class LetterQuicktext(models.Model):
    _name = "letter.quicktext"
    _description = "Reusable text block"
    _order = "category, sequence, name"

    name = fields.Char(string="Name", required=True)
    shortcut = fields.Char(
        string="Shortcut",
        help="Short code (e.g. ::followup) to find the block quickly.",
    )
    category = fields.Char(string="Category")
    body_html = fields.Html(
        string="Content",
        # Nettoyé comme un modèle de courriel d'Odoo : les balises QWeb (<t t-out>)
        # et les styles restent, le script et les « on… » partent.
        sanitize="email_outgoing",
        help="Can contain merge fields, e.g. {{ object.partner_id.name }}.",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        help="Leave empty to share the block across all companies.",
    )
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
