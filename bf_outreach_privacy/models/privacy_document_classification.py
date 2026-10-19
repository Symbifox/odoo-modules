from odoo import models


class PrivacyDocumentClassification(models.Model):
    """Rend le démarchage classifiable."""

    _inherit = "privacy.document.classification"

    def _privacy_classifiable_models(self):
        """Ajoute la cible et l'interaction.

        La destruction de masse passe par les règles DEM-001 et DEM-002. Une
        cible PARTICULIÈRE (une note qui consigne un renseignement de santé
        confié au téléphone, par exemple) mérite sa classification et son
        passage par une campagne approuvée.

        On surcharge la méthode plutôt que la constante `ALLOWED_MODELS` : la
        surcharge compose avec celle des autres ponts.
        """
        return super()._privacy_classifiable_models() | {
            "bf.outreach.target",
            "bf.outreach.touch",
        }
