from odoo import fields, models

from .letter_template import LETTERHEAD_MODES


class ResCompany(models.Model):
    _inherit = "res.company"

    letter_signatory_id = fields.Many2one(
        "res.partner",
        string="Signataire par défaut",
        help="Personne dont le nom et la fonction apparaissent dans le bloc "
        "de signature des lettres.",
    )
    letter_signatory_function = fields.Char(
        string="Fonction du signataire",
        help="Titre affiché sous le nom du signataire. Si vide, la fonction "
        "du contact est utilisée.",
    )
    letter_signature_image = fields.Binary(
        string="Image de signature",
        attachment=True,
        help="Signature numérisée insérée au-dessus du nom du signataire.",
    )
    letter_header_note = fields.Char(
        string="Mention d'en-tête",
        help="Courte mention affichée sous le nom de l'entreprise dans "
        "l'en-tête (ex. slogan, numéro d'entreprise).",
    )
    letter_footer_html = fields.Html(
        string="Pied de page des lettres",
        sanitize=False,
        help="Contenu du pied de page. Si vide, le nom et l'adresse de "
        "l'entreprise sont utilisés.",
    )

    # --- « Utiliser mon propre en-tête » ---------------------------------
    letter_default_mode = fields.Selection(
        LETTERHEAD_MODES,
        string="En-tête par défaut",
        default="banner",
        help="Style d'en-tête appliqué par défaut aux nouvelles lettres.",
    )
    letterhead_image = fields.Binary(
        string="Image d'en-tête (PNG/JPG)",
        attachment=True,
        help="Image pleine page utilisée comme fond en mode « Image téléversée ».",
    )
    letterhead_image_filename = fields.Char()
    letterhead_pdf = fields.Binary(
        string="PDF d'en-tête",
        attachment=True,
        help="PDF d'en-tête sur lequel le corps de la lettre est superposé en "
        "mode « PDF téléversé ».",
    )
    letterhead_pdf_filename = fields.Char()
    letter_body_top_margin = fields.Integer(
        string="Marge haute du corps (mm)",
        default=45,
        help="Décalage du corps depuis le haut de la page pour les modes sans "
        "en-tête généré (image, PDF, papier pré-imprimé).",
    )
    letter_body_bottom_margin = fields.Integer(
        string="Marge basse du corps (mm)",
        default=25,
        help="Espace réservé en bas de page pour les modes sans en-tête généré.",
    )
