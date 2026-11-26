"""Formal Loi 25 consent flow for testimonials (privacy_consent bridge).

Adds the 'privacy' consent mode (selection_add - the core only offers
verbal/written), the request/check buttons, and hard locks: a testimonial in
privacy mode can only be declared consented or published with a GRANTED
privacy.consent linked.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.bf_cx.models.langue import langue_de

_logger = logging.getLogger(__name__)


class BfCxTestimonial(models.Model):
    _inherit = "bf.cx.testimonial"

    consent_mode = fields.Selection(
        selection_add=[("privacy", "Privacy module (Law 25)")],
        ondelete={"privacy": "set null"},
    )
    privacy_consent_id = fields.Many2one(
        "privacy.consent",
        string="Consent (Law 25)",
        ondelete="set null",
        copy=False,
        tracking=True,
    )
    privacy_consent_status = fields.Selection(
        related="privacy_consent_id.status",
        string="Consent status",
        readonly=True,
    )

    def action_request_privacy_consent(self):
        """Create and email a formal privacy.consent request."""
        notice = self.env.ref(
            "bf_cx_privacy.notice_testimonial", raise_if_not_found=False
        )
        if not notice:
            raise UserError(
                _("The consent notice \"Use of your testimonial\" cannot "
                  "be found.")
            )
        for rec in self:
            if not rec.partner_id.email:
                raise UserError(
                    _("%s has no email address - cannot send the consent "
                      "request.")
                    % rec.partner_id.display_name
                )
            if rec.privacy_consent_id and rec.privacy_consent_id.status in (
                "pending",
                "granted",
            ):
                raise UserError(
                    _("A consent request already exists for this "
                      "testimonial (status: %s).")
                    % rec._bf_cx_status_label(rec.privacy_consent_id.status)
                )
            consent = self.env["privacy.consent"].create(
                {
                    "subject_partner_id": rec.partner_id.id,
                    "notice_id": notice.id,
                    "status": "pending",
                    "requested_at": fields.Datetime.now(),
                    "collection_method": "email",
                    "project_id": rec.project_id.id,
                    "company_id": rec.company_id.id,
                }
            )
            consent._send_consent_request_email()
            rec.write(
                {
                    "privacy_consent_id": consent.id,
                    "consent_mode": "privacy",
                    "state": "consent_pending",
                }
            )
            rec.message_post(
                body=_("Law 25 consent request sent to %s.")
                % rec.partner_id.display_name
            )
        return True

    def action_check_privacy_consent(self):
        """Sync the testimonial state with the consent record."""
        for rec in self:
            consent = rec.privacy_consent_id
            if not consent:
                raise UserError(_("No linked consent request."))
            if consent.status == "granted":
                rec.write(
                    {
                        "state": "consented",
                        "consent_date": fields.Date.context_today(rec),
                        "consent_note": rec.consent_note
                        or _("privacy.consent #%s") % consent.id,
                    }
                )
            elif consent.status in ("refused", "withdrawn", "expired"):
                rec._bf_cx_consent_lost(consent.status)
        return True

    def action_set_consented(self):
        """In privacy mode, the proof IS the granted consent record."""
        for rec in self.filtered(lambda r: r.consent_mode == "privacy"):
            if (
                not rec.privacy_consent_id
                or rec.privacy_consent_id.status != "granted"
            ):
                raise UserError(
                    _("\"Privacy\" mode: the linked consent must be "
                      "GRANTED before the testimonial is declared "
                      "consented (use \"Request consent (Law 25)\" then "
                      "\"Check consent\").")
                )
            if not rec.consent_note:
                rec.consent_note = (
                    _("privacy.consent #%s") % rec.privacy_consent_id.id
                )
        return super().action_set_consented()

    def _bf_cx_status_label(self, status):
        """The consent status as a reader sees it, not its technical key."""
        labels = dict(
            self.env["privacy.consent"]._fields["status"]
            ._description_selection(self.env)
        )
        return labels.get(status, status)

    def _bf_cx_consent_lost(self, status):
        """Consent gone (refused/withdrawn/expired): stop using the quote.

        Reached from a consent write: the client's public page, the expiry
        job, an officer. The trace is read by staff: in the company's
        language, not the visitor's or the job's (the source is English
        since bf_cx 18.0.1.13.2).
        """
        for rec in self:
            rec = rec.with_context(
                lang=langue_de(rec.env, societe=rec.company_id))
            if rec.state in ("consented", "published"):
                rec.action_retire()
            elif rec.state == "consent_pending":
                rec.write({"state": "declined"})
            rec.message_post(
                body=rec.env._(
                    "Law 25 consent \"%s\" - the testimonial can no "
                    "longer be used."
                )
                % rec._bf_cx_status_label(status)
            )
        return True

    @api.constrains("state", "consent_mode", "privacy_consent_id")
    def _check_privacy_consent_on_publish(self):
        for rec in self:
            if (
                rec.state in ("consented", "published")
                and rec.consent_mode == "privacy"
                and (
                    not rec.privacy_consent_id
                    or rec.privacy_consent_id.status != "granted"
                )
            ):
                raise ValidationError(
                    _("\"Privacy\" mode: cannot mark as consented or "
                      "published without a GRANTED Law 25 consent linked "
                      "to the testimonial.")
                )
