from odoo import fields, models


class BfTrainingLegalReference(models.Model):
    """Une base réglementaire, citée au long.

    Une exigence qui renvoie à « la loi » ne sert à personne le jour de
    l'inspection. Ce catalogue porte l'intitulé complet, le chapitre, l'article
    et le texte, pour qu'une exigence puisse s'y rattacher au lieu de le
    recopier de mémoire.
    """

    _name = "bf.training.legal.reference"
    _description = "Base réglementaire de formation"
    _order = "sequence, name"

    name = fields.Char(string="Intitulé", required=True, translate=True)
    code = fields.Char(string="Code", required=True)
    citation = fields.Char(
        string="Référence", required=True,
        help="Le chapitre et l'article, tels qu'ils se citent.")
    article = fields.Char(string="Article")
    text = fields.Text(string="Texte cité", translate=True)
    url = fields.Char(string="Adresse du texte officiel")
    sector = fields.Selection(
        [("general", "Tous les employeurs"),
         ("childcare", "Services de garde éducatifs"),
         ("seniors", "Résidences privées pour aînés"),
         ("construction", "Construction")],
        string="Secteur", default="general", required=True)
    sequence = fields.Integer(string="Ordre", default=10)
    active = fields.Boolean(string="Actif", default=True)
    requirement_ids = fields.One2many(
        "bf.training.requirement", "legal_reference_id", string="Exigences")

    _sql_constraints = [
        ("code_uniq", "unique (code)", "Ce code de référence est déjà pris."),
    ]
