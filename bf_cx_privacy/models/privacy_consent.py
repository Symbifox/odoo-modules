"""Withdrawal propagation.

The notice sent to the client promises the testimonial will be pulled if
consent is withdrawn - that promise must not depend on someone remembering
to click "Vérifier le consentement". Any consent leaving 'granted' (or a
pending one refused) retires the linked testimonials automatically. Covers
the expiry cron too (it writes status='expired').
"""
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class PrivacyConsent(models.Model):
    _inherit = "privacy.consent"

    def write(self, vals):
        res = super().write(vals)
        if vals.get("status") in ("refused", "withdrawn", "expired"):
            testimonials = (
                self.env["bf.cx.testimonial"]
                .sudo()
                .search(
                    [
                        ("privacy_consent_id", "in", self.ids),
                        (
                            "state",
                            "in",
                            ("consent_pending", "consented", "published"),
                        ),
                    ]
                )
            )
            if testimonials:
                testimonials._bf_cx_consent_lost(vals["status"])
        return res
