from odoo import models


class PrivacyDocumentClassification(models.Model):
    """Rend les lignes de l'inbox unifiée classifiables."""

    _inherit = "privacy.document.classification"

    def _privacy_classifiable_models(self):
        """Ajoute `bf.email` aux modèles classifiables.

        La destruction de masse porte sur des régimes, pas sur des documents :
        classifier des milliers de courriels un par un n'apprendrait rien, puisque
        chacun porterait la même règle et la même catégorie. Mais un courriel
        PARTICULIER — celui qui porte un renseignement médical, un dossier
        d'employé, une pièce d'identité — mérite sa classification et son
        passage par une campagne de destruction approuvée. C'est ce que ce
        point d'extension rend possible.

        On surcharge la méthode plutôt que la constante `ALLOWED_MODELS` : la
        surcharge compose avec celle des autres ponts, alors que redéfinir la
        constante ferait perdre leurs modèles au dernier module chargé.
        """
        return super()._privacy_classifiable_models() | {"bf.email"}
