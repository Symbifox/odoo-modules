import logging

from odoo import _, api, fields, models
from odoo.http import request
from odoo.tools import clean_context

_logger = logging.getLogger(__name__)

PURPOSES = {"hours": "bf_work_hours_privacy.purpose_work_hours",
            "insights": "bf_work_hours_privacy.purpose_work_insights"}


class WorkConsent(models.Model):
    """Each activation is mirrored in the privacy register, as a granted consent.

    The person then finds it in their privacy page (/my/privacy) beside their
    other consents, with its evidence, and can withdraw it from there too.
    """

    _inherit = "bf.work.consent"

    privacy_consent_id = fields.Many2one("privacy.consent", readonly=True, ondelete="set null")

    @api.model_create_multi
    def create(self, vals_list):
        consents = super().create(vals_list)
        consents.sudo()._bf_mirror_grant()
        return consents

    def write(self, vals):
        res = super().write(vals)
        # Every withdrawal reaches its mirror, whatever path it took: a mirror
        # already withdrawn (the one the register itself changed) is skipped.
        if vals.get("withdrawn_at"):
            mirrors = self.sudo().privacy_consent_id.filtered(lambda c: c.status == "granted")
            mirrors.with_context(bf_work_from_work=True).write(
                {"status": "withdrawn", "withdrawn_at": fields.Datetime.now()})
        return res

    def _bf_mirror_grant(self):
        """Mirror each activation as a granted consent of the person."""
        Consent = self.env["privacy.consent"]
        for consent in self:
            purpose = self.env.ref(PURPOSES[consent.scope], raise_if_not_found=False)
            if not purpose:
                continue
            # A clean context: no default (expiry, archive) sent by the caller or set
            # as ir.default reaches the mirror.
            mirror = Consent.with_context(clean_context(self.env.context), bf_work_from_work=True,
                                          mail_create_nolog=True, mail_notrack=True).create({
                "subject_partner_id": consent.user_id.partner_id.id,
                "purpose_id": purpose.id,
                "status": "granted",
                "granted_at": consent.granted_at,
                "collection_method": "portal",
                "notes": _("Turned on by the person themselves, in their own session, after reading "
                           "the notice (version %s), kept with the activation.", consent.notice_version),
                "company_id": consent.user_id.company_id.id,
                # No public link: a withdrawal goes through the person's own session.
                "access_token_expires_at": fields.Datetime.now(),
                "expires_at": False,
                "active": True,
            })
            consent.privacy_consent_id = mirror
            if request:
                try:
                    self.env["privacy.consent.evidence"].create_from_http_request(
                        consent=mirror, action="grant", note=_("Turned on by the person, in their own session"),
                        request_obj=request, access_type="portal_authenticated")
                except Exception:  # evidence is a plus, never a reason to refuse the activation
                    _logger.warning("Working time: consent evidence not recorded", exc_info=True)


class WorkCollector(models.AbstractModel):
    _inherit = "bf.work.collector"

    @api.model
    def _bf_notice_visibility(self):
        return super()._bf_notice_visibility() + [
            _("Your activation is also recorded in the privacy register, as your consent: the people in "
              "charge of privacy see that you turned it on, and when. Never your hours."),
        ]
