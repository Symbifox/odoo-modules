"""The document request engine, the heart of this module.

The ``bf_securetransfer`` base moves files one way: the sender uploads, the
recipient downloads. A document request goes the other way: an employee
*requests* documents, and the client uploads them from their phone.

The whole switch rests on one idea: **a request carries a draft transfer
created on the server side**, whose upload token is never shown to the client.
The client only knows the request's own token (``token``), and the public
routes of this module call the model on their behalf.

⚠️ This is a security decision, not a convenience. The base's
``/secrets/api/<ut>/finalize`` route lets ``recipient_emails`` be rewritten
when the brand is not a drop-off page. If the draft's ``upload_token`` were
handed to the mobile page, the client could redirect their own documents to
the address of their choosing. The request token cuts that off at the root: it
opens an upload, never a send.

Everything else (S3 presigning, quotas, the SHA-256 chain of the access log,
watermarking, purge on expiry) comes from the base without a single extra line.
"""

import logging
import re
import uuid
from datetime import timedelta

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.osv import expression
from odoo.tools import email_normalize, formataddr, html_escape
from odoo.tools.misc import format_datetime

from odoo.addons.bf_securetransfer.models import sms

_logger = logging.getLogger(__name__)

# The mobile page is for photographing documents, not for uploading archives:
# past the base's multipart threshold (64 MB by default), a file would need the
# full multipart upload cycle, which the capture page does not implement. We
# refuse cleanly rather than fail halfway.
MAX_CAPTURE_MB = 64

# VoIP.ms refuses messages over 160 characters, and accented letters count
# double in some encodings: aim for 150 to keep a margin.
SMS_MAX_CHARS = 150


def _mask_email(email):
    """"k•••@domain.org": enough to recognise the file in the hash-chained
    log, which ends up in the access certificate, without copying the whole
    address into it. Same rule as the last four digits of the mobile number."""
    local, _sep, domain = (email or "").partition("@")
    if not domain:
        return "•••"
    return "%s•••@%s" % (local[:1], domain)


def _mask_phone(phone):
    """"••••••0199": the last four digits, as in the sending log."""
    return (phone or "")[-4:].rjust(10, "•")


def _active_lang(env, code):
    """``code`` when it is active in this database, else the closest active language.

    Same order as the base module: another variant of the same language (en_US
    first, it is the source language), fr_CA, the company's language, then the
    first active language. ``with_context(lang=…)`` must never receive a
    language that is not active: reading ``env.lang`` would then raise. Kept
    here because earlier releases of ``bf_securetransfer`` do not provide it.
    """
    # sudo: the public page calls this as the public user.
    actives = [lang for lang, _name in env["res.lang"].sudo().get_installed()]
    same = []
    if code and code not in actives:
        prefix = code.split("_")[0] + "_"
        same = sorted((lang for lang in actives if lang.startswith(prefix)),
                      key=lambda lang: lang != "en_US")
    for candidate in (code, *same[:1], "fr_CA", env.company.sudo().partner_id.lang):
        if candidate and candidate in actives:
            return candidate
    return actives[0] if actives else "en_US"


# Once the request is sent, these fields decide WHERE the client's link goes:
# changing them rotates the token.
_DESTINATION_FIELDS = ("channel", "email", "phone")
# ... and they only change with the requester's consent. ``user_id`` is one of
# them: without it, any colleague could first make themselves the requester,
# then change the address.
_GUARDED_FIELDS = _DESTINATION_FIELDS + ("user_id",)
# The state fields: only the module's own actions write them, always under
# sudo(). Writable over RPC, they bypassed the guard above: put someone else's
# request back to draft, change its address, mark it sent again and resend it;
# or create a "sent" request on someone else's draft transfer, which a
# cancellation would then have purged.
_STATE_FIELDS = ("state", "transfer_id", "token", "sent_at", "completed_at",
                 "reminder_count", "last_reminder_at")
# On a requested document, what only the client's upload and the submission
# write, always under sudo(). Writable over RPC, ``file_id`` let a line point to
# a file of another transfer, which retaking the photo then deleted.
_LINE_FIELDS = ("file_id", "state", "received_at")

# Carriage return, line feed and other control characters: in the display name
# of an address header, they are enough to make ``email_split`` read a second
# address.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")


def _strip_controls(text):
    return _CONTROL_RE.sub(" ", text or "").strip()


def _without_defaults(records, fnames):
    """The records, in a context stripped of the ``default_<field>`` keys of
    these fields. The ORM completes ``vals`` with the context defaults AFTER
    ``create`` has read it: an RPC client could slip ``default_state`` or
    ``default_transfer_id`` in there, and the guard never saw them."""
    context = {
        key: value for key, value in records.env.context.items()
        if not (key.startswith("default_") and key[8:] in fnames)
    }
    return records.with_context(context)


def _value_changes(record, fname, value):
    """Does the written value really change the field? A form may send back an
    unchanged value: it must be neither refused nor rotate the token."""
    record.ensure_one()
    # The value goes through the cache, as the ORM would store it: an id
    # becomes a record, a string a date.
    field = record._fields[fname]
    value = field.convert_to_record(
        field.convert_to_cache(value, record, validate=False), record)
    return (record[fname] or False) != (value or False)


