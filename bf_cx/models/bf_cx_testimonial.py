"""Customer testimonials with consent tracking.

The core tracks consent obtained verbally or in writing (with a proof note).
When privacy_consent is installed, the bf_cx_privacy bridge adds the
formal Loi 25 consent flow (privacy.consent link + request button); the core
has no reference to privacy models so it loads on any tenant.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BfCxTestimonial(models.Model):
    _name = "bf.cx.testimonial"
    _description = "Customer testimonial"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"

    name = fields.Char(string="Title", required=True, tracking=True)
    partner_id = fields.Many2one(
        "res.partner", string="Client", required=True, index=True, tracking=True
    )
    body = fields.Text(string="Testimonial", required=True)
    context_note = fields.Char(
        string="Context",
        help="Engagement, project or situation the testimonial comes from.",
    )
    project_id = fields.Many2one("project.project", string="Project / "
                                                           "engagement")
    date = fields.Date(
        string="Date", default=fields.Date.context_today, required=True
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("consent_pending", "Consent requested"),
            ("consented", "Consent obtained"),
            ("published", "Published"),
            ("declined", "Refused"),
            ("retired", "Withdrawn"),
        ],
        string="Status",
        default="draft",
        required=True,
        tracking=True,
    )
    # The 'privacy' mode (formal privacy.consent flow) is ADDED by the
    # bf_cx_privacy bridge via selection_add - the core must not
    # offer a mode whose proof mechanism is not installed.
    consent_mode = fields.Selection(
        [
            ("verbal", "Verbal"),
            ("written", "Written (email, letter)"),
            ("survey", "Survey (box ticked by the respondent)"),
        ],
        string="Consent method",
        tracking=True,
    )
    consent_note = fields.Char(
        string="Proof of consent",
        help="Reference of the proof: client's email, date of the call, "
             "signed document, etc.",
    )
    consent_date = fields.Date(string="Consented on")
    published_at = fields.Date(string="Published on", readonly=True, copy=False)
    where_used = fields.Char(
        string="Used on",
        help="Website, proposal, LinkedIn page… where the testimonial is "
             "quoted.",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    feedback_id = fields.Many2one(
        "bf.cx.feedback",
        string="Source feedback",
        ondelete="set null",
    )

    # ── Actions ──────────────────────────────────────────────────────────────

    def action_set_consented(self):
        for rec in self:
            if not rec.consent_mode:
                raise UserError(
                    _("Set the consent method before declaring it obtained.")
                )
            if not rec.consent_note:
                raise UserError(
                    _("Record the proof of consent (\"Proof of consent\" "
                      "field).")
                )
            rec.write(
                {
                    "state": "consented",
                    "consent_date": rec.consent_date
                    or fields.Date.context_today(rec),
                }
            )
        return True

    def action_publish(self):
        for rec in self:
            if rec.state != "consented":
                raise UserError(
                    _("A testimonial can only be published with consent "
                      "obtained.")
                )
            rec.write(
                {
                    "state": "published",
                    "published_at": fields.Date.context_today(rec),
                }
            )
        return True

    def action_decline(self):
        self.write({"state": "declined"})
        return True

    def action_retire(self):
        """Consent withdrawn: stop using the testimonial everywhere."""
        for rec in self:
            rec.write({"state": "retired"})
            rec.message_post(
                body=_(
                    "Testimonial withdrawn - remove any current use (%s)."
                )
                % (rec.where_used or _("no location recorded"))
            )
        return True

    def action_reset_draft(self):
        self.write({"state": "draft"})
        return True
