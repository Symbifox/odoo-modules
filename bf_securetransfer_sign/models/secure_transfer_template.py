"""L'entente de confidentialité dans un préréglage de salle de données.

Le socle ne connaît pas ``bf_sign`` : c'est tout l'objet du pont. Le
préréglage suit la même règle que le reste — le champ vit ici, et il rejoint
l'assistant d'envoi par le point d'extension que le socle a prévu
(``secure.transfer.template._apply_vals``), sans que l'onchange du socle ait à
savoir qu'une entente existe.
"""
from odoo import fields, models


class SecureTransferTemplate(models.Model):
    _inherit = "secure.transfer.template"

    nda_required = fields.Boolean(
        string="Exiger la signature d'une entente",
        help="Chaque visiteur signera l'entente à son nom, après avoir "
             "confirmé son identité par code et avant de voir le contenu. "
             "L'entente elle-même vient de la marque.",
    )

    def _apply_vals(self):
        vals = super()._apply_vals()
        vals["nda_required"] = self.nda_required
        return vals