class BfDocumentRequest(models.Model):
    _name = "bf.document.request"
    _description = "Document request"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(
        string="Request",
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _("New"),
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Client",
        required=True,
        tracking=True,
        help="The person the documents are requested from.",
    )
    channel = fields.Selection(
        selection=[
            ("email", "Email"),
            ("sms", "SMS"),
        ],
        string="Send by",
        required=True,
        default=lambda self: self._default_channel(),
        tracking=True,
        help="How the client receives their link. By default: text message "
        "when it is set up on this database, email otherwise.",
    )
    phone = fields.Char(
        string="Mobile",
        tracking=True,
        help="The number that will receive the link by text message. Filled "
        "in from the client's contact; can be changed here without touching "
        "the address book. Once sent, changing it replaces the link: the old "
        "one stops working, and the new one goes out with the next reminder, "
        "manual (\"Resend\") or automatic.",
    )
    email = fields.Char(
        string="Email",
        tracking=True,
        help="The address that will receive the link by email. Filled in from "
        "the client's contact; can be changed here without touching the "
        "address book. Once sent, changing it replaces the link: the old one "
        "stops working, and the new one goes out with the next reminder, "
        "manual (\"Resend\") or automatic.",
    )
    brand_id = fields.Many2one(
        "secure.transfer.brand",
        string="Brand",
        required=True,
        default=lambda self: self._default_brand(),
        help="Look and domain of the page the client will open: the brand of "
        "your organisation, not the software's.",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Requester",
        required=True,
        default=lambda self: self.env.user,
        tracking=True,
        help="The employee making the request. They are the one who will "
        "receive the documents.",
    )
    message = fields.Text(
        string="Note to the client",
        help="A line of context shown at the top of the mobile page.",
    )
    line_ids = fields.One2many(
        "bf.document.request.line",
        "request_id",
        string="Requested documents",
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("sent", "Sent"),
            ("partial", "Partially received"),
            ("received", "Received"),
            ("expired", "Expired"),
            ("cancelled", "Cancelled"),
        ],
        string="Status",
        default="draft",
        required=True,
        tracking=True,
        copy=False,
    )
    deadline = fields.Datetime(
        string="Deadline",
        tracking=True,
        copy=False,
        help="After this date, the client's link stops working. This is the "
        "time allowed to RESPOND, not to be confused with retention, which is "
        "how long the documents live once uploaded. Without a deadline, the "
        "request is closed automatically 30 days after it was sent (parameter "
        "bf_document_request.max_open_days, 0 = never).",
    )
    sent_at = fields.Datetime(string="Sent on", readonly=True, copy=False)
    completed_at = fields.Datetime(string="Received on", readonly=True, copy=False)

    # Reminders. `last_reminder_at` is shared by manual and automatic
    # reminders: it is what keeps the scheduled job from doubling up on an
    # employee who just resent by hand. `reminder_count` only counts the
    # automatic ones: it is a budget imposed on the machine, not on people.
    reminder_count = fields.Integer(
        string="Automatic reminders", readonly=True, copy=False, default=0)
    last_reminder_at = fields.Datetime(
        string="Last reminder", readonly=True, copy=False)

    # The draft transfer that collects the files. Created when the request is
    # sent, it becomes active when the client submits, and that switch is what
    # triggers the link email to the employee, through the base.
    transfer_id = fields.Many2one(
        "secure.transfer",
        string="Transfer",
        readonly=True,
        copy=False,
        ondelete="restrict",
    )
    # The last link email. The email deletes itself once sent
    # (``auto_delete``): this field then empties by itself. A failed email
    # stays, and that is what lets the requester be told about it. Restricted
    # to administrators, because the email body carries the client's link.
    link_mail_id = fields.Many2one(
        "mail.mail",
        string="Last link email",
        readonly=True,
        copy=False,
        ondelete="set null",
        groups="base.group_system",
    )
    # What the requester is allowed to know: computed under sudo, without
    # ever opening the email to them.
    link_mail_state = fields.Selection(
        selection=[
            ("queued", "Queued"),
            ("failed", "Failed"),
            ("none", "Nothing pending"),
        ],
        string="Last link email (status)",
        compute="_compute_link_mail_state",
        compute_sudo=True,
        help="Only sees immediate refusals from the outgoing mail server: an "
        "email that was accepted, then bounced later (address does not exist, "
        "mailbox full), does not show here.",
    )
    # The mobile page token. Restricted to managers: a regular user has no
    # reason to read a client's link, they trigger the sending and that is all.
    token = fields.Char(
        string="Token",
        readonly=True,
        copy=False,
        index=True,
        groups="bf_securetransfer.group_securetransfer_manager",
    )
    access_url = fields.Char(
        string="Client link",
        compute="_compute_access_url",
        groups="bf_securetransfer.group_securetransfer_manager",
    )
    doc_count = fields.Integer(
        string="Documents requested", compute="_compute_counts", store=True)
    received_count = fields.Integer(
        string="Documents received", compute="_compute_counts", store=True)
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
    )

    # ------------------------------------------------------------------ defaults / computes
    @api.model
    def _default_channel(self):
        """Text message when it is really set up (switch, credentials, number),
        email otherwise. Offering by default a channel that will refuse to send
        is what left the first requests of a live deployment stuck in draft."""
        return "sms" if sms.configured(self.env) else "email"

    @api.model
    def _default_brand(self):
        Brand = self.env["secure.transfer.brand"].sudo()
        return Brand.search([("active", "=", True), ("is_default", "=", True)],
                            limit=1) \
            or Brand.search([("active", "=", True)], limit=1)

    @api.depends("line_ids.state")
    def _compute_counts(self):
        for rec in self:
            rec.doc_count = len(rec.line_ids)
            rec.received_count = len(
                rec.line_ids.filtered(lambda line: line.state == "received"))

    @api.depends("token", "brand_id")
    def _compute_access_url(self):
        for rec in self:
            rec.access_url = rec._public_url() if rec.token else False

    @api.depends("link_mail_id.state")
    def _compute_link_mail_state(self):
        # ``compute_sudo``: the requester has no access to the email itself.
        # "cancel" counts as a failure: a cancelled email (blacklisted address,
        # for instance) did not go out either.
        for rec in self:
            mail_state = rec.link_mail_id.state
            if mail_state in ("exception", "cancel"):
                rec.link_mail_state = "failed"
            elif mail_state == "outgoing":
                rec.link_mail_state = "queued"
            else:
                rec.link_mail_state = "none"

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        # Changing the client rewrites the mobile number AND the address from
        # the new contact, even when empty: keeping the previous client's would
        # send them the link to the new file.
        for rec in self:
            if not rec.partner_id:
                continue
            rec.phone = rec.partner_id.mobile or rec.partner_id.phone or ""
            rec.email = rec.partner_id.email or ""

    def _refuse_state_fields(self):
        raise AccessError(_(
            "The status of a document request, its transfer, its token, its "
            "sent and received dates and its reminders can only be changed "
            "through its buttons (Send, Resend, Cancel)."))

    @api.model_create_multi
    def create(self, vals_list):
        # A request is born a draft, without transfer or token. The form sends
        # "draft" and zeros: those pass. Read BEFORE taking the sequence
        # number, which a refusal does not give back (a PostgreSQL sequence
        # does not roll back with the transaction).
        if not self.env.su:
            for vals in vals_list:
                if any(vals.get(fname) and not (fname == "state"
                                                and vals[fname] == "draft")
                       for fname in _STATE_FIELDS):
                    self._refuse_state_fields()
            # The context defaults come in after this check.
            self = _without_defaults(self, _STATE_FIELDS)
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "bf.document.request") or _("New")
        records = super().create(vals_list)
        if not self.env.su:
            records._check_born_draft()
        return records

    def _check_born_draft(self):
        """Belt and braces: whatever the ORM added after ``vals`` was read (a
        personal ``ir.default``, another module), a request created outside
        sudo is born a draft, without transfer, token, dates or reminders."""
        for rec in self.sudo():
            if rec.state != "draft" or any(
                    rec[fname] for fname in _STATE_FIELDS if fname != "state"):
                self._refuse_state_fields()

    def write(self, vals):
        """Guards the destinations of a sent request.

        Once the link is out, the channel, the address and the mobile number
        decide where the NEXT link goes: a colleague who could change them,
        then resend, would get the link to a file that is not theirs. The guard
        lives here, not in the view: an RPC call does not see the view. Any
        change of destination rotates the token.

        The state fields, for their part, are only written under sudo:
        otherwise the guard could be bypassed by sending the request back
        through draft.
        """
        state_fields = [fname for fname in _STATE_FIELDS if fname in vals]
        if state_fields and not self.env.su:
            # Compared under sudo: the token is restricted to managers.
            if any(rec._value_differs(fname, vals[fname])
                   for rec in self.sudo() for fname in state_fields):
                self._refuse_state_fields()
        guarded = [fname for fname in _GUARDED_FIELDS if fname in vals]
        before = {}
        handover = self.browse()
        if guarded:
            for rec in self:
                if rec.state not in ("sent", "partial"):
                    continue
                changes = [fname for fname in guarded
                           if rec._value_differs(fname, vals[fname])]
                if not changes:
                    continue
                if not rec._may_change_destination():
                    raise UserError(_(
                        "Request %s has already been sent: only its requester, "
                        "a secure transfer manager or the administrator can "
                        "change its channel, address, mobile number or "
                        "requester.", rec.name))
                if any(fname in _DESTINATION_FIELDS for fname in changes):
                    before[rec] = rec._destination()
                if "user_id" in changes:
                    handover |= rec
        res = super().write(vals)
        # Only what changes WHERE the link goes rotates it: correcting the
        # mobile number of a request sent by email does not kill the client's
        # link.
        for rec, destination in before.items():
            if rec._destination() != destination:
                rec._rotate_link()
        for rec in handover:
            rec._follow_requester()
        return res

    def _destination(self):
        """(channel, address or mobile number): where the request's link goes.

        Normalised: "Client@Example.com" and "client@example.com", or
        "514 555-0199" and "(514) 555-0199", are the same destination, and
        fixing the case or the formatting does not kill the client's link. An
        invalid entry is compared as is, without surrounding spaces.
        """
        self.ensure_one()
        if self.channel == "email":
            raw = (self.email or "").strip()
            return ("email", email_normalize(raw) or raw.lower())
        raw = (self.phone or "").strip()
        return ("sms", sms.normalize_na(raw) or raw)

    def _value_differs(self, fname, value):
        return _value_changes(self, fname, value)

    def _follow_requester(self):
        """The requester hands an open request over: the new one is who will
        receive the documents on submission.

        Otherwise the transfer keeps the first requester as its recipient, and
        as the fallback sender for a text message. Same rule as when sending: a
        requester without a valid address could not receive anything.
        """
        self.ensure_one()
        recipient = email_normalize(self.user_id.email or "")
        if not recipient:
            raise UserError(_(
                "The requester \"%s\" has no valid email address: they could "
                "not receive the documents.", self.user_id.name))
        self.transfer_id.sudo().write({
            "recipient_emails": recipient,
            "sender_email": self._transfer_sender_email(),
        })

    def _may_change_destination(self):
        self.ensure_one()
        user = self.env.user
        return bool(
            self.env.su
            or user._is_superuser()
            or user == self.user_id
            or user.has_group("bf_securetransfer.group_securetransfer_manager"))

    def _rotate_link(self):
        """A new token after a change of destination.

        The old link dies on the spot: it may have gone to an address that is
        no longer the right one. Nothing goes out on the spot: the new link
        goes out with the next reminder, the requester's ("Resend") or the
        scheduled job's, if the brand sends reminders automatically. The
        hash-chained log keeps the masked destination, the chatter the address
        in clear, and neither sees the link.

        The base's upload receipt follows the new destination, and the link
        email still in the queue, which carries the old link to the old
        destination, no longer goes out.
        """
        self.ensure_one()
        # Only a request that was really sent has a link to replace: on a
        # draft or a forged request, nothing is minted.
        if not (self.transfer_id and self.sent_at
                and self.state in ("sent", "partial")):
            raise UserError(_(
                "The link of request %s cannot be replaced: it is not open.",
                self.name))
        # sudo(): ``token`` is restricted to managers (see action_send).
        self.sudo().write({"token": uuid.uuid4().hex})
        # Without this, the receipt went to the address that was just removed.
        self.transfer_id.sudo().write(
            {"sender_email": self._transfer_sender_email()})
        # Removed from the queue, as ``auto_delete`` would have done after
        # sending. Cancelled, it would stay in the database with its dead link
        # and light up the failure banner on the form.
        queued = self.sudo().link_mail_id
        if queued.state == "outgoing":
            queued.unlink()
        log_env = self._log_env()
        if self.channel == "email":
            masked = log_env._("email to %s", _mask_email(self.email))
            in_clear = _("email to %s", self.email or "")
        else:
            masked = log_env._("text message to %s", _mask_phone(self.phone))
            in_clear = _("text message to %s", self.phone or "")
        self._log(
            "bf_document_request_destination_changed",
            note=log_env._("Link destination changed by %(who)s: %(dest)s; "
                           "the old link is revoked",
                           who=self.env.user.name, dest=masked))
        # Without promising that nothing goes out before "Resend": the
        # reminder job, if active, will send the new link by itself.
        self.message_post(body=_(
            "Link destination changed: %(dest)s. The old link no longer "
            "works; the new one will go out with the next reminder, manual "
            "(\"Resend\") or automatic.",
            dest=in_clear))

    def unlink(self):
        # A sent request has left a trace in the transfer's hash-chained log:
        # deleting it would make the chain lie. Cancel it instead.
        sent = self.filtered(lambda rec: rec.state != "draft")
        if sent:
            raise UserError(_(
                "A request that has been sent cannot be deleted: its log must "
                "be kept. Use \"Cancel\"."))
        return super().unlink()

    # ------------------------------------------------------------------ helpers
    def _public_url(self):
        self.ensure_one()
        base = self.brand_id.sudo()._share_base_url()
        return "%s/d/%s" % (base.rstrip("/"), self.token)

    def _log(self, action, note=None, file=None, ip=None, ua=None, actor=None):
        """Writes to the base's SHA-256 chain, carried by the transfer.

        One log, one chain: the request's lifecycle and the file accesses are
        verified together (``verify_chain``). Compose ``note`` with
        ``self._log_env()._(…)``: it is frozen text, printed as is on the
        access certificate.
        """
        self.ensure_one()
        if not self.transfer_id:
            return False
        return self.transfer_id.sudo()._log(
            action, file=file, ip=ip, ua=ua,
            actor=actor or self.env.user.login, note=note)

    # ------------------------------------------------------------------ languages
    def _requester_lang(self):
        """The language of the employee who made the request."""
        self.ensure_one()
        return _active_lang(self.env, self.user_id.sudo().lang or self.env.user.lang)

    def _transfer_locale(self):
        """The ``locale`` given to the transfer: always an ACTIVE language.

        Earlier releases of ``bf_securetransfer`` use the locale as is: the
        access certificate is rendered ``with_context(lang=locale)``, and so
        is the client's receipt when their address matches no contact with a
        language. Odoo refuses a language that is not active (« Invalid
        language code »): an English requester on a database with fr_CA and
        en_US but no en_CA made the certificate fail every time, and the
        client's "Finish and send" was refused.

        Only the values of the field qualify, in the base's order: fr_CA and
        en_CA first (the catalogue those releases translate is en_CA), then
        en_US, added by this module for a database with English but no en_CA.
        The requester's language family wins, then fr_CA, the company's
        language, any active value. When none is active, en_US: Odoo accepts
        it in any context, active or not, and recent bases map every locale
        to an active language anyway.
        """
        self.ensure_one()
        family = self._requester_lang().split("_")[0]
        values = self.env["secure.transfer"]._fields["locale"].get_values(self.env)
        actives = {code for code, _name in self.env["res.lang"].sudo().get_installed()}
        usable = [code for code in values if code in actives]
        for code in usable:
            if code.split("_")[0] == family:
                return code
        for code in ("fr_CA", self.env.company.sudo().partner_id.lang, *usable):
            if code in usable:
                return code
        return "en_US"

    def _requester_env(self):
        """The environment for text the requester reads but nobody typed in
        their session: messages posted by the scheduled jobs or by the client's
        page. ``_()`` alone would follow the scheduled job's user, or the
        visitor's browser."""
        return self.with_context(lang=self._requester_lang()).env

    def _log_env(self):
        """The environment the access-log notes are written in: the transfer's
        language, the one its access certificate is printed in. A note written
        in the language of whoever triggered the event (a client on the page,
        a scheduled job, an employee) would mix languages on that document."""
        self.ensure_one()
        locale = self.transfer_id.sudo().locale
        return self.with_context(
            lang=_active_lang(self.env, locale) if locale else self._requester_lang()).env

    def _client_lang(self):
        """The language the client reads their email and text messages in.

        Their contact's language first: the request names the client. Without
        one, the language of the contact who owns the destination address,
        then the transfer's language (the base's ``_lang_for_email``).
        """
        self.ensure_one()
        partner_lang = self.partner_id.sudo().lang
        if partner_lang:
            return _active_lang(self.env, partner_lang)
        transfer = self.transfer_id.sudo()
        if transfer:
            # Earlier releases of the base may return a language that is not
            # active (the transfer's locale as is).
            return _active_lang(self.env, transfer._lang_for_email(
                self.email if self.channel == "email" else ""))
        return self._requester_lang()

    def _effective_policy(self):
        """The retention policy that governs this upload.

        The SHORTEST policy among the requested types wins: a file that holds
        a document kept one day cannot live thirty days just because another
        document is entitled to it.

        We return the POLICY, not a number of days, and set it on the
        transfer. Otherwise ``SecureTransfer._activate()`` (further down in
        this module) would reapply the brand's default policy and overwrite the
        result: the one-day document would live thirty days.
        """
        self.ensure_one()
        policies = self.line_ids.mapped(
            "document_type_id.expiry_policy_id").filtered("retention_days")
        if not policies:
            return self.env["bf.document.expiry.policy"]
        return min(policies, key=lambda pol: pol.retention_days)

    def _sms_body(self):
        """The text message, capped at 150 characters: the limit is hard on
        the VoIP.ms side. Written in the language of ``self``'s context:
        call it through ``_client_env_record()``.

        The link is built first (it cannot be shortened), then the brand name
        is trimmed: better a truncated name than a truncated link.
        """
        self.ensure_one()
        url = self._public_url()
        count = len(self.line_ids)
        suffix = _("%(n)s document(s) to upload: %(url)s",
                   n=count, url=url)
        brand_name = (self.brand_id.name or "").strip()
        budget = SMS_MAX_CHARS - len(suffix) - 2  # 2 = "." + space
        if brand_name and budget > 8:
            return "%s. %s" % (brand_name[:budget], suffix)
        return suffix

    def _sms_reminder_body(self):
        """The reminder text message.

        It differs from the first one on two points that matter to the client:
        it presents itself as a reminder, and it only counts what is MISSING.
        Asking again for "3 documents" from someone who already uploaded two
        is the best way to make them give up.
        """
        self.ensure_one()
        url = self._public_url()
        missing = len(self.line_ids.filtered(lambda ln: ln.state == "pending"))
        if missing:
            suffix = _("Reminder: %(n)s document(s) to upload. %(url)s",
                       n=missing, url=url)
        else:
            # Everything is uploaded: only the confirmation is left, as the
            # reminder email says.
            suffix = _("Reminder: please confirm sending your documents. %(url)s",
                       url=url)
        brand_name = (self.brand_id.name or "").strip()
        budget = SMS_MAX_CHARS - len(suffix) - 2
        if brand_name and budget > 8:
            return "%s. %s" % (brand_name[:budget], suffix)
        return suffix

    def _client_env_record(self):
        """``self`` under sudo, in the client's language: the text messages
        carry the link, hence the token, which the sending employee may not
        read."""
        self.ensure_one()
        return self.sudo().with_context(lang=self._client_lang())

    # ------------------------------------------------------------------ email
    def _email_destination(self):
        """The client's address, normalised, or a UserError.

        One complete address only: ``email_normalize`` returns False for
        "a@x.com, b@y.com", and that is intended. The link is the key to the
        upload, it goes to ONE person.
        """
        self.ensure_one()
        email = email_normalize(self.email or "")
        if not email:
            raise UserError(_(
                "Invalid client email address: a single, complete address is "
                "needed to send them the link."))
        return email

    def _transfer_sender_email(self):
        """The transfer's "sender": the base sends the upload receipt to it.

        That is the client. By email, it is the address that receives the
        link. By text message, when their email is unknown (common: only
        their mobile number is known), the one on their contact is used,
        failing that the requester's: the base requires a sender address to
        finalise, and the uploader's real identity is proven by the text
        message anyway, not by an email. The named trace lives in the log, not
        in this field.

        The same computation serves the sending and every change of
        destination.
        """
        self.ensure_one()
        requester = email_normalize(self.user_id.email or "")
        if self.channel == "email":
            # An invalid address will not receive the link: the receipt does
            # not fall back to the contact for all that, which may hold the
            # very address that was just replaced.
            return email_normalize(self.email or "") or requester
        return email_normalize(self.partner_id.email or "") or requester

    def _email_deadline_text(self):
        """The deadline in the requester's time zone, spelled out, in the
        language of ``self``'s context."""
        self.ensure_one()
        if not self.deadline:
            return ""
        tz = self.user_id.tz or self.env.user.tz or "America/Montreal"
        # A date pattern (Unicode CLDR): each language orders day, month and
        # time its own way.
        return format_datetime(
            self.env, self.deadline, tz=tz, dt_format=_("MMMM d, y 'at' HH:mm"))

    def _email_link_content(self, reminder=False):
        """(subject, band heading, inner body) of the link email.

        Everything that comes from user input (client, requester and brand
        names, documents, note to the client) is escaped: it is HTML sent to
        someone else. A reminder only lists what is MISSING, for the same
        reason as the reminder text message.
        """
        self.ensure_one()
        url = self._public_url()
        # Each read of the visuals searches the file storage for the logo and
        # the favicon (see the base's ``_otp_email``). There are two per email:
        # this one, and the one in the base's ``_brand_email_shell``, which
        # reads them again for its header. Do not add a third: do not call
        # ``_visuals()`` anywhere else in this email.
        visuals = self.brand_id.sudo()._visuals()
        brand = (visuals.get("name") or self.brand_id.name or "").strip()
        lines = self.line_ids
        if reminder:
            lines = lines.filtered(lambda ln: ln.state == "pending")
        n = len(lines)
        # Everything is uploaded, nothing is sent yet: "0 document(s) to
        # upload" would have let the client believe they were done.
        to_confirm = reminder and not n
        if to_confirm:
            subject = _("Reminder: please confirm sending your documents to %(brand)s",
                        brand=brand)
            heading = _("Reminder")
            lead = _("Your documents are uploaded, but %(who)s, from %(brand)s, "
                     "has not received them yet: all that is left is to confirm, "
                     "with the \"Finish and send\" button on the page.",
                     who=self.user_id.name, brand=brand)
        elif reminder:
            subject = _("Reminder: %(n)s document(s) to upload for %(brand)s",
                        n=n, brand=brand)
            heading = _("Reminder")
            lead = _("%(who)s, from %(brand)s, is still waiting for the "
                     "following documents:", who=self.user_id.name, brand=brand)
        else:
            subject = _("%(brand)s: %(n)s document(s) to upload",
                        brand=brand, n=n)
            heading = _("Requested documents")
            lead = _("%(who)s, from %(brand)s, is asking you for the following "
                     "documents:", who=self.user_id.name, brand=brand)
        name = (self.partner_id.name or "").strip()
        hello = _("Hello %s,", name) if name else _("Hello,")
        # Translated OUTSIDE the comprehension: in a generator, ``_`` does not
        # find the language in its calling frame and returns the raw source.
        optional = ' <span style="color:#777;">(%s)</span>' % html_escape(
            _("optional"))
        items = "".join(
            '<li style="margin:2px 0;">%s%s</li>' % (
                html_escape(ln.document_type_id.name or ""),
                "" if ln.required else optional)
            for ln in lines)
        parts = [
            "<p>%s</p>" % html_escape(hello),
            "<p>%s</p>" % html_escape(lead),
        ]
        if items:
            parts.append(
                '<ul style="padding-left:20px;margin:8px 0 16px;">%s</ul>' % items)
        note = (self.message or "").strip()
        if note:
            parts.append(
                '<p style="border-left:3px solid #e3e7eb;padding:4px 12px;'
                'margin:0 0 16px;color:#444;">%s</p>'
                % "<br/>".join(html_escape(line) for line in note.splitlines()))
        deadline = self._email_deadline_text()
        if deadline:
            parts.append("<p><strong>%s</strong></p>" % html_escape(
                _("Please upload them before %s.", deadline)))
        primary = visuals.get("primary") or "#1A5BAA"
        parts.append(
            '<p style="text-align:center;margin:24px 0;">'
            '<a href="%(url)s" style="display:inline-block;background:%(bg)s;'
            'color:#ffffff;text-decoration:none;font-weight:700;'
            'border-radius:8px;padding:12px 24px;">%(label)s</a></p>' % {
                "url": html_escape(url), "bg": html_escape(primary),
                "label": html_escape(_("Confirm sending") if to_confirm
                                     else _("Upload my documents")),
            })
        parts.append('<p style="font-size:13px;">%s</p>' % html_escape(_(
            "On your phone, you can photograph each document; on a computer, "
            "attach a file (image or PDF). Your documents are encrypted and "
            "deleted automatically when they expire.")))
        parts.append(
            '<p style="color:#777;font-size:12px;">%s<br/>'
            '<span style="word-break:break-all;">%s</span></p>' % (
                html_escape(_("If the button does not work, copy this link "
                              "into your browser:")),
                html_escape(url)))
        parts.append('<p style="color:#777;font-size:12px;">%s</p>' % html_escape(_(
            "This link is personal: do not forward it. If you were not "
            "expecting this request, ignore this email.")))
        return subject, heading, "".join(parts)

    def _send_link_email(self, reminder=False):
        """Queues the email that carries the client's link, in the client's
        language.

        ⚠️ The email is attached to NO record (empty ``model``), has neither an
        author nor a creator on the employee's side, and deletes itself once
        sent. The link IS the key to the upload and ``token`` is restricted to
        managers: an email posted on the request's chatter, or authored by the
        employee, would make it readable to them, and the base has already
        paid for that hole on its own emails (``_redact_chatter_links``). The
        chatter only keeps the address.

        Queued rather than sent on the spot, like the base's link emails: an
        SMTP send inside the transaction would go out before the commit, and a
        rollback afterwards would leave the client with a link to a request
        that does not exist. ``_wake_mail_queue`` sends the queue right after
        the commit.
        """
        self.ensure_one()
        destination = self._email_destination()
        transfer = self.transfer_id.sudo()
        if not transfer:
            raise UserError(_("This request has no transfer yet."))
        lang = self._client_lang()
        rec = self.sudo().with_context(lang=lang)
        subject, heading, inner = rec._email_link_content(reminder=reminder)
        values = {
            "subject": subject,
            "email_from": transfer._brand_email_from(),
            # The address ALONE, without the client's name: a line break in
            # that name would make ``email_split`` read a second address, hence
            # a second recipient of the link.
            "email_to": destination,
            "body_html": transfer.with_context(lang=lang)._brand_email_shell(
                heading, inner),
            "model": False,
            "res_id": False,
            "author_id": False,
            "auto_delete": True,
        }
        requester = email_normalize(self.user_id.email or "")
        if requester:
            # The client's reply goes to the person who made the request, not
            # to the brand's mailbox. Accepted risk: a reply that quotes the
            # email shows the link to the requester, that is, to the person
            # who requested the documents and who will receive them.
            # The name is stripped of control characters, for the same reason
            # as ``email_to``.
            values["reply_to"] = formataddr(
                (_strip_controls(self.user_id.name), requester))
        # SUPERUSER_ID, not sudo(): sudo() keeps the user, so the employee
        # would be the message's ``create_uid``, and Odoo 18 lets the creator
        # of a message read it again (``mail.message._get_forbidden_access``).
        mail = self.env["mail.mail"].with_user(SUPERUSER_ID).create(values)
        # The last link email, kept to report a failure.
        self.sudo().link_mail_id = mail.id
        transfer._wake_mail_queue()
        return destination

    # ------------------------------------------------------------------ sending
    def action_send(self):
        """Creates the draft transfer, mints the token, sends the link.

        By text message or by email, according to ``channel``. Everything that
        can refuse the sending is checked BEFORE the draft is created: a
        refused request leaves nothing behind.
        """
        self.ensure_one()
        if not self.env.user.has_group(
                "bf_securetransfer.group_securetransfer_user"):
            raise UserError(_(
                "This action is reserved for secure transfer users."))
        if self.state != "draft":
            raise UserError(_("This request has already been sent."))
        if not self.line_ids:
            raise UserError(_("Add at least one document to request."))
        if not self.brand_id:
            raise UserError(_("Choose your organisation's brand."))

        by_email = self.channel == "email"
        phone = destination = False
        if by_email:
            destination = self._email_destination()
        else:
            phone = sms.normalize_na(self.phone or "")
            if not phone:
                raise UserError(_(
                    "Invalid mobile number: a 10-digit North American number "
                    "is needed to reach the client by text message."))
            if not sms.configured(self.env):
                raise UserError(_(
                    "The text message channel is not set up (VoIP.ms). Without "
                    "it, the client cannot receive their link: send it by "
                    "email, or see Configuration › Settings."))

        # The requesting employee is the RECIPIENT of the transfer: they will
        # receive the link to the documents once the upload is submitted.
        recipient = email_normalize(self.user_id.email or "")
        if not recipient:
            raise UserError(_(
                "The requester \"%s\" has no valid email address: they could "
                "not receive the documents.", self.user_id.name))

        # The SENDER is the client: see ``_transfer_sender_email``.
        sender = self._transfer_sender_email()

        policy = self._effective_policy()
        # The transfer speaks the requester's language: they receive its
        # emails and keep its access certificate.
        requester_lang = self._requester_lang()
        Transfer = self.env["secure.transfer"].sudo()
        transfer = Transfer.create({
            "brand_id": self.brand_id.id,
            "sender_name": (self.partner_id.name or "")[:128],
            "sender_email": sender,
            "recipient_emails": recipient,
            "message": self.message or self.with_context(lang=requester_lang).env._(
                "Documents uploaded by %s (request %s).",
                self.partner_id.name, self.name),
            "retention_days": policy.retention_days or 7,
            "bf_document_expiry_policy_id": policy.id or False,
            "locale": self._transfer_locale(),
            "ip_created": "backend",
            "ua_created": "backend:bf_document_request:%s" % self.env.user.login,
            # The base would email a code to the "sender" before activating.
            # Here the sender is a client reached by text message on THEIR
            # number: the proof of identity is already made, and asking them
            # for an email code they may not have would block them outright.
            "sender_confirmed": True,
            "state": "draft",
        })
        # sudo(): `token` is a field restricted to MANAGERS (it is a
        # capability: the client's link). An employee may send a request, not
        # read other people's tokens: without this sudo, sending raises an
        # AccessError for everyone but a manager. The group check is done
        # above.
        self.sudo().write({
            "transfer_id": transfer.id,
            "token": uuid.uuid4().hex,
            "state": "sent",
            "sent_at": fields.Datetime.now(),
        })
        log_env = self._log_env()
        if by_email:
            self._send_link_email()
            self._log(
                "bf_document_request_sent",
                note=log_env._("Request %(name)s: %(n)s document(s) requested "
                               "from %(who)s, email to %(email)s, deadline "
                               "%(deadline)s",
                               name=self.name, n=len(self.line_ids),
                               who=self.partner_id.display_name,
                               email=_mask_email(destination),
                               deadline=fields.Datetime.to_string(self.deadline)
                               or log_env._("none")),
            )
            self.message_post(body=_(
                "Request sent by email to %(email)s, %(n)s document(s).",
                email=destination, n=len(self.line_ids)))
            return True
        self._log(
            "bf_document_request_sent",
            note=log_env._("Request %(name)s: %(n)s document(s) requested from "
                           "%(who)s, text message to %(phone)s, deadline "
                           "%(deadline)s",
                           name=self.name, n=len(self.line_ids),
                           who=self.partner_id.display_name,
                           phone=_mask_phone(phone),
                           deadline=fields.Datetime.to_string(self.deadline)
                           or log_env._("none")),
        )
        # sudo(): the text message carries the link, hence the token, which
        # the sending employee may not READ. They send it without ever seeing
        # it, which is exactly the intent.
        if not sms.send(self.env, phone, self._client_env_record()._sms_body()):
            # sms.send never raises: it returns False, and WE raise.
            # ⚠️ Raising here ROLLS BACK the transaction: the draft transfer,
            # the token and the "sent" status go with it. The request therefore
            # stays a draft: do not tell the employee it was created and that
            # they should "resend" (there is no resend button on a draft).
            raise UserError(_(
                "The text message could not be delivered to %s. The request "
                "stays a draft: check the number and try again.", self.phone))
        self.message_post(body=_(
            "Request sent by text message to %(phone)s, %(n)s document(s).",
            phone=self.phone, n=len(self.line_ids)))
        return True

    def action_resend(self):
        """Reminder: same token, same request, a new message through the
        request's channel."""
        self.ensure_one()
        if self.state not in ("sent", "partial"):
            raise UserError(_(
                "Only an open request can be resent."))
        if self.channel != "email":
            return self._resend_sms()
        destination = self._send_link_email(reminder=True)
        # Same timestamp as a text message reminder: without it, the scheduled
        # job would remind the client a few minutes after the employee did.
        self.sudo().last_reminder_at = fields.Datetime.now()
        self._log("bf_document_reminder_sent",
                  note=self._log_env()._("Manual reminder by email to %s",
                                         _mask_email(destination)))
        self.message_post(body=_("Reminder sent by email to %s.",
                                 destination))
        return True

    def action_resend_sms(self):
        """The former button, kept for existing calls: it only resends by text
        message, and refuses a request sent by email rather than write to the
        mobile of a client who expects an email."""
        self.ensure_one()
        if self.channel != "sms":
            raise UserError(_(
                "This request goes out by email: use \"Resend\"."))
        return self.action_resend()

    def _resend_sms(self):
        """Reminder: same token, same request, a new text message."""
        self.ensure_one()
        phone = sms.normalize_na(self.phone or "")
        if not phone:
            raise UserError(_("Invalid mobile number."))
        if not sms.send(self.env, phone, self._client_env_record()._sms_reminder_body()):
            raise UserError(_("The text message could not be delivered to %s.",
                              self.phone))
        # Even a manual reminder is timestamped: otherwise the scheduled job
        # could send a second text message a few minutes later and the client
        # would get two in a row. The automatic counter stays untouched: the
        # employee does not use up the machine's budget.
        self.sudo().last_reminder_at = fields.Datetime.now()
        # Masked like the rest of the hash-chained log, which ends up on the
        # certificate.
        self._log("bf_document_reminder_sent",
                  note=self._log_env()._("Manual reminder by text message to %s",
                                         _mask_phone(self.phone)))
        self.message_post(body=_("Reminder sent by text message to %s.",
                                 self.phone))
        return True

    def action_print_certificate(self):
        """The request's access certificate.

        There are not two of them: the request's lifecycle is logged in THE
        chain of the underlying transfer (the whole point of refusing a second
        log). The base's certificate therefore already tells the full story:
        the message sent, the page opened, each document uploaded, the
        download by the employee. We delegate.
        """
        self.ensure_one()
        if not self.transfer_id:
            raise UserError(_(
                "This request has no transfer yet: the certificate only "
                "exists once it has been sent."))
        return self.transfer_id.action_print_certificate()

    def action_cancel(self):
        """Cuts the client's link. The log stays."""
        for rec in self:
            if rec.state in ("received", "cancelled"):
                continue
            rec._log("bf_document_request_cancelled",
                     note=rec._log_env()._("Request cancelled by %s",
                                           self.env.user.name))
            # The token goes: the mobile page answers 404 at once. sudo() for
            # the same reason as when sending: `token` is restricted to
            # managers, and an employee may cancel their own request.
            rec.sudo().write({"token": False, "state": "cancelled"})
            rec._close_draft(actor=self.env.user.login)
        return True

    def _close_draft(self, actor=None):
        """Settles the draft of a request that will not go through.

        ⚠️ Not ``action_expire_now()``: the base can only expire an ACTIVE
        transfer, and ours stayed a draft. The right operation is
        ``_purge_s3()``, the one the base's garbage collector applies to
        abandoned drafts: it aborts the multipart uploads, deletes the photos
        already uploaded and moves the draft to "cancelled", keeping the trace.

        Best effort: unreachable storage must never prevent cancelling a
        request. Whatever stays in storage, the base's hourly garbage
        collector will sweep anyway.
        """
        self.ensure_one()
        if not self.transfer_id or self.transfer_id.state != "draft":
            return False
        try:
            self.transfer_id.sudo()._purge_s3(actor=actor)
        except Exception:
            _logger.warning(
                "bf_document_request: could not purge draft %s, the base's "
                "garbage collector will try again", self.transfer_id.name,
                exc_info=True)
            return False
        return True

    def action_open_transfer(self):
        self.ensure_one()
        if not self.transfer_id:
            raise UserError(_("This request has no transfer yet."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Transfer"),
            "res_model": "secure.transfer",
            "res_id": self.transfer_id.id,
            "view_mode": "form",
            "target": "current",
        }

    # ------------------------------------------------------------------ upload (called by the public controller)
    def _assert_open(self):
        """Does the request still accept an upload? Raises otherwise."""
        self.ensure_one()
        if self.state not in ("sent", "partial"):
            raise UserError(_("This request is no longer open."))
        if self.deadline and fields.Datetime.now() > self.deadline:
            raise UserError(_("The deadline of this request has passed."))
        if not self.transfer_id or self.transfer_id.state != "draft":
            raise UserError(_("This request no longer accepts documents."))

    def _register_capture(self, line, filename, size):
        """Registers a file on the draft and attaches it to the line.

        Retaking a photo is the NORMAL case (glare, framing): if the line
        already carries a file, it is removed from storage before a new one is
        accepted, otherwise each retake would use up the transfer's quota.
        """
        self.ensure_one()
        self._assert_open()
        if line.request_id != self:
            raise UserError(_("Unknown document for this request."))
        # The file the line already carries, which the retake is about to
        # delete, must belong to the request's transfer.
        if line.file_id and line.sudo().file_id.transfer_id != self.transfer_id:
            raise UserError(_("Unknown document for this request."))
        threshold = MAX_CAPTURE_MB * 1024 * 1024
        if size and size > threshold:
            raise UserError(_(
                "File too large (maximum %s MB).", MAX_CAPTURE_MB))
        if line.file_id:
            line._drop_file()
        rec_file = self.transfer_id.sudo()._register_file(filename, size)
        if rec_file.upload_mode != "simple":
            # Belt and braces: the base's threshold is a parameter, it could
            # drop below our ceiling.
            rec_file.unlink()
            raise UserError(_(
                "File too large (maximum %s MB).", MAX_CAPTURE_MB))
        # sudo(): the line's fields are only written under sudo. The public
        # controller already calls under sudo; we do not depend on it.
        line.sudo().write({"file_id": rec_file.id, "state": "pending"})
        return rec_file

    def _confirm_capture(self, line, ip=None, ua=None):
        """The browser says its PUT is done: we check on the storage."""
        self.ensure_one()
        self._assert_open()
        if not line.file_id:
            raise UserError(_("No file to confirm for this document."))
        # A file from another transfer is not confirmed here.
        if line.sudo().file_id.transfer_id != self.transfer_id:
            raise UserError(_("Unknown document for this request."))
        if not line.file_id.sudo()._verify_on_s3():
            raise UserError(_(
                "The file did not arrive in full. Please take the photo again."))
        # sudo(): see ``_register_capture``.
        line.sudo().write({
            "state": "received",
            "received_at": fields.Datetime.now(),
        })
        log_env = self._log_env()
        self._log(
            "bf_document_doc_received", file=line.file_id, ip=ip, ua=ua,
            actor=self.partner_id.display_name,
            note=log_env._("\"%(doc)s\" uploaded (%(filename)s)",
                           doc=line.document_type_id.with_context(lang=log_env.lang).name,
                           filename=line.file_id.filename),
        )
        if self.state == "sent":
            # sudo(): the status is only written under sudo. The public
            # controller already calls under sudo; we do not depend on it.
            self.sudo().state = "partial"
        return True

    def _submit(self, ip=None, ua=None):
        """The client is done: the transfer is finalised and the employee is
        notified.

        The base's ``action_finalize`` checks each file on the storage again,
        pins the ETags, rotates the upload token (the upload capability dies
        here), arms the expiry and sends the link email.
        """
        self.ensure_one()
        self._assert_open()
        missing = self.line_ids.filtered(
            lambda line: line.required and line.state != "received")
        if missing:
            raise UserError(_(
                "Still missing: %s.",
                ", ".join(missing.mapped("document_type_id.name"))))
        if not self.line_ids.filtered(lambda line: line.state == "received"):
            raise UserError(_("No document has been uploaded."))
        # Optional lines left empty must not hold the file back: they are
        # closed cleanly.
        # sudo(): see ``_register_capture``.
        self.line_ids.filtered(
            lambda line: not line.required and line.state != "received"
        ).sudo().write({"state": "skipped"})

        result = self.transfer_id.sudo().action_finalize()
        # sender_confirmed=True at creation: the base cannot ask for a sender
        # code here. If that contract changed, we want to know.
        if isinstance(result, dict) and result.get("otp_required"):
            raise UserError(_(
                "The transfer is waiting for a confirmation that has no place "
                "in a document request. Please notify the administrator."))
        # sudo(): see ``_confirm_capture``.
        self.sudo().write({
            "state": "received",
            "completed_at": fields.Datetime.now(),
        })
        self._log(
            "bf_document_request_completed", ip=ip, ua=ua,
            actor=self.partner_id.display_name,
            note=self._log_env()._("Request %(name)s completed: %(n)s document(s) "
                                   "sent to %(who)s", name=self.name,
                                   n=self.received_count, who=self.user_id.name),
        )
        # Posted by the client's page: in the requester's language, not the
        # visitor's.
        self.message_post(body=self._requester_env()._(
            "The client uploaded %(n)s document(s). They have been sent to you "
            "by email.", n=self.received_count))
        return True

    # ------------------------------------------------------------------ scheduled jobs
    @api.model
    def _max_open_days(self):
        """Days after sending at which a request WITHOUT a deadline is closed
        automatically. 0: never. Parameter ``bf_document_request.max_open_days``."""
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "bf_document_request.max_open_days", "30")
        try:
            return max(int(raw), 0)
        except (TypeError, ValueError):
            return 30

    @api.model
    def _cron_expire_requests(self):
        """Closes the requests whose deadline has passed.

        A request sent WITHOUT a deadline is closed automatically after
        ``_max_open_days()`` days: its draft transfer is spared by the base's
        cleanup as long as the request is open, so it must not keep the
        uploaded documents, or a live link, forever.

        The link dies (token removed), not the trace. The draft transfer is
        expired: the base's garbage collector will purge its files.
        """
        now = fields.Datetime.now()
        open_requests = [("state", "in", ("sent", "partial"))]
        domain = expression.AND([open_requests, [
            ("deadline", "!=", False),
            ("deadline", "<", now),
        ]])
        days = self._max_open_days()
        if days:
            domain = expression.OR([domain, expression.AND([open_requests, [
                ("deadline", "=", False),
                ("sent_at", "<", now - timedelta(days=days)),
            ]])])
        stale = self.sudo().search(domain)
        for rec in stale:
            log_env = rec._log_env()
            if rec.deadline:
                note = log_env._("Deadline passed on %s",
                                 fields.Datetime.to_string(rec.deadline))
            else:
                note = log_env._("No deadline: request closed automatically %s "
                                 "days after it was sent.", days)
            rec._log("bf_document_request_expired", actor="cron", note=note)
            rec.write({"token": False, "state": "expired"})
            rec._close_draft(actor="cron")
        if stale:
            _logger.info("bf_document_request: %s request(s) expired", len(stale))
        return True

    @api.model
    def _cron_send_reminders(self):
        """Reminds incomplete files, through each request's channel.

        The first sending is not the problem: the file still incomplete three
        days later is. This job is therefore what really makes the product
        work.

        ⚠️ The quota guard is not decorative. VoIP.ms blocks at around 27
        messages in a burst, a quota that only resets at midnight. An unbounded
        automatic reminder can therefore eat the quota and make a FIRST
        sending fail: a client who never receives their request, which is far
        worse than a reminder that waits an hour. A share of the quota is
        therefore reserved, set by ``bf_document_request.reminder_daily_cap``.

        This cap is the text message quota's, and it ONLY bounds text
        messages: an email reminder costs nothing at VoIP.ms, and an exhausted
        text message quota must not deprive a client reached by email of their
        reminder. The per-request budget, the delay and the refusal to remind
        an overdue file apply to both channels. To turn off all reminders for
        a brand: ``bf_document_reminder_enabled``.
        """
        now = fields.Datetime.now()
        cap = int(self.env["ir.config_parameter"].sudo().get_param(
            "bf_document_request.reminder_daily_cap", 10))

        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        # Counts ALL of today's text message reminders, manual ones included:
        # the carrier's quota makes no difference between the two.
        already = self.sudo().search_count([
            ("last_reminder_at", ">=", day_start),
            ("channel", "=", "sms"),
        ])
        sms_budget = max(cap, 0) - already
        if sms_budget <= 0:
            _logger.info(
                "bf_document_request: text message reminder cap reached "
                "(%s/%s), only emails will go out", already, cap)

        candidates = self.sudo().search([
            ("state", "in", ("sent", "partial")),
            ("token", "!=", False),
            ("brand_id.bf_document_reminder_enabled", "=", True),
        ], order="last_reminder_at asc, sent_at asc")

        sent_sms = sent_mail = 0
        sms_blocked = sms_budget <= 0
        for rec in candidates:
            brand = rec.brand_id
            if rec.reminder_count >= brand.bf_document_reminder_max:
                continue
            # An overdue request is not reminded: the expiry job is about to
            # close it, and reminding someone about a link that dies within the
            # hour would only lead them on.
            if rec.deadline and rec.deadline <= now:
                continue
            since = rec.last_reminder_at or rec.sent_at
            if not since:
                continue
            delay = timedelta(hours=max(1, brand.bf_document_reminder_delay_hours))
            if now - since < delay:
                continue

            # The log note and the chatter message are read by the requester,
            # not by this job's user.
            log_env = rec._log_env()
            requester_env = rec._requester_env()
            if rec.channel == "email":
                if not email_normalize(rec.email or ""):
                    _logger.info(
                        "bf_document_request: email reminder of %s skipped, "
                        "address missing or invalid", rec.name)
                    continue
                # One savepoint per request: a request that raises (badly set
                # up brand, damaged data) must neither undo the reminders
                # already made in this run, nor deprive the next ones of
                # theirs. What it started writing goes away with the
                # savepoint.
                try:
                    with self.env.cr.savepoint():
                        destination = rec._send_link_email(reminder=True)
                except Exception:
                    _logger.exception(
                        "bf_document_request: email reminder of %s failed, "
                        "request skipped", rec.name)
                    continue
                via = log_env._("by email to %s", _mask_email(destination))
                via_chatter = requester_env._("by email")
                sent_mail += 1
            else:
                if sms_blocked or sent_sms >= sms_budget:
                    continue
                phone = sms.normalize_na(rec.phone or "")
                if not phone:
                    continue
                if not sms.send(self.env, phone,
                                rec._client_env_record()._sms_reminder_body()):
                    # sms.send never raises, it returns False. A failure here is
                    # most often an exhausted carrier quota: insisting on the
                    # next text messages would only burn attempts. The text
                    # messages of this run stop (emails go on) and the next run
                    # takes over, without touching the counters, so that this
                    # request keeps its budget.
                    _logger.warning(
                        "bf_document_request: text message reminder refused "
                        "for %s, text messages of this run stopped", rec.name)
                    sms_blocked = True
                    continue
                via = log_env._("by text message to %s", _mask_phone(rec.phone))
                via_chatter = requester_env._("by text message")
                sent_sms += 1

            rec.write({
                "reminder_count": rec.reminder_count + 1,
                "last_reminder_at": now,
            })
            rec._log("bf_document_reminder_sent", actor="cron",
                     note=log_env._("Automatic reminder %(i)s/%(max)s %(via)s",
                                    i=rec.reminder_count,
                                    max=brand.bf_document_reminder_max,
                                    via=via))
            rec.message_post(body=requester_env._(
                "Automatic reminder sent %(via)s (%(i)s of %(max)s).",
                via=via_chatter, i=rec.reminder_count,
                max=brand.bf_document_reminder_max))

        if sent_sms or sent_mail:
            _logger.info(
                "bf_document_request: %s text message and %s email "
                "reminder(s) sent", sent_sms, sent_mail)
        return True


