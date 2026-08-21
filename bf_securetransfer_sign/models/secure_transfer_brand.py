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
        string="Exiger une entente de confidentialité",
        default=False,
        help="Valeur par défaut des nouveaux transferts de cette marque. "
             "Chaque envoi peut la retenir ou l'écarter.",
    )
    nda_document = fields.Binary(
        string="Entente (PDF)", attachment=True,
        help="Le document que chaque visiteur devra signer avant d'accéder au "
             "contenu. Une demande de signature distincte est créée pour "
             "chacun, à son nom.",
    )
    nda_filename = fields.Char(string="Nom du fichier")
    nda_field_template_id = fields.Many2one(
        "bf.sign.field.template",
        string="Gabarit de pavés",
        ondelete="restrict",
        help="Position des pavés de signature sur l'entente. Facultatif : sans "
             "gabarit, la signature est valide et certifiée, mais elle n'est "
             "pas dessinée sur les pages du document.",
    )
    nda_consent_text = fields.Text(
        string="Texte de consentement",
        help="Phrase que le signataire coche avant de signer. Vide = le texte "
             "par défaut de bf_sign.",
    )

    @api.constrains("nda_required", "nda_document")
    def _check_nda_document_present(self):
        """Exiger une entente sans en fournir une bloquerait tous les visiteurs
        devant une porte qui n'a pas de clé."""
        for rec in self:
            if rec.nda_required and not rec.nda_document:
                raise ValidationError(_(
                    "« %s » exige une entente de confidentialité, mais aucun "
                    "document n'est téléversé : les visiteurs resteraient "
                    "bloqués devant une entente inexistante.",
                    rec.display_name,
                ))
