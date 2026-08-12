# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import fields, models


class ResCompany(models.Model):
    """Charity identity carried onto every official donation receipt."""

    _inherit = "res.company"

    charity_registration_number = fields.Char(
        string="Numéro d'enregistrement (ARC)",
        help="Numéro d'enregistrement d'organisme de bienfaisance de l'ARC, "
        "format BN/RR — ex. « 123456789 RR 0001 ».",
    )
    charity_signatory_name = fields.Char(string="Signataire autorisé")
    charity_signatory_title = fields.Char(string="Titre du signataire")
    charity_signature = fields.Image(
        string="Signature", max_width=1024, max_height=512
    )
    receipt_place_of_issue = fields.Char(string="Lieu de délivrance")
    receipt_show_place_of_issue = fields.Boolean(
        string="Afficher le lieu de délivrance",
        default=False,
        help="La modernisation ARC 2024 rend le lieu de délivrance facultatif.",
    )
    receipt_show_appraiser = fields.Boolean(
        string="Afficher l'évaluateur (dons en nature)", default=True
    )
    charity_cra_url = fields.Char(
        string="Adresse ARC (organismes de bienfaisance)",
        default="canada.ca/organismesdebienfaisance",
    )