class BfDocumentRequestLine(models.Model):
    _name = "bf.document.request.line"
    _description = "Requested document"
    _order = "sequence, id"

    request_id = fields.Many2one(
        "bf.document.request",
        string="Request",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    document_type_id = fields.Many2one(
        "bf.document.type",
        string="Document",
        required=True,
    )
    required = fields.Boolean(
        string="Required",
        default=True,
        help="An optional document does not prevent the client from submitting.",
    )
    state = fields.Selection(
        selection=[
            ("pending", "Expected"),
            ("received", "Uploaded"),
            ("skipped", "Not provided"),
        ],
        default="pending",
        required=True,
        copy=False,
    )
    file_id = fields.Many2one(
        "secure.transfer.file",
        string="File",
        readonly=True,
        copy=False,
        ondelete="set null",
    )
    received_at = fields.Datetime(readonly=True, copy=False)
    instructions = fields.Text(related="document_type_id.instructions")

    _sql_constraints = [
        (
            "doc_type_uniq",
            "UNIQUE(request_id, document_type_id)",
            "This document is already requested in this request.",
        ),
    ]

    # ------------------------------------------------------------------ guards
    # A requested document's file, status and date received are only written
    # under sudo, by the client's upload and the submission; a line does not
    # move from one request to another. The form keeps adding, removing and
    # reordering documents: type, "required" and order stay free.
    def _refuse_line_fields(self):
        raise AccessError(_(
            "The file, status and date received of a requested document can "
            "only be changed by the client's upload."))

    @api.model_create_multi
    def create(self, vals_list):
        # A line is born expected, without file or date received. The context
        # defaults of these fields are removed, as for the request: a line
        # created with ``default_state`` is born expected.
        if not self.env.su:
            self = _without_defaults(self, _LINE_FIELDS)
        lines = super().create(vals_list)
        # Then we look at what was born: ``vals`` (the form sends "pending"
        # and empty values, which pass) and a personal default
        # (``ir.default``), which goes through neither ``vals`` nor the
        # context. Unlike the request, there is no sequence number to spare:
        # reading ``vals`` before the creation would refuse nothing more.
        if not self.env.su:
            for line in lines.sudo():
                if line.state != "pending" or line.file_id or line.received_at:
                    self._refuse_line_fields()
        return lines

    def write(self, vals):
        if not self.env.su:
            line_fields = [fname for fname in _LINE_FIELDS if fname in vals]
            # Compared under sudo, like the request's state fields: an
            # unchanged value sent back by the form passes.
            if line_fields and any(_value_changes(line, fname, vals[fname])
                                   for line in self.sudo() for fname in line_fields):
                self._refuse_line_fields()
            if "request_id" in vals and any(
                    _value_changes(line, "request_id", vals["request_id"])
                    for line in self.sudo()):
                raise AccessError(_(
                    "A requested document does not move from one request to "
                    "another: remove it, then add it to the other request."))
        return super().write(vals)

    def unlink(self):
        # A received document's upload is written in the transfer's chained
        # log: removing its line would leave the log naming a document the
        # request no longer has. Pending documents can still be removed.
        if not self.env.su and any(line.state == "received" for line in self.sudo()):
            raise UserError(_(
                "A document the client has already uploaded cannot be removed "
                "from the request: its upload is recorded in the log."))
        return super().unlink()

    def _drop_file(self):
        """Removes a retaken photo's file from storage."""
        self.ensure_one()
        rec_file = self.file_id.sudo()
        if not rec_file:
            return
        # Only a file of the request's transfer: a line pointing to another
        # transfer's file would get it deleted, under sudo, storage and record
        # included.
        if rec_file.transfer_id != self.sudo().request_id.transfer_id:
            raise UserError(_("Unknown document for this request."))
        try:
            if rec_file.upload_mode == "multipart" and rec_file.s3_upload_id:
                rec_file.mpu_abort()
            rec_file._s3_delete()
        except Exception:
            # At worst the object stays orphaned: the base's garbage collector
            # will sweep it. Never block a retake on that.
            _logger.warning(
                "bf_document_request: best-effort storage cleanup failed for "
                "file #%s", rec_file.id, exc_info=True)
        self.sudo().write({"file_id": False, "state": "pending"})
        rec_file.unlink()
