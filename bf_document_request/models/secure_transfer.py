from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.osv import expression


class SecureTransferBrand(models.Model):
    _inherit = "secure.transfer.brand"

    bf_document_default_expiry_policy_id = fields.Many2one(
        "bf.document.expiry.policy",
        string="Default expiry (document requests)",
        help="Expiry policy applied to this brand's transfers. Takes "
        "precedence over the retention chosen when sending.",
    )

    # ------------------------------------------------------- automatic reminders
    # Set per brand, hence per organisation: a business that serves ten
    # clients a day does not have the same tolerance for reminders as an
    # office that sees two a week. Deliberate opt-in (default False): sending automatic
    # text messages to clients without anyone asking for it is the kind of
    # default nobody forgives.
    bf_document_reminder_enabled = fields.Boolean(
        string="Automatically remind document requests",
        default=False,
        help="Sends the request's link again, by email or by text message "
        "according to each request's channel, as long as the file is "
        "incomplete, following the delay and the maximum number below.",
    )
    bf_document_reminder_delay_hours = fields.Integer(
        string="Delay between reminders (h)",
        default=24,
        help="How long to wait after the last sending before reminding. "
        "Counted from the last reminder, manual or automatic.",
    )
    bf_document_reminder_max = fields.Integer(
        string="Maximum number of reminders",
        default=2,
        help="Budget of AUTOMATIC reminders per request. The employee's "
        "manual reminders do not use it up.",
    )

    @api.constrains("bf_document_reminder_delay_hours", "bf_document_reminder_max")
    def _check_bf_document_reminder(self):
        for rec in self:
            if rec.bf_document_reminder_enabled:
                if rec.bf_document_reminder_delay_hours < 1:
                    raise ValidationError(_(
                        "The delay between reminders must be at least one "
                        "hour, otherwise the scheduled job reminds at every "
                        "run."))
                if rec.bf_document_reminder_max < 1:
                    raise ValidationError(_(
                        "A maximum number of reminders of zero amounts to "
                        "turning automatic reminders off."))


class SecureTransfer(models.Model):
    _inherit = "secure.transfer"

    # The base offers fr_CA and en_CA. A database with English but without
    # en_CA (en_US, Odoo's source language, alone) had no English value that
    # is active there: see ``bf.document.request._transfer_locale``. On
    # uninstall, these transfers fall back to the base's default.
    locale = fields.Selection(
        selection_add=[("en_US", "English (United States)")],
        ondelete={"en_US": "set default"},
    )

    @api.model
    def _gc_drafts_domain(self, cutoff):
        # The draft of a Sent or Partially received request waits for the
        # documents until the request's deadline: the request closes it
        # (_cron_expire_requests). The 24-hour cleanup used to cancel it,
        # photos included, and the request stayed "Sent" with a dead link.
        return expression.AND([super()._gc_drafts_domain(cutoff), [
            "|", ("bf_document_request_id", "=", False),
            ("bf_document_request_id.state", "not in", ("sent", "partial")),
        ]])

    # ------------------------------------------------------- sender quota
    # The base caps SPONTANEOUS sendings per sender address (5 a day, drafts
    # included) against abuse of its public page. A transfer born from a
    # document request is not a spontaneous sending: the requester asked for
    # it, and its "sender" is the client's address (or the requester's, by
    # text message). Without what follows, the sixth request of the day to the
    # same client failed at upload, once the documents were uploaded. The base
    # is not touched: its two narrowest entry points are overridden.
    @api.model
    def _check_sender_quota(self, sender_email, exclude_id=None):
        # On finalisation, ``exclude_id`` is the transfer being finalised: if
        # it belongs to a request, it is not subject to the quota.
        if exclude_id and self.sudo().browse(exclude_id).bf_document_request_id:
            return None
        # Elsewhere (public page), the quota holds, but the requests'
        # transfers no longer count among this address's sendings.
        return super(SecureTransfer, self.with_context(
            bf_document_quota_without_requests=True,
        ))._check_sender_quota(sender_email, exclude_id=exclude_id)

    @api.model
    def _daily_usage(self, domain):
        # Only under ``_check_sender_quota``: the base's other counts (quota
        # per IP address, volume) stay what they are.
        if self.env.context.get("bf_document_quota_without_requests"):
            domain = list(domain) + [("bf_document_request_id", "=", False)]
        return super()._daily_usage(domain)

    bf_document_expiry_policy_id = fields.Many2one(
        "bf.document.expiry.policy",
        string="Expiry (document request)",
        help="Replaces the retention for this transfer. "
        "Empty = the brand's default.",
    )
    # Back link to the request that gave birth to this transfer. An
    # "ordinary" transfer (public page, backend sending) has none: that is
    # what tells a requested upload from a spontaneous sending.
    bf_document_request_ids = fields.One2many(
        "bf.document.request",
        "transfer_id",
        string="Document request",
    )
    bf_document_request_id = fields.Many2one(
        "bf.document.request",
        string="Request",
        compute="_compute_bf_document_request_id",
        store=True,
    )

    @api.depends("bf_document_request_ids")
    def _compute_bf_document_request_id(self):
        for rec in self:
            rec.bf_document_request_id = rec.bf_document_request_ids[:1]

    def _send_link_emails(self, recipients_only=False):
        """No receipt to the client of a document request.

        The base sends its "sender" a receipt carrying the download link of
        what they uploaded. Here the sender is the client: their identity
        document would stay downloadable from their mailbox for the whole
        retention. Only the requester receives the link; the client's page
        already tells them their documents were sent."""
        requested = self.filtered("bf_document_request_id")
        others = self - requested
        if others:
            super(SecureTransfer, others)._send_link_emails(
                recipients_only=recipients_only)
        if requested:
            super(SecureTransfer, requested.with_context(
                bf_document_no_receipt=True))._send_link_emails(recipients_only=True)

    def _log(self, action, file=None, ip=None, ua=None, actor=None, note=None):
        # The base's note says "receipt to: <client>" whenever the sending is
        # not a manual re-send: in the chained log, it must say what happened.
        if (action == "emailed" and not actor
                and self.env.context.get("bf_document_no_receipt")):
            note = self._log_env()._(
                "Link sent to: %s (no receipt to the client)",
                self.recipient_emails or "")
        return super()._log(action, file=file, ip=ip, ua=ua, actor=actor, note=note)

    def _bf_document_effective_policy(self):
        self.ensure_one()
        return (self.bf_document_expiry_policy_id
                or self.brand_id.bf_document_default_expiry_policy_id)

    def _activate(self):
        """Apply the document-request expiry policy, if any, BEFORE the base computes
        ``expiry_date`` from ``retention_days``.

        Writing ``retention_days`` here rather than clamping ``expiry_date``
        afterwards is what keeps this zero-touch: the base only bounds the
        retention when the send form validates, never inside ``_activate``.
        """
        self.ensure_one()
        policy = self._bf_document_effective_policy()
        if policy and policy.retention_days:
            self.retention_days = policy.retention_days
        return super()._activate()
