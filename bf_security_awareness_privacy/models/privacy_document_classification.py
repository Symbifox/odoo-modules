from odoo import models


class PrivacyDocumentClassification(models.Model):
    """Rend les données de sensibilisation classifiables."""

    _inherit = "privacy.document.classification"

    def _privacy_classifiable_models(self):
        """Ajoute les modèles de la sensibilisation aux modèles classifiables.

        On surcharge la méthode plutôt que la constante `ALLOWED_MODELS` :
        la surcharge compose avec celle des autres ponts, alors que
        redéfinir la constante ferait perdre leurs modèles au dernier
        module chargé.
        """
        return super()._privacy_classifiable_models() | {
            "bf.security.profile",
            "bf.phishing.result",
            "bf.training.assignment",
            "bf.reported.phish",
        }
