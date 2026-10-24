from odoo import models


class PrivacyDocumentClassification(models.Model):
    """Rend le persona, ses indicateurs et ses règles de copie classifiables."""

    _inherit = "privacy.document.classification"

    def _privacy_classifiable_models(self):
        """Ajoute les trois modèles de bf_persona.

        La destruction de masse passe par la règle PER-001. Un persona
        PARTICULIER (des détails personnels qui consignent un renseignement de
        santé confié au détour d'un courriel, par exemple), ou un indicateur
        tiré de la comptabilité, mérite sa classification et son passage par
        une campagne approuvée.

        On surcharge la méthode plutôt que la constante `ALLOWED_MODELS` : la
        surcharge compose avec celle des autres ponts.
        """
        return super()._privacy_classifiable_models() | {
            "contact.persona",
            "contact.persona.kpi",
            "contact.cc.rule",
        }
