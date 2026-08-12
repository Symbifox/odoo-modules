from odoo import models


class PrivacyConsent(models.Model):
    _name = "privacy.consent"
    _inherit = ["privacy.consent", "bf.sign.mixin"]

    def _sign_report_ref(self):
        # Consent certificate PDF (the consent artifact to be signed).
        return "privacy_consent.action_report_consent_certificate"

    def _sign_default_signers(self):
        # privacy.consent tracks the subject under ``subject_partner_id`` rather
        # than the generic ``partner_id`` the mixin looks for, so prefill it.
        self.ensure_one()
        partner = self.subject_partner_id
        if partner:
            return [{
                "name": partner.name, "email": partner.email,
                "partner_id": partner.id,
            }]
        return []
