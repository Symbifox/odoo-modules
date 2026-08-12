from odoo import fields, models


class LetterQuicktext(models.Model):
    _name = "letter.quicktext"
    _description = "Bloc de texte réutilisable"
    _order = "category, sequence, name"

    name = fields.Char(string="Nom", required=True)
    shortcut = fields.Char(
        string="Raccourci",
        help="Code court (ex. ::relance) pour retrouver rapidement le bloc.",
    )
    category = fields.Char(string="Catégorie")
    body_html = fields.Html(
        string="Contenu",
        sanitize=False,
        help="Peut contenir des champs de fusion, ex. {{ object.partner_id.name }}.",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Société",
        help="Laisser vide pour partager le bloc entre toutes les sociétés.",
    )
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
