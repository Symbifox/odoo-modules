from odoo import fields, models


class BfTrainingLegalReference(models.Model):
    """Une base réglementaire, citée au long.

    Une exigence qui renvoie à « la loi » ne sert à personne le jour de
    l'inspection. Ce catalogue porte l'intitulé complet, le chapitre, l'article
    et le texte, pour qu'une exigence puisse s'y rattacher au lieu de le
    recopier de mémoire.
    """

    _name = "bf.training.legal.reference"
    _description = "Training regulatory basis"
    _order = "sequence, name"

    name = fields.Char(string="Title", required=True, translate=True)
    code = fields.Char(string="Code", required=True)
    citation = fields.Char(
        string="Reference", required=True,
        help="The chapter and the section, as they are cited.")
    article = fields.Char(string="Section")
    text = fields.Text(string="Quoted text", translate=True)
    url = fields.Char(string="Address of the official text")
    sector = fields.Selection(
        [("general", "All employers"),
         ("childcare", "Educational childcare"),
         ("seniors", "Private seniors' residences"),
         ("construction", "Construction")],
        string="Sector", default="general", required=True)
    sequence = fields.Integer(string="Sequence", default=10)
    active = fields.Boolean(string="Active", default=True)
    requirement_ids = fields.One2many(
        "bf.training.requirement", "legal_reference_id", string="Requirements")

    _sql_constraints = [
        ("code_uniq", "unique (code)", "This reference code is already taken."),
    ]
