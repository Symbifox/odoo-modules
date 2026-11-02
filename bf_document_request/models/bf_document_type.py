from odoo import fields, models


class BfDocumentType(models.Model):
    """The catalogue of documents an organisation can request.

    The employee does not type a free-form name: they tick documents in a
    catalogue. Without it, a per-document retention would have nothing to hang
    on, hence the direct link to ``bf.document.expiry.policy``.
    """

    _name = "bf.document.type"
    _description = "Requested document type"
    _order = "sequence, name, id"

    name = fields.Char(
        string="Document",
        required=True,
        translate=True,
        help="The label the client sees on their phone, e.g. "
        "\"Identity document\".",
    )
    code = fields.Char(
        string="Code",
        help="A short, stable identifier, e.g. \"identity\". Used by "
        "integrations; never shown to the client.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    instructions = fields.Text(
        string="Instructions to the client",
        translate=True,
        help="Shown under the document name on the mobile page, e.g. "
        "\"Front and back, lying flat, without glare\".",
    )
    two_sided = fields.Boolean(
        string="Two-sided",
        help="Marks a document that has two sides, such as a driver's licence. "
        "The page asks for one file per document: say in the instructions "
        "how to show both sides (both in one photo, or a PDF).",
    )
    expiry_policy_id = fields.Many2one(
        "bf.document.expiry.policy",
        string="Retention",
        help="How long the document lives once uploaded. Empty = the brand's "
        "policy applies.",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
    )

    _sql_constraints = [
        (
            "code_uniq",
            "UNIQUE(code, company_id)",
            "This document code already exists for this company.",
        ),
    ]
