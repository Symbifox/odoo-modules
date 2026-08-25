from odoo import models


class PrivacyConsent(models.Model):
    _name = "privacy.consent"
    _inherit = ["privacy.consent", "bf.sign.mixin"]

    def _sign_report_ref(self):
        # The consent notice, rendered by this module.
        #
        # NOT ``privacy_consent.action_report_consent_certificate``: that report
        # is bound to the ``privacy.consent.evidence`` model, not to
        # ``privacy.consent``. Handing it a consent id makes it look for an
        # evidence record carrying the same id, and the render raises
        # MissingError. On the substance, a certificate attests to something
        # already done, which is not what a person is asked to sign.
        return "bf_sign_privacy.action_report_consent_form"

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
