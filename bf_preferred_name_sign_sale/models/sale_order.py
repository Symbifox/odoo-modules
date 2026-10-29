"""« Signé par » d'un devis : le nom de l'acte, AVANT la confirmation.

`bf_sign_sale._sign_on_signed` écrit `signed_by` au nom des signataires, puis
confirme la commande, ce qui envoie au client le devis en PDF. Écrire le nom
de l'acte après coup laissait partir un PDF au nom d'usage, que le devis
contredisait ensuite. Le nom de l'acte est
donc posé dans l'écriture même de `signed_by`, avant la confirmation.

La clé de contexte ne lève aucune garde : elle remplace une valeur que
l'appelant peut de toute façon écrire lui-même s'il a le droit d'écrire le
devis.
"""

from odoo import models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def _sign_on_signed(self, request):
        names = [s._bf_act_name() for s in request.signer_ids if s._bf_act_name()]
        if names:
            # Bornée à CETTE commande : la clé suit tout ce que la confirmation
            # crée (livraisons, factures, autres commandes) ; elle ne doit
            # réécrire que le « Signé par » de celle-ci.
            self = self.with_context(bf_act_signed_by={self.id: ", ".join(names)})
        return super(SaleOrder, self)._sign_on_signed(request)

    def write(self, vals):
        noms = self.env.context.get("bf_act_signed_by") or {}
        # Seulement une valeur posée (une remise à zéro reste une remise à
        # zéro), et seulement sur la commande signée.
        if vals.get("signed_by") and len(self) == 1 and self.id in noms:
            vals = dict(vals, signed_by=noms[self.id])
        return super().write(vals)
