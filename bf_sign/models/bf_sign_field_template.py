from odoo import api, fields, models


class BfSignFieldTemplate(models.Model):
    """A reusable layout of signature pads.

    Saves the placement of pads for a document so the same layout can be applied
    to future requests for that document. Pads reference a signer by **rank**
    (signer_index: 0 = first signer, 1 = second…) rather than a specific signer,
    so the template is reusable across requests with different signers.
    """

    _name = "bf.sign.field.template"
    _description = "Modèle de disposition des pavés"
    _order = "name"

    name = fields.Char(string="Nom", required=True)
    company_id = fields.Many2one(
        "res.company", string="Société", default=lambda self: self.env.company)
    line_ids = fields.One2many(
        "bf.sign.field.template.line", "template_id", string="Pavés", copy=True)
    field_count = fields.Integer(compute="_compute_counts", string="Pavés")
    signer_count = fields.Integer(compute="_compute_counts", string="Signataires")
    active = fields.Boolean(default=True)

    @api.depends("line_ids", "line_ids.signer_index")
    def _compute_counts(self):
        for rec in self:
            rec.field_count = len(rec.line_ids)
            rec.signer_count = (
                max(rec.line_ids.mapped("signer_index")) + 1) if rec.line_ids else 0


class BfSignFieldTemplateLine(models.Model):
    _name = "bf.sign.field.template.line"
    _description = "Pavé d'un modèle de disposition"
    _order = "signer_index, page, sequence, id"

    template_id = fields.Many2one(
        "bf.sign.field.template", required=True, ondelete="cascade", index=True)
    signer_index = fields.Integer(
        string="Rang du signataire", default=0,
        help="0 = premier signataire, 1 = deuxième, etc.")
    field_type = fields.Selection(
        selection=[
            ("signature", "Signature"),
            ("initials", "Paraphe"),
            ("date", "Date"),
            ("text", "Texte"),
            ("name", "Nom"),
            ("email", "Courriel"),
            ("number", "Nombre"),
            ("checkbox", "Case à cocher"),
        ],
        string="Type", default="signature", required=True)
    page = fields.Integer(string="Page", default=1, required=True)
    pos_x = fields.Float(default=0.60)
    pos_y = fields.Float(default=0.80)
    width = fields.Float(default=0.25)
    height = fields.Float(default=0.08)
    fill_mode = fields.Selection(
        selection=[
            ("auto", "Automatique (signataire / date de signature)"),
            ("fixed", "Valeur fixe (préparateur)"),
            ("signer", "Rempli par le signataire"),
        ],
        string="Mode de remplissage", default="signer", required=True)
    required = fields.Boolean(string="Obligatoire", default=True)
    value_text = fields.Char(string="Valeur fixe / étiquette")
    sequence = fields.Integer(default=10)
