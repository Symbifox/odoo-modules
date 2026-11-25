from odoo import api, fields, models


class BfTrainingRequirement(models.Model):
    _inherit = "bf.training.requirement"

    legal_reference_id = fields.Many2one(
        "bf.training.legal.reference", string="Catalogue reference",
        help="Takes the title, the citation and the text, rather than "
             "copying them from memory.")

    @api.onchange("legal_reference_id")
    def _onchange_legal_reference_id(self):
        for rec in self:
            reference = rec.legal_reference_id
            if not reference:
                continue
            rec.legal_basis = "%s, %s" % (reference.name, reference.article) \
                if reference.article else reference.name
            rec.legal_reference = reference.citation
            rec.legal_text = reference.text
