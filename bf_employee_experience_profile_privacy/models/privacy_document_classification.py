from odoo import models


class PrivacyDocumentClassification(models.Model):
    _inherit = "privacy.document.classification"

    def _privacy_classifiable_models(self):
        """⚠️ Le catalogue reste dehors.

        `bf.ex.profile.framework` et `bf.ex.profile.type` décrivent un
        référentiel, pas quelqu'un : les classifier ferait croire qu'un
        référentiel se détruit. Seule la DECLARATION est personnelle, et ses
        lignes de score partent avec elle.
        """
        return super()._privacy_classifiable_models() | {"bf.ex.profile"}
