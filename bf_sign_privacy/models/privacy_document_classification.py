from odoo import models


class PrivacyDocumentClassification(models.Model):
    """Rend les demandes de signature maison classifiables."""

    _inherit = "privacy.document.classification"

    def _privacy_classifiable_models(self):
        """Ajoute les modèles de bf_sign aux modèles classifiables.

        La liste du socle nommait `sign.request` / `sign.request.item`, les
        modèles d'Odoo Enterprise, absents de nos locataires. Le modèle
        maison, lui, est `bf.sign.request`.
        """
        return super()._privacy_classifiable_models() | {
            "bf.sign.request",
            "bf.sign.signer",
        }
