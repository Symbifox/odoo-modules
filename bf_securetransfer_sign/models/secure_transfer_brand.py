"""L'entente de confidentialité par défaut d'une marque.

La marque porte le gabarit — c'est le cas d'usage réel : une entreprise a UNE
entente, qu'elle fait signer à tous ceux qui entrent dans ses salles de
données. Un transfert peut la remplacer par la sienne quand un dossier
particulier l'exige.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SecureTransferBrand(models.Model):
    _inherit = "secure.transfer.brand"

    nda_required = fields.Boolean(
        string="Require an NDA",
        default=False,
        help="Default value for new transfers of this brand. Each sending "
             "can keep it or drop it.",
    )
    nda_document = fields.Binary(
        string="NDA (PDF)", attachment=True,
        help="The document each visitor must sign before accessing the "
             "content. A separate signature request is created for each "
             "of them, in their name.",
    )
    nda_filename = fields.Char(string="File name")
    nda_field_template_id = fields.Many2one(
        "bf.sign.field.template",
        string="Signature field template",
        ondelete="restrict",
        help="Position of the signature fields on the NDA. Optional: "
             "without a template, the signature is valid and certified, "
             "but it is not drawn on the document's pages.",
    )
    nda_consent_text = fields.Text(
        string="Consent text",
        help="Sentence the signer checks before signing. Empty = the "
             "bf_sign default text.",
    )

    @api.constrains("nda_required", "nda_document")
    def _check_nda_document_present(self):
        """Exiger une entente sans en fournir une bloquerait tous les visiteurs
        devant une porte qui n'a pas de clé."""
        for rec in self:
            if rec.nda_required and not rec.nda_document:
                raise ValidationError(_(
                    "\"%s\" requires an NDA, but no document is uploaded: "
                    "visitors would be stuck in front of an NDA that does "
                    "not exist.",
                    rec.display_name,
                ))
