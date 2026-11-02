from odoo import models


class PrivacyDocumentClassification(models.Model):
    """Rend la messagerie classifiable."""

    _inherit = "privacy.document.classification"

    def _privacy_classifiable_models(self):
        """Ajoute les trois modèles de la messagerie.

        La destruction de masse porte sur des régimes, pas sur des documents :
        classifier des milliers de messages un par un n'apprendrait rien, chacun
        portant la même règle. Mais un message PARTICULIER — celui qui porte
        une pièce d'identité photographiée, un renseignement médical — mérite
        sa classification et son passage par une campagne approuvée.

        On surcharge la méthode plutôt que la constante `ALLOWED_MODELS` : la
        surcharge compose avec celle des autres ponts, alors que redéfinir la
        constante ferait perdre leurs modèles au dernier module chargé.
        """
        return super()._privacy_classifiable_models() | {
            "sms.archive.message",
            "sms.archive.mms.part",
            "call.archive.call",
        }
