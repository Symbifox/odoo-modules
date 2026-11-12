from odoo import models


class PrivacyDocumentClassification(models.Model):
    """Makes the route stop and the position notice classifiable."""

    _inherit = "privacy.document.classification"

    def _privacy_classifiable_models(self):
        """Adds the three models of the module that carry personal information.

        Mass destruction bears on a regime, not on documents: classifying each stop would
        teach nobody anything, since they all carry ROU-001. But a PARTICULAR stop deserves
        its classification, the one whose note says why the customer was away, for instance.

        The notice as read (`bf.route.notice.ack`) is the proof of the prior information of
        s. 8.1: it is classifiable, but it does not fall under ROU-001 and it is not erased
        with the positions. It outlives the position it made lawful, without which there
        would be no way left to show the person had been informed.

        The method is overridden rather than the `ALLOWED_MODELS` constant: an override
        composes with the other bridges', whereas redefining the constant would make the
        last module loaded lose theirs.

        The route day itself came with ROU-002: it carries the kilometres measured from the
        readings, which is the piece of the kilometre register that rule covers. A rule whose
        model cannot be classified is a rule nobody can attach to a record.
        """
        return super()._privacy_classifiable_models() | {
            "bf.route.day",
            "bf.route.day.stop",
            "bf.route.notice.ack",
        }
