from odoo import models


class PrivacyDocumentClassification(models.Model):
    """Rend les rencontres classifiables."""

    _inherit = "privacy.document.classification"

    def _privacy_classifiable_models(self):
        """Ajoute les modèles de rencontre aux modèles classifiables.

        On surcharge la méthode plutôt que la constante `ALLOWED_MODELS` :
        la surcharge compose avec celle des autres ponts, alors que
        redéfinir la constante ferait perdre leurs modèles au dernier
        module chargé.

        ⚠️ Seuls les deux modèles de tête sont ouverts. `meeting.attendance`,
        `meeting.decision` et `meeting.topic` portent eux aussi des
        renseignements personnels — une présence nomme quelqu'un et dit s'il
        était là, une décision nomme son décideur — mais ils sont rattachés à
        leur parent en `ondelete='cascade'` et n'ont aucun cycle de vie propre.
        Les classifier séparément fabriquerait des classifications orphelines
        dès la destruction du parent : la campagne les balaierait ensuite comme
        des documents à détruire alors qu'ils n'existent plus, et le registre
        certifierait des destructions déjà faites. Une classification sur le
        compte rendu couvre ses lignes.
        """
        return super()._privacy_classifiable_models() | {
            "meeting.record",
            "meeting.agenda",
        }
