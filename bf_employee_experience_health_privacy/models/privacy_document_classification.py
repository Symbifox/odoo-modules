from odoo import models


class PrivacyDocumentClassification(models.Model):
    _inherit = "privacy.document.classification"

    def _privacy_classifiable_models(self):
        """⚠️ `bf.ex.allergen` reste dehors : c'est un catalogue d'allergènes,
        pas un renseignement sur quelqu'un. Seule la DÉCLARATION est
        personnelle."""
        return super()._privacy_classifiable_models() | {"bf.ex.allergy"}
