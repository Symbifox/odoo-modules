from odoo import fields, models


class BfDocumentType(models.Model):
    """Has each document type declare the category of personal information it
    carries.

    Without this field, the register entry written when an identity document
    is destroyed would say "not classified, categories unknown": true of an
    ordinary transfer, but false here, since what was requested is known
    exactly (it was requested from a catalogue).

    Left empty on purpose for the types an organisation creates itself: a
    register that says "category not declared" is better than one that
    guesses.
    """

    _inherit = "bf.document.type"

    privacy_pi_category = fields.Selection(
        selection=lambda self: self.env[
            "privacy.document.classification"
        ]._fields["pi_category"]._description_selection(self.env),
        string="Personal information category",
        help="The category of personal information this document carries. "
        "Carried as is into the destruction register. Empty = not declared; "
        "the register will say so rather than guess.",
    )
