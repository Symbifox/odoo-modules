from odoo import models


class PrivacyDocumentClassification(models.Model):
    """Makes secure transfers classifiable."""

    _inherit = "privacy.document.classification"

    def _privacy_classifiable_models(self):
        """Adds the secure transfer models to the classifiable models.

        The method is overridden rather than the `ALLOWED_MODELS` constant: the
        override composes with those of the other bridges, whereas redefining
        the constant would drop their models in favour of the last module
        loaded.
        """
        return super()._privacy_classifiable_models() | {
            "secure.transfer",
            "secure.transfer.file",
        }
