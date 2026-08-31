from odoo import models


class PrivacyDocumentClassification(models.Model):
    """Rend classifiables les trois modèles porteurs de renseignements personnels."""

    _inherit = "privacy.document.classification"

    def _privacy_classifiable_models(self):
        """On surcharge la méthode, jamais la constante `ALLOWED_MODELS`.

        La surcharge compose avec celle des autres ponts ; redéfinir la
        constante ferait perdre leurs modèles au dernier module chargé.

        ⚠️ `bf.ex.benefit` et `bf.ex.eligibility.rule` restent dehors, et c'est
        voulu. Un avantage est une décision d'entreprise, pas un renseignement
        sur quelqu'un. Les classifier ferait apparaître le catalogue dans les
        campagnes de destruction, où une suppression emporterait par contrainte
        tout l'historique qui s'y rattache.
        """
        return super()._privacy_classifiable_models() | {
            "bf.ex.entitlement",
            "bf.ex.usage",
            "bf.ex.claim",
        }
