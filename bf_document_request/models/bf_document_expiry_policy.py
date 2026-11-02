from odoo import fields, models


class BfDocumentExpiryPolicy(models.Model):
    _name = "bf.document.expiry.policy"
    _description = "Expiry policy (role / document type)"
    _order = "sequence, retention_days, id"

    name = fields.Char(
        string="Name",
        required=True,
        translate=True,
        help="A readable label, e.g. \"Short (24 h)\" or \"Long (30 days)\".",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    retention_days = fields.Integer(
        string="Retention (days)",
        required=True,
        default=7,
        help="How long the link lives before it expires automatically. "
        "1 = 24 h, 7 = one week, 30 = 30 days, depending on what the brand "
        "offers; any other duration makes the sending fail.",
    )
    doc_type = fields.Char(
        string="Document type",
        help="An indicative label for the kind of document this policy covers.",
    )
    note = fields.Text(string="Notes")
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
    )

    _sql_constraints = [
        (
            "retention_positive",
            "CHECK(retention_days > 0)",
            "Retention must be at least 1 day.",
        ),
    ]
