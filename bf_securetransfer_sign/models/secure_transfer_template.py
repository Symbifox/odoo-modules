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
        string="Require an NDA signature",
        help="Each visitor will sign the NDA in their own name, after "
             "confirming their identity with a code and before seeing the "
             "content. The NDA itself comes from the brand.",
    )

    def _apply_vals(self):
        vals = super()._apply_vals()
        vals["nda_required"] = self.nda_required
        return vals
