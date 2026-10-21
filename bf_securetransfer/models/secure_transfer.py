"""A secure transfer: a batch of files shared through a tokenized link.

Lifecycle: draft (upload phase, capability = upload_token) → active (link
live, capability = token) → expired (date, download budget, or operator)
→ deleted (S3 purged, metadata + access trail KEPT) → hard GC after the log
retention period (the only path that ever unlinks). Abandoned drafts are
harvested to cancelled. suspended is the abuse kill-switch.

All public entry points are called in sudo() by the controllers behind
token checks — the public user has NO direct ACL on these models.
"""
import base64
import csv
import hmac
import io
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from passlib.context import CryptContext
from werkzeug.utils import secure_filename

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import email_normalize, email_split, formataddr, html_escape
from odoo.tools import hmac as odoo_hmac  # aliased: `hmac` above is the stdlib one

from . import pdf_watermark
from . import s3
from . import sms
from .secure_transfer_file import DENY_EXTENSIONS

_logger = logging.getLogger(__name__)


def active_lang(env, code=None):
    """``code`` when it is active in this database, else an active language.

    The fallback order is another active variant of the same language (en_CA
    asked, en_US active: en_US), fr_CA, the company's language, then the first
    active language. ⚠️ Never a language written in stone: ``Environment.lang``
    raises « Invalid language code » for one that is not active, and the old
    hardcoded fr_CA turned every public page into a 400 on a database without
    it. ⚠️ The same-language step comes BEFORE fr_CA: without it an English
    reader on a database with fr_CA and en_US (but no en_CA) got French.
    """
    # sudo: the public pages call this as the public user.
    actives = [c for c, _name in env["res.lang"].sudo().get_installed()]
    same = []
    if code and code not in actives:
        prefix = code.split("_")[0] + "_"
        # en_US first among the English variants: it is the source language.
        same = sorted((c for c in actives if c.startswith(prefix)),
                      key=lambda c: c != "en_US")
    for candidate in (code, *same[:1], "fr_CA", env.company.sudo().partner_id.lang):
        if candidate and candidate in actives:
            return candidate
    return actives[0] if actives else "en_US"

# Same scheme res.users relies on — zero new dependency.
_pwd_ctx = CryptContext(schemes=["pbkdf2_sha512"])

MAX_RECIPIENTS = 10
MAX_MESSAGE_LEN = 2000
MAX_NAME_LEN = 128
# The subject rides in a mail header, so it is a single line and it is short:
# past ~120 characters every mail client truncates it anyway, and the field
# exists to be READ in an inbox list.
MAX_SUBJECT_LEN = 120
# Public form ceiling for the download budget (0 = unlimited stays possible).
MAX_DOWNLOAD_BUDGET = 100
# Bucket sweep grace: an MPU younger than this may belong to a live upload.
MPU_SWEEP_GRACE_HOURS = 48
# What replaces the token in the bodies the record retains. Deliberately a
# readable, obviously-dead placeholder rather than a blank: the reader sees
# THAT a link was sent and that it was masked, and clicking it lands on the
# neutral "no longer available" page instead of the files.
SHARE_TOKEN_MASK = "jeton-masque"


class SecureTransfer(models.Model):
    _name = "secure.transfer"
    _description = "Secure transfer"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(
        string="Reference", required=True, readonly=True, copy=False,
        default=lambda self: _("New"),
    )
    # Two distinct capabilities (defense in depth): upload_token drives the
    # draft/upload API and is rotated (killed) at finalize; token drives the
    # /s/ share page and is inert until activation. Stored in clear
    # (hash-at-rest would break link resend) but manager-only, with the
    # logged reveal wizard as the only backend exposure path.
    token = fields.Char(
        string="Share token",
        required=True,
        index=True,
        copy=False,
        groups="bf_securetransfer.group_securetransfer_manager",
        default=lambda self: uuid.uuid4().hex,
    )
    upload_token = fields.Char(
        string="Upload token",
        required=True,
        index=True,
        copy=False,
        groups="bf_securetransfer.group_securetransfer_manager",
        default=lambda self: uuid.uuid4().hex,
    )
    # Opaque S3 prefix for this transfer's objects — never the tokens (a
    # capability must not leak into bucket listings or presigned URLs).
    s3_prefix = fields.Char(
        string="S3 prefix",
        required=True,
        readonly=True,
        copy=False,
        default=lambda self: uuid.uuid4().hex,
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("active", "Active"),
            ("expired", "Expired"),
            ("deleted", "Deleted"),
            ("cancelled", "Canceled"),
            ("suspended", "Suspended"),
        ],
        string="Status",
        default="draft",
        required=True,
        copy=False,
        tracking=True,
        index=True,
    )
    brand_id = fields.Many2one(
        "secure.transfer.brand",
        string="Brand",
        required=True,
        ondelete="restrict",
        index=True,
    )
    company_id = fields.Many2one(
        related="brand_id.company_id", store=True, string="Company",
    )
    sender_name = fields.Char(string="Sender name")
    sender_email = fields.Char(
        # Optional at draft creation (a file can be dropped first); required at
        # finalize — enforced in action_finalize, not by a NOT NULL constraint.
        string="Sender email", index=True,
    )
    recipient_emails = fields.Char(
        string="Recipients",
        help="Recipient addresses, comma-separated (maximum %s)." % MAX_RECIPIENTS,
    )
    # The one line that tells the recipient WHAT this is before they open
    # anything. Deliberately outside every gate: it travels in clear in the
    # mail header (the password and the recipient code hold the CONTENT, not
    # this). The public form says so under the field, and _clean_line strips
    # CR/LF — a header field must never carry a line break (injection).
    subject = fields.Char(
        string="Subject",
        help="A short line telling the recipient what this is (e.g. "
             "\"2026 leases\"). It appears in the email subject and as "
             "the page title: nothing confidential belongs in it.",
    )
    message = fields.Text(
        string="Message",
        help="Sender's message (plain text, rendered escaped, never "
             "interpreted as HTML).",
    )
    locale = fields.Selection(
        selection=[("fr_CA", "French (Canada)"), ("en_CA", "English (Canada)")],
        string="Language",
        default="fr_CA",
        required=True,
    )
    retention_days = fields.Integer(
        string="Retention (days)",
        default=lambda self: self._default_retention_days(),
    )
    expiry_date = fields.Datetime(string="Expires on", copy=False)
    password_hash = fields.Char(
        string="Password hash",
        copy=False,
        groups="base.group_system",
    )
    has_password = fields.Boolean(
        string="Password protected",
        compute="_compute_has_password",
        store=True,  # stored so the search filter « Protégé » can query it
    )
    max_downloads = fields.Integer(
        string="Max. downloads",
        default=0,
        help="0 = unlimited. Applied immediately, not exposed on the "
             "public form in the MVP.",
    )
    download_count = fields.Integer(
        string="Downloads", default=0, copy=False,
    )
    # Phase 2 placeholders — schema ready, hooks land at the _log choke-point.
    burn_after_download = fields.Boolean(
        string="Destroy after reading (Phase 2)", default=False,
    )
    notify_on_download = fields.Boolean(
        string="Notify on download", default=False,
    )
    # Sender OTP (confirmation of send): when the tenant requires it, finalize
    # emails a code to the sender and only activates once it is confirmed —
    # proves the sender controls the From address (anti-spoof/piggyback).
    sender_confirmed = fields.Boolean(default=False, copy=False)
    sender_otp_hash = fields.Char(copy=False, groups="base.group_system")
    sender_otp_expiry = fields.Datetime(copy=False)
    sender_otp_fails = fields.Integer(default=0, copy=False)
    # Recipient OTP — the "hold the mail until identity is proven" gate. The
    # tenant setting require_recipient_otp is the instance-wide default; this
    # per-transfer flag FORCES the gate on regardless (set by a secure send
    # from the backend). _recipient_otp_required() ORs the two.
    force_recipient_otp = fields.Boolean(
        string="Require a code from the recipient",
        default=False,
        help="The recipient must enter a one-time code before the message "
             "and files are shown; the content stays withheld until this "
             "proof of identity.",
    )
    # What the operator actually needs to read off the form: the EFFECTIVE
    # gate. force_recipient_otp alone is misleading — an instance that requires
    # the code for every transfer leaves that box unticked, so a gated transfer
    # looks wide open (and its retained link looks like a leak when it is not).
    recipient_otp_status = fields.Selection(
        selection=[
            ("off", "None: the link alone opens the content"),
            ("transfer", "Required for this transfer"),
            ("instance", "Required by the instance setting"),
            ("audience", "Required: open audience"),
        ],
        string="Recipient code",
        compute="_compute_recipient_otp_status",
    )
    recipient_otp_channel = fields.Selection(
        selection=[("email", "Email"), ("sms", "SMS")],
        string="Recipient code channel",
        default="email",
        required=True,
        help="Delivery channel for the recipient's code. \"SMS\" requires "
             "a known mobile number for the recipient; otherwise, email "
             "automatically takes over.",
    )
    # email_normalisé -> numéro (10 chiffres NANP), sérialisé JSON. Alimenté
    # seulement par l'envoi sécurisé backend (le formulaire public ne collecte
    # pas de téléphone) — sinon vide.
    recipient_sms_map = fields.Text(copy=False)
    # -- Audience ouverte ------------------------------------------------------
    # « declared » = le mode historique : les destinataires sont nommés à
    # l'envoi et le code ne fait que prouver qu'on est l'un d'eux.
    # « open » = le lien ne nomme personne : le visiteur déclare son adresse,
    # reçoit un code, et sa confirmation l'inscrit dans `audience_ids`.
    audience_mode = fields.Selection(
        selection=[
            ("declared", "Named recipients"),
            ("open", "Open audience (the visitor identifies themselves)"),
        ],
        string="Delivery mode",
        default="declared",
        required=True,
        tracking=True,
        help="\"Open audience\": the link targets no one in particular. "
             "Anyone who opens it enters their address, receives a "
             "one-time code and only reaches the content after confirming "
             "it.",
    )
    audience_domains = fields.Text(
        string="Allowed domains",
        help="Allowlist of self-declared addresses (full address or "
             "domain \"@client.com\", one per line or comma-separated). "
             "Empty = the brand's list.",
    )
    audience_max = fields.Integer(
        string="Max. visitors",
        default=0,
        help="Maximum number of distinct addresses allowed. 0 = brand "
             "value. This cap is what keeps a leaked link from becoming "
             "an email relay to third parties.",
    )
    audience_max_downloads = fields.Integer(
        string="Downloads per visitor",
        default=0,
        help="0 = unlimited. Budget applied to EACH visitor separately; "
             "\"Max. downloads\" remains the overall cap of the transfer.",
    )
    audience_allow_sms = fields.Boolean(
        string="Code by SMS offered",
        default=False,
        help="Lets the visitor request their code on their mobile. No "
             "effect if the brand does not offer it or if the SMS channel "
             "is not set up.",
    )
    notify_on_join = fields.Boolean(
        string="Notify on each new visitor",
        default=True,
        help="Notifies the sender as soon as a new address is confirmed. "
             "On an open audience, it is the only way to know who comes "
             "in: the download notice is sent only once.",
    )
    audience_ids = fields.One2many(
        "secure.transfer.audience", "transfer_id", string="Audience",
    )
    audience_count = fields.Integer(
        string="Confirmed visitors", compute="_compute_audience_count",
    )
    # Float on purpose: Integer maps to int4 and overflows at 2.1 GB.
    total_size = fields.Float(
        string="Total size (bytes)",
        compute="_compute_total_size",
        store=True,
    )
    file_ids = fields.One2many(
        "secure.transfer.file", "transfer_id", string="Files",
    )
    file_count = fields.Integer(compute="_compute_file_count", string="Files "
                                                                      "(count)")
    access_log_ids = fields.One2many(
        "secure.transfer.access.log", "transfer_id", string="Access log",
    )
    ip_created = fields.Char(string="Creation IP", readonly=True, index=True)
    ua_created = fields.Char(string="User agent (creation)", readonly=True)
    finalized_at = fields.Datetime(string="Finalized on", readonly=True, copy=False)
    purged_at = fields.Datetime(string="Purged on", readonly=True, copy=False)
    purge_error_count = fields.Integer(
        string="Purge failures", default=0, copy=False,
    )

    _sql_constraints = [
        ("token_uniq", "unique(token)", "This share token already exists."),
        ("upload_token_uniq", "unique(upload_token)",
         "This upload token already exists."),
    ]

    # ------------------------------------------------------------------ retention guard
    @api.model
    def _default_retention_days(self):
        """Défaut aligné sur ce que la marque offre RÉELLEMENT.

        ⚠ Le défaut était `7` en dur. Une marque dont `max_retention_days` tombe
        entre 1 et 6 ne propose que `[1]` : le défaut violait alors à lui seul la
        contrainte ci-dessous, sur un champ que l'appelant n'avait même pas
        renseigné.

        ⚠ On garde 7 quand 7 est offert. Prendre systématiquement `choices[0]`
        aurait fait tomber le défaut de 7 à 1 jour sur TOUS les locataires — un
        raccourcissement silencieux de la durée de vie des liens d'envoi interne,
        que personne n'a demandé. Le repli va vers la durée la plus COURTE offerte,
        jamais la plus longue : sur un produit de confidentialité, se tromper de
        défaut doit se tromper du côté prudent.
        """
        brand = self.env["secure.transfer.brand"].browse(
            self.env.context.get("default_brand_id")
        )
        if not brand.exists():
            brand = self.env["secure.transfer.brand"].search([], limit=1)
        if not brand:
            return 7
        choices = brand._effective_limits()["expiry_choices"]
        if not choices:
            return 7
        return 7 if 7 in choices else choices[0]

    @api.constrains("retention_days", "brand_id")
    def _check_retention_offered(self):
        """La durée de rétention doit être une de celles que la marque offre.

        ⚠ Ce contrôle n'existait QUE dans `api_create()`, c'est-à-dire sur le seul
        chemin public. L'assistant d'envoi interne, le `write` de finalisation de
        `upload_api` et tout appel ORM/XML-RPC pouvaient poser n'importe quelle
        durée. Or la règle de cycle de vie du seau S3 est écrite d'après la plus
        longue rétention qu'une marque peut accorder : une durée hors grille
        promet au client une date que le fournisseur de stockage ne tiendra pas.

        Posé en `@api.constrains` plutôt que dans `create()` : il couvre ainsi
        create ET write d'un seul geste, et Odoo 18 injecte les défauts avant
        validation, donc il mord même quand l'appelant omet le champ.
        """
        for rec in self:
            if not rec.brand_id:
                continue
            choices = rec.brand_id._effective_limits()["expiry_choices"]
            if rec.retention_days not in choices:
                raise ValidationError(_(
                    "Retention period not offered for \"%(brand)s\": "
                    "%(asked)s day(s) requested, while this service "
                    "offers %(choices)s.",
                    brand=rec.brand_id.display_name,
                    asked=rec.retention_days,
                    choices=", ".join(str(c) for c in choices),
                ))

    # ------------------------------------------------------------------ computes / CRUD
    @api.depends("file_ids.size")
    def _compute_total_size(self):
        for rec in self:
            rec.total_size = sum(rec.file_ids.mapped("size"))

    @api.depends("file_ids")
    def _compute_file_count(self):
        for rec in self:
            rec.file_count = len(rec.file_ids)

    @api.depends("force_recipient_otp", "audience_mode")
    def _compute_recipient_otp_status(self):
        # The instance setting is read once per batch: it is the same answer
        # for every record and it costs a config-parameter query.
        instance = self._needs_recipient_otp_param()
        for rec in self:
            rec.recipient_otp_status = (
                # L'audience ouverte passe en premier : c'est le motif le plus
                # fort (le code n'y est pas une option, il EST le contrôle
                # d'accès) et celui que l'opérateur doit lire.
                "audience" if rec.audience_mode == "open"
                else "transfer" if rec.force_recipient_otp
                else "instance" if instance
                else "off"
            )

    @api.depends("audience_ids.state")
    def _compute_audience_count(self):
        # Les visiteurs seulement « en attente » (code envoyé, jamais validé)
        # ne comptent pas : le chiffre lu par l'opérateur doit être celui des
        # gens qui ont réellement franchi la porte.
        for rec in self:
            rec.audience_count = len(
                rec.audience_ids.filtered(lambda a: a.state == "confirmed"))

    @api.constrains("audience_mode", "brand_id")
    def _check_audience_mode_offered(self):
        """L'audience ouverte n'existe que si la marque l'offre.

        Posé en contrainte et pas seulement dans l'assistant : le mode
        s'écrit aussi par ORM et par XML-RPC, et il ouvre un lien à des
        adresses que personne n'a validées en amont. La marque est le seul
        endroit où l'opérateur a dit oui."""
        for rec in self:
            if rec.audience_mode != "open" or not rec.brand_id:
                continue
            if not rec.brand_id.allow_open_audience:
                raise ValidationError(_(
                    "The \"%s\" brand does not offer the open audience. "
                    "Turn it on for the brand before using it.",
                    rec.brand_id.display_name,
                ))

    @api.depends("password_hash")
    def _compute_has_password(self):
        for rec in self:
            # sudo: password_hash is group-restricted; only its presence leaks.
            rec.has_password = bool(rec.sudo().password_hash)

    # Fields whose value ends up in a mail HEADER. Odoo hands them to Python's
    # email stack, which REFUSES a value carrying CR/LF (ValueError, raised in
    # Header()). So a stray line break does not inject a header — it kills the
    # delivery: mail.mail lands in `exception`, and the sender was already told
    # « transfert prêt ». Nobody finds out.
    #
    # ⚠ Cleaning in the controllers was not enough, and the gap was real:
    # upload_api's finalize route wrote `sender_name` through its own helper
    # (strip + truncate, no CR/LF filter), and any ORM/XML-RPC write bypassed
    # the controllers entirely. create/write is the single place every entry
    # point has to go through.
    _HEADER_FIELDS = {
        "sender_name": MAX_NAME_LEN,
        "subject": MAX_SUBJECT_LEN,
        # Comma-joined list, capped well above MAX_RECIPIENTS × address length.
        "recipient_emails": MAX_RECIPIENTS * 256,
    }

    @api.model
    def _normalize_header_fields(self, vals):
        """Strip CR/LF and control characters, cap the length, in place.
        Falsy values are left alone so clearing a field stays possible."""
        for fname, maxlen in self._HEADER_FIELDS.items():
            if vals.get(fname):
                vals[fname] = self._clean_line(vals[fname], maxlen)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._normalize_header_fields(vals)
            if not vals.get("name") or vals["name"] == _("New"):
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("secure.transfer")
                    or _("New")
                )
        return super().create(vals_list)

    def write(self, vals):
        self._normalize_header_fields(vals)
        return super().write(vals)

    def unlink(self):
        # Metadata + access trail survive the S3 purge by design (Loi 25).
        # The only legal deletion path is the hard GC after the retention
        # period, which sets the st_gc context.
        if not self.env.context.get("st_gc"):
            raise UserError(_(
                "Transfers cannot be deleted: the access log must be "
                "kept. Use \"Expire\" or \"Purge\" instead."
            ))
        return super().unlink()

    # ------------------------------------------------------------------ journal choke-point
    def _log(self, action, file=None, ip=None, ua=None, actor=None, note=None):
        """EVERY event goes through here (single choke-point — the Phase 2
        burn/notify hooks plug into this seam). Delegates to the append-only
        chained log in sudo."""
        self.ensure_one()
        return self.env["secure.transfer.access.log"].sudo()._append(
            self, action, file=file, ip=ip, user_agent=ua, actor=actor, note=note,
        )

    # ------------------------------------------------------------------ locking / quotas
    def _lock_row(self):
        """Row lock (SELECT ... FOR UPDATE) serializing quota checks,
        finalize and download accounting across workers. flush+invalidate so
        the ORM re-reads the row as the lock winner left it."""
        self.ensure_one()
        self.env.flush_all()  # pending ORM writes must hit the DB first
        self.env.cr.execute(
            "SELECT id FROM secure_transfer WHERE id = %s FOR UPDATE",
            (self.id,),
        )
        self.invalidate_recordset()

    @api.model
    def _day_start(self):
        """Start of the current UTC day (create_date is naive UTC)."""
        return fields.Datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    @api.model
    def _daily_usage(self, domain):
        """(count, declared_bytes) of today's transfers matching ``domain``.
        DB counters (not in-memory dicts): per-worker dicts reset and lie
        about daily quotas."""
        rows = self.sudo()._read_group(
            [("create_date", ">=", self._day_start())] + domain,
            groupby=[],
            aggregates=["__count", "total_size:sum"],
        )
        count, declared = rows[0] if rows else (0, 0.0)
        return int(count or 0), float(declared or 0.0)

    @api.model
    def _check_sender_quota(self, sender_email, exclude_id=None):
        """Per-sender daily transfer quota, under an advisory lock (TOCTOU).
        Raises UserError when exceeded. Called at create (if the e-mail is
        known then) and at finalize.

        ``exclude_id`` is what makes the finalize call honest: by then the
        transfer being finalized is already in the DB (and _lock_row has
        flushed), so it would COUNT ITSELF. Without it a quota of 5 really
        allowed 4, and the refusal landed after the upload was done — the
        worst possible moment, with no way for the sender to recover."""
        self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                            ("bf_st_create_sender:" + (sender_email or ""),))
        quota = s3._int_param(self.env, "quota_daily_transfers_per_sender", 5)
        domain = [("sender_email", "=", sender_email)]
        if exclude_id:
            domain.append(("id", "!=", exclude_id))
        count, _b = self._daily_usage(domain)
        if count >= quota:
            raise UserError(_(
                "Daily transfer limit reached for this sender address. "
                "Try again tomorrow."))

    # ------------------------------------------------------------------ public API — create
    @api.model
    def api_create(self, brand, vals, ip, ua, locale):
        """Create a draft transfer for the public upload page. Validates
        everything server-side and enforces the daily DB quotas (IP +
        sender email). Raises UserError with a safe message on any refusal.
        """
        limits = brand._effective_limits()

        # Email is OPTIONAL at draft creation (the browser creates the draft as
        # soon as the user starts, before filling the form), and REQUIRED at
        # finalize. This is what lets a file be dropped before the e-mail is
        # typed without an error. When present, it is validated here too.
        sender_email = email_normalize(vals.get("sender_email") or "")
        if sender_email and not brand._sender_allowed(sender_email):
            raise UserError(_(
                "This email address is not authorized to send from this "
                "service. Contact this instance's administrator."
            ))
        sender_name = self._clean_line(vals.get("sender_name"), MAX_NAME_LEN)

        # Drop page ("Dropbox"-style): the recipient is FORCED to the brand's
        # single fixed address, whatever the client submitted — the page can
        # only ever send to its owner (no piggyback, no redirection).
        if brand.fixed_recipient:
            recipients = [brand._drop_recipient()]
        else:
            recipients = self._clean_recipients(vals.get("recipient_emails"))
            # Anti-piggyback (destination side): a locked instance may only send
            # to its own domain(s). Reject the transfer if any recipient is off.
            bad = [r for r in recipients if not brand._recipient_allowed(r)]
            if bad:
                raise UserError(_(
                    "This service only allows sending to certain "
                    "addresses. Unauthorized recipient(s): %s", ", ".join(bad)))

        message = (vals.get("message") or "").strip()
        if len(message) > MAX_MESSAGE_LEN:
            raise UserError(_(
                "The message is limited to %s characters.", MAX_MESSAGE_LEN,
            ))
        # _clean_line, not a bare strip: the subject ends up in a mail header,
        # where a CR/LF would let a sender inject headers of their own.
        subject = self._clean_line(vals.get("subject"), MAX_SUBJECT_LEN)

        try:
            retention = int(vals.get("retention_days") or 0)
        except (TypeError, ValueError):
            retention = 0
        if not retention:
            # ⚠ Le repli allait vers `choices[-1]`, c'est-à-dire la durée la plus
            # LONGUE offerte. Sur un produit de confidentialité, un défaut absent
            # ne doit pas se résoudre en « garde le fichier le plus longtemps
            # possible ». Repli vers la plus courte, 7 restant privilégié quand il
            # est offert — mêmes règles que `_default_retention_days`.
            choices = limits["expiry_choices"]
            retention = (7 if 7 in choices else choices[0]) if choices else 7
        if retention not in limits["expiry_choices"]:
            # Message d'usage sur le chemin public ; la ceinture est le
            # `@api.constrains` `_check_retention_offered`, qui couvre les
            # chemins internes.
            raise UserError(_("Retention period not offered for this service."))

        try:
            max_downloads = max(0, int(vals.get("max_downloads") or 0))
        except (TypeError, ValueError):
            max_downloads = 0

        # -- daily quotas (counted on creation regardless of final state:
        #    an abandoned draft still consumed the quota — anti-abuse).
        #    Serialize the count-then-create per IP and per sender with
        #    transaction-scoped advisory locks: without them, concurrent
        #    requests all read the same pre-commit count and every quota is
        #    bypassed by a burst (TOCTOU). The locks release at commit/rollback.
        cr = self.env.cr
        cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                   ("bf_st_create_ip:" + (ip or ""),))
        quota_ip = s3._int_param(self.env, "quota_daily_transfers_per_ip", 25)
        count_ip, _bytes_ip = self._daily_usage([("ip_created", "=", ip or "")])
        if count_ip >= quota_ip:
            raise UserError(_(
                "Daily transfer limit reached for your connection. Try "
                "again tomorrow."
            ))
        # The per-sender quota needs the e-mail; enforced at finalize (see
        # _check_sender_quota) since the e-mail may be blank at draft creation.
        if sender_email:
            self._check_sender_quota(sender_email)

        rec = self.create({
            "brand_id": brand.id,
            "sender_name": sender_name,
            "sender_email": sender_email,
            "recipient_emails": ", ".join(recipients),
            "subject": subject,
            "message": message,
            "retention_days": retention,
            "max_downloads": max_downloads,
            # Inherit the brand's download-notification policy at creation.
            "notify_on_download": brand.notify_on_download,
            "locale": locale if locale in ("fr_CA", "en_CA") else "fr_CA",
            "ip_created": ip or "",
            "ua_created": (ua or "")[:512],
            "state": "draft",
        })
        rec._log(
            "created", ip=ip, ua=ua,
            note=rec._log_env()._("Sender %s, %s recipient(s), retention %s day(s)")
            % (sender_email, len(recipients), retention),
        )
        return rec

    @staticmethod
    def _clean_line(value, maxlen):
        """One display line: control chars / CR-LF stripped (anti-spoofing,
        anti log-injection), length capped."""
        raw = (value or "").strip()
        return "".join(
            c for c in raw if c.isprintable() and c not in "\r\n"
        )[:maxlen]

    @api.model
    def _clean_recipients(self, raw):
        """Normalize, validate and dedupe the recipient list (≤10). An empty
        list is allowed: link-only mode — the sender receives the link in the
        receipt and shares it themselves."""
        if isinstance(raw, (list, tuple)):
            raw = ",".join(str(r) for r in raw)
        recipients = []
        for addr in email_split(raw or ""):
            normalized = email_normalize(addr)
            if not normalized:
                raise UserError(_("Invalid recipient address."))
            if normalized not in recipients:
                recipients.append(normalized)
        if len(recipients) > MAX_RECIPIENTS:
            raise UserError(_(
                "Maximum %s recipients per transfer.", MAX_RECIPIENTS,
            ))
        return recipients

    # ------------------------------------------------------------------ public API — files
    def _register_file(self, filename, size):
        """Register one file on a draft transfer under the row lock: name
        sanitation, deny-list, per-transfer and daily-IP quotas, server-side
        key/mimetype, upload mode decision. Returns the file record."""
        self.ensure_one()
        self._lock_row()
        if self.state != "draft":
            raise UserError(_("This transfer no longer accepts files."))
        limits = self.brand_id._effective_limits()

        # -- name sanitation (bf_survey_upload pattern): secure_filename
        # sanitises; the stored display name keeps Unicode but drops path
        # components, control chars and CR-LF, capped at 255.
        stripped = secure_filename(filename or "") or ""
        raw_name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        # The extension comes from the RAW name, never from secure_filename's
        # output. secure_filename drops non-ASCII then strips leading "._", so
        # "документ.pdf" collapses to "pdf" — no dot left, and a perfectly
        # legitimate file was refused with "must have an extension", a message
        # its owner could only read as wrong. Worse for the deny-list: it saw
        # "" instead of "php" for "рисунок.php", so the block was incidental
        # rather than by rule. Keep only the alphanumerics of the raw suffix:
        # enough to match DENY_EXTENSIONS, and safe to store.
        raw_ext = raw_name.rsplit(".", 1)[-1] if "." in raw_name else ""
        ext = "".join(c for c in raw_ext if c.isalnum()).lower()[:32]
        if not ext:
            raise UserError(_("The file must have an extension."))
        if ext in DENY_EXTENSIONS:
            raise UserError(_(
                "The \".%s\" format is not allowed for security reasons.", ext,
            ))
        display_name = "".join(
            c for c in raw_name if c.isprintable() and c not in "\r\n"
        )[:255] or stripped or "fichier"

        try:
            size = float(size)
        except (TypeError, ValueError):
            raise UserError(_("Invalid file size."))
        if size <= 0:
            raise UserError(_("Invalid file size."))

        # -- per-transfer limits
        if len(self.file_ids) >= limits["max_files"]:
            raise UserError(_(
                "Maximum %s files per transfer.", limits["max_files"],
            ))
        declared = sum(self.file_ids.mapped("size"))
        if declared + size > limits["max_bytes"]:
            raise UserError(_(
                "The total size exceeds this service's limit of %s MB.",
                limits["max_bytes"] // (1024 * 1024),
            ))

        # -- daily declared-bytes quota per IP (this transfer's already
        #    registered files are included via the stored total_size)
        quota_mb = s3._int_param(self.env, "quota_daily_bytes_per_ip_mb", 10240)
        _count, day_bytes = self._daily_usage(
            [("ip_created", "=", self.ip_created or "")],
        )
        if day_bytes + size > quota_mb * 1024 * 1024:
            raise UserError(_(
                "Daily volume limit reached for your connection. Try "
                "again tomorrow."
            ))

        threshold = s3._int_param(self.env, "multipart_threshold_mb", 64) * 1024 * 1024
        FileModel = self.env["secure.transfer.file"]
        return FileModel.create({
            "transfer_id": self.id,
            "filename": display_name,
            "extension": ext,
            "size": size,
            # Server-derived, never client-provided; informative only.
            "mimetype": FileModel._guess_mimetype(display_name),
            "s3_key": FileModel._key_for(self),
            "upload_mode": "multipart" if size > threshold else "simple",
            "state": "pending",
        })

    # ------------------------------------------------------------------ backend origination
    # The backend composer (secure.transfer.send.wizard) is the ONE path where
    # file content goes through Odoo. The public page never does: the browser
    # PUTs straight to S3 and the worker only ever sees metadata. A backend
    # form has no such option, so the whole file sits in worker memory for the
    # duration of the send — hence two ceilings, a tunable one and one that no
    # setting can lift. Past them the honest answer is "use /secrets", which
    # streams multi-Go without loading a byte here.
    _BACKEND_UPLOAD_HARD_MAX_MB = 100

    @api.model
    def _backend_max_upload_bytes(self):
        """Ceiling for ONE backend-composed transfer, all files together."""
        mb = s3._int_param(self.env, "backend_max_upload_mb", 25)
        return max(1, min(int(mb), self._BACKEND_UPLOAD_HARD_MAX_MB)) * 1024 * 1024

    def _backend_add_file(self, filename, data):
        """Register and upload ONE file on this draft, server-side.

        Deliberately routed through ``_register_file``: that is where the deny
        list, the per-transfer file and byte limits, the sanitized display
        name, the server-derived mimetype and the opaque S3 key live. Creating
        a ``secure.transfer.file`` row directly here would have handed the
        backend a upload path with none of them.
        """
        self.ensure_one()
        rec_file = self._register_file(filename, len(data))
        # _register_file wrote the plan the BROWSER would have followed (simple
        # PUT below the threshold, MPU above). We just did one server-side PUT,
        # so the row has to say so — otherwise the operator reads "multipart"
        # on a file that never had an upload id, and the resume machinery would
        # be consulted for a file that has nothing to resume.
        rec_file.write({"upload_mode": "simple", "state": "uploading"})
        s3.put_bytes(self.env, rec_file.s3_key, data)
        # Same verification the public flow runs at finalize: HEAD the object,
        # confirm the exact size, pin the ETag. The bytes came from us, but the
        # evidence has to be built the same way or the access certificate would
        # rest on a weaker check for backend transfers.
        if not rec_file._verify_on_s3():
            raise UserError(_(
                "The file \"%s\" could not be verified on storage after "
                "upload.", rec_file.filename))
        return rec_file

    # ------------------------------------------------------------------ finalize
    def action_finalize(self, password=None):
        """Verify every file on S3 (HEAD: exists + exact size, ETag pinned),
        rotate the upload token, arm the expiry, activate and send the
        emails. Idempotent under the row lock: a duplicate call on an
        already-active transfer returns the same payload."""
        self.ensure_one()
        self._lock_row()
        if self.state == "active":
            return {
                "share_url": self._share_url(),
                "expiry_date": fields.Datetime.to_string(self.expiry_date),
            }
        if self.state != "draft":
            raise UserError(_("This transfer can no longer be finalized."))
        # E-mail is required to SEND (it may have been blank while dropping
        # files). Validate + allowlist + per-sender quota here.
        if not self.sender_email:
            raise UserError(_("A sender email address is required."))
        if not self.brand_id._sender_allowed(self.sender_email):
            raise UserError(_(
                "This email address is not authorized to send from this "
                "service."))
        self._check_sender_quota(self.sender_email, exclude_id=self.id)
        # Drop page guarantor: re-force the fixed recipient at send time too,
        # so a finalize call that carried a different address can never
        # redirect the transfer away from the page owner.
        if self.brand_id.fixed_recipient:
            forced = self.brand_id._drop_recipient()
            if self.recipient_emails != forced:
                self.recipient_emails = forced
        # A transfer must carry SOMETHING: files, or a message (message-only
        # mode — a secure note, e.g. a password).
        if not self.file_ids and not (self.message or "").strip():
            raise UserError(_("Add at least one file or a message."))
        for f in self.file_ids:
            if f.s3_upload_id:
                raise UserError(_(
                    "The upload of \"%s\" is not finished.", f.filename,
                ))
            if not f._verify_on_s3():
                raise UserError(_(
                    "The file \"%s\" was not uploaded correctly. Remove "
                    "it or upload it again.", f.filename,
                ))
        if password:
            if not self.brand_id.allow_password:
                raise UserError(_(
                    "Password protection is not offered for this service."
                ))
            self._set_password(password)

        # Sender OTP gate (tenant setting): don't activate yet — email a code
        # to the sender and require confirmation first. The password is already
        # stored above, so it survives the confirm round-trip.
        if self._needs_sender_otp():
            self._send_sender_otp()
            return {"otp_required": "sender"}

        return self._activate()

    def _activate(self):
        """Flip a verified draft to active: rotate the upload token, arm the
        expiry, send the branded emails. Shared by the direct finalize and the
        sender-OTP confirmation path."""
        self.ensure_one()
        now = fields.Datetime.now()
        expiry = now + timedelta(days=self.retention_days or 7)
        self.write({
            # Rotation: the draft/upload capability dies here.
            "upload_token": uuid.uuid4().hex,
            "expiry_date": expiry,
            "finalized_at": now,
            "state": "active",
        })
        self._log(
            "finalized",
            note=self._log_env()._("%s file(s), %.0f byte(s), expires on %s")
            % (len(self.file_ids), self.total_size,
               fields.Datetime.to_string(expiry)),
        )
        self._send_link_emails()
        return {
            "share_url": self._share_url(),
            "expiry_date": fields.Datetime.to_string(expiry),
        }

    # ------------------------------------------------------------------ OTP
    @api.model
    def _otp_hash(self, code):
        """Keyed digest of a one-time code.

        ⚠ This was a bare ``sha256("bf_st_otp:" + code)``. The code is six
        digits and the prefix is a constant, not a salt: the whole 10^6 space
        pre-computes in well under a second, so the digest protected nothing on
        its own. The field carries ``groups="base.group_system"`` and the
        recipient challenge lives in the server-side session, so the value never
        leaked through the ORM — but anyone reading a database dump or a backup
        recovered every pending code.

        ``odoo.tools.hmac`` keys the digest on the database secret, which is not
        in the same dump as the data for most deployment shapes and is not
        shared between tenants. Same choice as bf_cx's unsubscribe token.

        ⚠ Deploy note: this invalidates codes already in flight. Both consumers
        are short-lived (a draft awaiting sender confirmation, a session-held
        recipient challenge) and both offer a resend, so the blast radius is a
        few minutes of "wrong code" — no migration needed.
        """
        return odoo_hmac(self.env(su=True), "bf_st_otp", code or "")

    @api.model
    def _needs_sender_otp_param(self):
        return (s3.param(self.env, "require_sender_otp", "0") or "").strip().lower() \
            in ("1", "true", "yes")

    @api.model
    def _needs_recipient_otp_param(self):
        return (s3.param(self.env, "require_recipient_otp", "0") or "").strip().lower() \
            in ("1", "true", "yes")

    def _needs_sender_otp(self):
        self.ensure_one()
        return self._needs_sender_otp_param() and not self.sender_confirmed

    def _recipient_otp_required(self):
        """Whether THIS transfer holds its content behind a recipient code:
        le mode audience ouverte, le drapeau par transfert, OU le réglage
        d'instance. Le contrôleur public consulte ce prédicat sur la page de
        téléchargement, à la demande de code et à chaque téléchargement.

        ⚠ L'audience ouverte n'est pas une option ici, c'est le contrôle
        d'accès lui-même : un lien qui ne nomme personne et ne demande pas de
        code est un lien public. Le prédicat le force donc, quoi qu'en disent
        les deux autres sources."""
        self.ensure_one()
        return (self.audience_mode == "open"
                or self.force_recipient_otp
                or self._needs_recipient_otp_param())

    def _brand_email_shell(self, heading, inner_html):
        """Wrap ``inner_html`` in the branded email skeleton (dark header band
        with logo/name + accent bar + white card). Shared by the OTP and abuse
        emails so every Python-built message matches the pages."""
        self.ensure_one()
        v = self.brand_id._visuals()
        base = self.brand_id._share_base_url()
        logo = v.get("logo_url") or ""
        if logo and not logo.startswith("http"):
            logo = base + logo
        head = (
            '<img src="%s" alt="" style="height:36px;display:block;border:0;"/>'
            % logo if logo else
            '<span style="color:#fff;font-size:18px;font-weight:700;">%s</span>'
            % html_escape(v["name"]))
        return (
            '<div style="font-family:Lexend,system-ui,Arial,sans-serif;color:#2E3132;'
            'font-size:14px;line-height:1.55;max-width:600px;margin:0 auto;">'
            '<div style="background:%(dark)s;padding:18px 24px;border-radius:10px 10px 0 0;">'
            '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
            'border="0"><tbody><tr><td>%(head)s</td>'
            '<td align="right" style="color:#fff;font-size:16px;font-weight:700;">%(heading)s</td>'
            '</tr></tbody></table></div>'
            '<div style="height:4px;background:%(primary)s;"></div>'
            '<div style="background:#fff;border:1px solid #e3e7eb;border-top:0;padding:24px;'
            'border-radius:0 0 10px 10px;">%(inner)s</div></div>' % {
                "dark": html_escape(v["dark"]), "primary": html_escape(v["primary"]),
                "head": head, "heading": html_escape(heading), "inner": inner_html,
            })

    def _brand_email_from(self):
        self.ensure_one()
        return (self.brand_id.email_from or self.company_id.email_formatted
                or self.env.user.email_formatted)

    def _otp_email(self, to_email, code, kind):
        """Send a branded 6-digit-code email. The intro is built HERE (not
        passed in) so it renders in this recordset's context language — call
        via ``self.with_context(lang=…)._otp_email(...)`` for the contact's
        language. ``kind`` is 'sender' or 'recipient'."""
        self.ensure_one()
        # Resolve the brand visuals ONCE. Each call walks up to four binary
        # fields, and for every one of them reads the filestore just to test
        # presence and then searches ir.attachment — so calling it three times
        # to build a single e-mail meant a dozen attachment searches per code
        # sent.
        visuals = self.brand_id._visuals()
        if kind == "sender":
            intro = _("Hello %s, confirm your transfer with this code:") \
                % (self.sender_name or "")
        else:
            intro = _("A sender shared files with you via %s. Enter this "
                      "code to access them:") \
                % visuals["name"]
        inner = (
            '<p>%s</p>'
            '<p style="text-align:center;margin:24px 0;">'
            '<span style="display:inline-block;font-size:30px;font-weight:700;'
            'letter-spacing:8px;background:#f4f6f8;border-radius:8px;padding:14px 24px;'
            'color:%s;">%s</span></p>'
            '<p style="color:#777;font-size:12px;">%s</p>'
            % (html_escape(intro), html_escape(visuals["dark"]), code,
               html_escape(_("This code expires in 15 minutes. If you did "
                             "not request it, please ignore this email."))))
        self.env["mail.mail"].sudo().create({
            "subject": _("Your confirmation code: %s") % self.name,
            "email_from": self._brand_email_from(),
            "email_to": to_email,
            "body_html": self._brand_email_shell(_("Confirmation code"), inner),
            "auto_delete": True,
        }).send()

    def _otp_sms(self, phone, code):
        """Deliver a recipient code by SMS (VoIP.ms). Returns True on a
        confirmed send, False on any failure so the caller falls back to
        e-mail. The text is one short segment; no PII reaches the logs."""
        self.ensure_one()
        text = _("%(brand)s: your verification code is %(code)s (valid "
                 "for 15 minutes).") % {
            "brand": self.brand_id._visuals()["name"], "code": code,
        }
        return sms.send(self.env, phone, text)

    def _abuse_desk_email(self):
        """Where an abuse report is escalated, resolved from THIS tenant.

        ⚠ The address used to be hardcoded to a Blue Fox mailbox in three
        places: the tenant setting's ``default=``, the Settings placeholder and
        the fallback here. On a white-label tenant that meant a client's abuse
        notice — which carries the sender, the FULL recipient list, the
        reporter's reason and their IP — was addressed to another company's
        mailbox unless someone thought to change it. Nothing in this module
        should name an operator: the address now comes from the tenant's own
        records.

        Order: the explicit tenant setting, then the company that owns the
        brand, then the address the brand already sends from (so the notice
        lands in the operator's own mailbox rather than nowhere — an empty
        ``email_to`` fails silently in the mail queue).
        """
        self.ensure_one()
        explicit = (s3.param(self.env, "abuse_email", "") or "").strip()
        if explicit:
            return explicit
        company = self.company_id or self.env.company
        return (company.email or "").strip() or self._brand_email_from()

    def _send_abuse_notice(self, reason=None, ip=None):
        """On an abuse report: alert the abuse desk (full detail) and warn the
        transfer's recipients (neutral). The desk address resolves per tenant
        — see ``_abuse_desk_email``.

        Each mail is written in ITS reader's language: the desk in the
        operator's (its contact's language, else the company's), each recipient
        in the one their link mail used. The request that triggers this is the
        anonymous reporter's, whose language concerns neither."""
        self.ensure_one()
        abuse_email = self._abuse_desk_email()
        from_addr = self._brand_email_from()
        company = self.company_id or self.env.company
        # 1) Abuse desk — full detail for the reviewer.
        desk = self.with_context(lang=self._lang_for_email(
            abuse_email, fallback=company.sudo().partner_id.lang))
        denv = desk.env
        rows = [
            (denv._("Reference"), self.name),
            (denv._("Brand"), self.brand_id.name),
            (denv._("Sender"), "%s <%s>" % (self.sender_name or "", self.sender_email or "")),
            (denv._("Recipients"), self.recipient_emails or ""),
            (denv._("Reason"), reason or ""),
            (denv._("Reporter's IP"), ip or ""),
        ]
        detail = (
            '<p>%s</p>'
            '<table role="presentation" style="font-size:13px;color:#444;">'
            '<tbody>%s</tbody></table>'
            '<p style="margin-top:16px;color:#777;font-size:12px;">%s</p>' % (
                denv._("A transfer was <strong>reported as abusive</strong> and "
                       "<strong>automatically suspended</strong> pending "
                       "review."),
                "".join(
                    '<tr><td style="padding:2px 12px 2px 0;"><strong>%s</strong></td>'
                    '<td>%s</td></tr>' % (html_escape(k), html_escape(v or "—"))
                    for k, v in rows),
                html_escape(denv._(
                    "Reactivate the transfer (false report) or purge it "
                    "(confirmed abuse) in Odoo, under Secure transfers.")),
            ))
        self.env["mail.mail"].sudo().create({
            "subject": denv._("[Abuse] Transfer reported and suspended: %s") % self.name,
            "email_from": from_addr,
            "email_to": abuse_email,
            "body_html": desk._brand_email_shell(denv._("Abuse report"), detail),
            "auto_delete": True,
        }).send()
        # 2) Recipients — neutral notice (no reason/IP leaked).
        # ONE MAIL PER RECIPIENT, like _send_link_emails. A single mail with
        # recipient_emails in To: would show every recipient to every other —
        # a disclosure any anonymous link holder could trigger by clicking
        # "report abuse", on a transfer that may span several organisations.
        for recipient in self._recipient_list():
            renv = self.with_context(lang=self._lang_for_email(recipient)).env
            notice = (
                '<p>%s</p><p>%s</p><p style="color:#777;font-size:12px;">%s</p>' % (
                    html_escape(renv._("Hello,")),
                    renv._("A file transfer shared with you was <strong>reported "
                           "and temporarily suspended</strong> while it is "
                           "checked. The link is unavailable for now."),
                    html_escape(renv._("You have nothing to do. If the transfer "
                                       "is legitimate, it will be restored "
                                       "after review.")),
                ))
            self.env["mail.mail"].sudo().create({
                "subject": renv._("Transfer suspended for verification: %s") % self.name,
                "email_from": from_addr,
                "email_to": recipient,
                "body_html": self._brand_email_shell(
                    renv._("Transfer suspended"), notice),
                "auto_delete": True,
            }).send()

    def _send_sender_otp(self, reset_fails=True):
        """Generate a fresh 6-digit code, store its hash (15 min TTL), and
        email it to the sender.

        ``reset_fails=False`` is for the "resend a code" button. Clearing the
        counter there let anyone walk past the 8-attempt cap in
        confirm_sender_otp: guess seven times, click "renvoyer un code", and
        the budget is full again. The per-transfer ceiling has to survive a
        resend or it bounds nothing — only the per-IP limiter would be left.
        """
        self.ensure_one()
        code = "%06d" % secrets.randbelow(1_000_000)
        vals = {
            "sender_otp_hash": self._otp_hash(code),
            "sender_otp_expiry": fields.Datetime.now() + timedelta(minutes=15),
        }
        if reset_fails:
            vals["sender_otp_fails"] = 0
        self.write(vals)
        self.with_context(lang=self._lang_for_email(self.sender_email))._otp_email(
            self.sender_email, code, "sender")
        self._log("otp_sent", actor=self.sender_email,
                  note=self._log_env()._("Confirmation code sent to the sender"))

    def confirm_sender_otp(self, code):
        """Verify the sender code and, on success, activate the transfer.
        Returns the same payload as finalize. Raises UserError on a bad or
        expired code."""
        self.ensure_one()
        self._lock_row()
        if self.state == "active":
            return {"share_url": self._share_url(),
                    "expiry_date": fields.Datetime.to_string(self.expiry_date)}
        if self.state != "draft" or not self.sudo().sender_otp_hash:
            raise UserError(_("No confirmation is pending."))
        if self.sender_otp_fails >= 8:
            raise UserError(_("Too many attempts. Request a new code."))
        expiry = self.sudo().sender_otp_expiry
        if not expiry or expiry < fields.Datetime.now():
            raise UserError(_("The code has expired. Request a new one."))
        if not hmac.compare_digest(
                self.sudo().sender_otp_hash, self._otp_hash((code or "").strip())):
            self.sender_otp_fails += 1
            self._log("otp_fail", actor=self.sender_email)
            raise UserError(_("Invalid code."))
        self.write({
            "sender_confirmed": True,
            "sender_otp_hash": False,
            "sender_otp_expiry": False,
        })
        self._log("otp_ok", actor=self.sender_email,
                  note=self._log_env()._("Sender confirmed by code"))
        return self._activate()

    # -- recipient OTP (download gate). The challenge is held in the visitor's
    #    session by the controller (per-browser, no cross-recipient clobber);
    #    the model only validates the target and sends the code.
    def _is_recipient_email(self, email):
        """L'adresse a-t-elle le droit de demander un code sur ce transfert ?

        En mode « destinataires nommés », la réponse est l'appartenance à la
        liste — le code PROUVE une identité déjà connue. En audience ouverte,
        il n'y a pas de liste : la question devient « cette adresse est-elle
        admissible », et c'est `_audience_admissible` qui tranche."""
        self.ensure_one()
        email = email_normalize(email or "") or ""
        if not email:
            return False
        # Les deux modes passent par `_audience_admissible` : c'est là que le
        # blocage posé par l'opérateur est lu. Sans cela, un destinataire nommé
        # bloqué rentrait en redemandant simplement un code.
        return self._audience_admissible("email", email)[0]

    # ------------------------------------------------------------------ audience ouverte
    def _audience_limits(self):
        """Plafonds effectifs de l'audience : ceux du transfert, sinon ceux de
        la marque."""
        self.ensure_one()
        brand = self.brand_id
        return {
            "max_visitors": int(self.audience_max or brand.audience_max_default or 0),
            "allowlist": (self.audience_domains or "").strip()
            or brand._audience_allowlist(),
            "allow_sms": bool(self.audience_allow_sms
                              and brand.allow_audience_sms
                              and sms.configured(self.env)),
        }

    def _audience_admissible(self, kind, value):
        """(admissible, motif) pour une identité auto-déclarée.

        Quatre barrières, dans cet ordre :

        1. **le canal** — une identité mobile n'est recevable que si le
           transfert ET la marque offrent le SMS et que le canal est câblé ;
        2. **la liste blanche** — celle du transfert, sinon celle de la marque,
           qui retombe elle-même sur la liste des destinataires autorisés. Une
           instance qui restreint déjà ses destinataires ne s'ouvre pas par le
           seul choix du mode.
           ⚠ Une liste de domaines ne sait rien dire d'un numéro : dès qu'elle
           est posée, les identités mobiles sont refusées plutôt que laissées
           passer sans contrôle. Restreindre par domaine, c'est exiger le
           courriel ;
        3. **le plafond de visiteurs distincts** — une identité DÉJÀ inscrite
           repasse toujours (sinon le 51ᵉ visiteur fermerait la porte aux
           cinquante premiers) ; seule une identité NOUVELLE s'y heurte ;
        4. **le blocage individuel** posé par l'opérateur.

        ⚠ C'est le plafond (3) qui empêche un lien fuité de servir de relais :
        sans lui, quiconque tient le lien ferait partir un courriel signé DKIM
        — ou un SMS facturé — vers autant de tiers qu'il le souhaite.
        """
        self.ensure_one()
        identity = self.env["secure.transfer.audience"]._identity_values(kind, value)
        if not identity:
            return False, "invalid"
        kind, email, phone = identity
        existing_row = self._audience_for(kind, value)
        if existing_row.state == "blocked":
            return False, "blocked"
        if self.audience_mode != "open":
            # Mode « destinataires nommés ». La ligne d'audience n'y sert pas à
            # ADMETTRE quelqu'un — la liste l'a déjà fait à l'envoi — mais à
            # ancrer un destinataire une fois qu'il a prouvé son identité :
            # c'est ce à quoi s'accrochent le filigrane nominatif, le budget
            # par personne et la NDA du module pont. Ni plafond ni liste
            # blanche ici : ils appartiennent au mode ouvert.
            if kind != "email":
                return False, "channel"
            declared = email in [
                email_normalize(e) for e in (self.recipient_emails or "").split(",")
                if e.strip()
            ]
            return (True, "") if declared else (False, "not_a_recipient")
        limits = self._audience_limits()
        if kind == "sms" and not limits["allow_sms"]:
            return False, "channel"
        allowlist = (limits["allowlist"] or "").strip()
        if kind == "sms":
            if allowlist:
                return False, "domain"
        elif not self.brand_id._email_matches_list(email, allowlist):
            return False, "domain"
        if existing_row:
            # Déjà inscrit (et non bloqué : contrôlé plus haut) — il repasse.
            return True, ""
        if limits["max_visitors"] and len(self.audience_ids) >= limits["max_visitors"]:
            # Les visiteurs en attente comptent : sans cela le plafond ne
            # limiterait que les confirmations, et l'envoi de codes — le coût
            # et le risque réels — resterait sans borne.
            return False, "full"
        return True, ""

    def _audience_for(self, kind, value):
        """La ligne d'audience de cette identité, ou un recordset vide."""
        self.ensure_one()
        identity = self.env["secure.transfer.audience"]._identity_values(kind, value)
        if not identity:
            return self.env["secure.transfer.audience"]
        kind, email, phone = identity
        if kind == "sms":
            return self.audience_ids.filtered(
                lambda a: a.identity_kind == "sms" and a.phone == phone)[:1]
        return self.audience_ids.filtered(
            lambda a: a.identity_kind == "email" and a.email == email)[:1]

    def _audience_join(self, kind, value, ip=None, ua=None):
        """Créer (ou retrouver) la ligne d'audience d'une identité admissible.

        Sous le verrou de ligne : deux navigateurs qui demandent un code au
        même instant pour la même identité doivent aboutir à UNE ligne, et le
        plafond de visiteurs doit être compté une seule fois. La contrainte SQL
        d'unicité est la ceinture ; le verrou évite qu'elle se traduise en
        page d'erreur pour le visiteur."""
        self.ensure_one()
        Audience = self.env["secure.transfer.audience"]
        identity = Audience._identity_values(kind, value)
        if not identity:
            return Audience
        kind, email, phone = identity
        self._lock_row()
        existing = self._audience_for(kind, value)
        if existing:
            return existing
        admissible, _reason = self._audience_admissible(kind, value)
        if not admissible:
            return Audience
        rec = Audience.sudo().create({
            "transfer_id": self.id,
            "identity_kind": kind,
            "email": email or False,
            "phone": phone or False,
            "ip": ip or "",
            "user_agent": (ua or "")[:512],
        })
        log_env = self._log_env()
        self._log("audience_requested", actor=rec.display_identity, ip=ip, ua=ua,
                  note=(log_env._("Self-declared identity on an open audience (%s)")
                        % (log_env._("SMS") if kind == "sms" else log_env._("email")))
                  if self.audience_mode == "open"
                  else log_env._("Code requested by a named recipient"))
        return rec

    def _audience_confirm(self, member, ip=None, ua=None):
        """Le code a été validé : inscrire le visiteur et prévenir l'expéditeur.

        Rendue distincte de `_audience_join` parce que les deux moments n'ont
        pas la même valeur de preuve : la demande dit qu'une identité a été
        TAPÉE, la confirmation dit qu'elle a été LUE."""
        self.ensure_one()
        if not member:
            return member
        if member.state == "blocked":
            # Un code valide ne lève pas un blocage : seul l'opérateur le fait.
            self._log("otp_fail", actor=member.display_identity, ip=ip, ua=ua,
                      note="blocked")
            return member.browse()
        was_confirmed = member.state == "confirmed"
        member._confirm()
        if not was_confirmed:
            log_env = self._log_env()
            self._log("audience_joined", actor=member.display_identity, ip=ip, ua=ua,
                      note=log_env._("Visitor confirmed (%s)")
                      % (log_env._("open audience") if self.audience_mode == "open"
                         else log_env._("named recipient")))
            # L'avis ne vaut que pour l'audience ouverte : sur un envoi à des
            # destinataires nommés, l'expéditeur SAIT déjà à qui il a écrit —
            # lui poster un courriel par destinataire serait du bruit.
            if self.audience_mode == "open" and self.notify_on_join \
                    and self.sender_email:
                self._notify_audience_join(member)
        return member

    def _notify_audience_join(self, member):
        """Avertir l'expéditeur qu'une nouvelle identité est entrée.

        ⚠ C'est le seul avis qui parte à CHAQUE visiteur. `notify_on_download`
        ne se déclenche qu'au PREMIER téléchargement du transfert (une fois,
        pas une fois par personne) : sur une salle de données, il ne dirait
        jamais qui d'autre est entré."""
        self.ensure_one()
        lang = self._lang_for_email(self.sender_email)
        # The SENDER reads this, in their language; ``_()`` would read the
        # caller's frame, i.e. the confirming visitor's request.
        this = self.with_context(lang=lang)
        tenv = this.env
        inner = (
            '<p>%s</p>'
            '<p style="text-align:center;margin:20px 0;font-size:18px;'
            'font-weight:700;">%s</p>'
            '<p style="color:#777;font-size:12px;">%s</p>'
            % (html_escape(tenv._("A new person confirmed their identity to "
                                  "access \"%s\":")
                           % (self.subject or self.name)),
               html_escape(member.display_identity),
               html_escape(tenv._("You are receiving this notice because this "
                                  "transfer has an open audience. You can "
                                  "block a visitor from the transfer record."))))
        self.env["mail.mail"].sudo().create({
            "subject": tenv._("New visitor: %s") % self.name,
            "email_from": self._brand_email_from(),
            "email_to": self.sender_email,
            "body_html": this._brand_email_shell(tenv._("New visitor"), inner),
            "auto_delete": True,
        }).send()
        self._log("notified", actor=member.display_identity,
                  note=self._log_env()._("New visitor notice sent to %s")
                  % self.sender_email)

    def _extra_access_gate(self, member, token):
        """Une barrière SUPPLÉMENTAIRE entre le code confirmé et le contenu.

        Rend l'URL vers laquelle envoyer le visiteur, ou ``False`` pour le
        laisser passer. Le socle n'en pose aucune : la méthode existe pour que
        le module pont ``bf_securetransfer_sign`` y branche la signature d'une
        entente de confidentialité sans que le socle ait à connaître bf_sign —
        un champ typé vers ``bf.sign.request`` d'ici en ferait une dépendance
        dure, et cela ne se verrait que sur une installation neuve.

        Appelée aux DEUX endroits qui donnent accès au contenu : la page de
        téléchargement et la route de téléchargement d'un fichier. Ne la
        brancher qu'à la page laisserait les liens directs ouverts.
        """
        self.ensure_one()
        return False

    def _send_audience_otp(self, kind, value, ip=None, ua=None):
        """Envoyer un code à une identité auto-déclarée (mode audience ouverte).

        Rend ``(hash, expiry, member)`` pour que le contrôleur range le défi
        dans la session, ou ``(None, None, vide)`` sur tout refus. Comme
        `_send_recipient_otp`, la méthode est privée à dessein : elle rend un
        condensé dérivé du code et déclenche un envoi sortant, donc elle ne
        doit pas être joignable par RPC.

        ⚠ Le code part sur le canal de l'identité déclarée, jamais sur un
        autre : c'est ce qui fait qu'une confirmation prouve quelque chose.
        """
        self.ensure_one()
        member = self._audience_join(kind, value, ip=ip, ua=ua)
        if not member:
            return None, None, member
        allowed, reason = member._may_receive_otp()
        if not allowed:
            self._log("otp_fail", actor=member.display_identity, ip=ip, ua=ua,
                      note=self._log_env()._("Code request refused (%s)") % reason)
            return None, None, member
        code = "%06d" % secrets.randbelow(1_000_000)
        expiry = fields.Datetime.now() + timedelta(minutes=15)
        if member.identity_kind == "sms":
            if not self._otp_sms(member.phone, code):
                # Le SMS a échoué : PAS de repli vers le courriel ici. En
                # audience ouverte, l'identité EST le numéro — livrer ailleurs
                # donnerait l'accès à quelqu'un d'autre. Le visiteur réessaie.
                self._log("otp_fail", actor=member.display_identity, ip=ip, ua=ua,
                          note=self._log_env()._("SMS code delivery failed"))
                return None, None, member
        else:
            # The visitor asked for this code from the page, in the page's
            # language (the request's): that is the fallback, not the
            # sender's locale.
            self.with_context(lang=self._lang_for_email(
                member.email, fallback=self.env.lang))._otp_email(
                member.email, code, "recipient")
        member._record_otp_sent()
        log_env = self._log_env()
        self._log("otp_sent", actor=member.display_identity, ip=ip, ua=ua,
                  note=log_env._("Code sent to an open audience visitor (%s)")
                  % (log_env._("SMS") if member.identity_kind == "sms"
                     else log_env._("email")))
        return self._otp_hash(code), expiry, member

    def _phone_for_recipient(self, email):
        """The mobile number to text a recipient's code to, or ''. First the
        per-transfer sms map (captured by a backend secure send), then a
        matching Odoo contact's mobile/phone. Normalized to 10 digits."""
        self.ensure_one()
        email = email_normalize(email or "") or ""
        if not email:
            return ""
        try:
            mapping = json.loads(self.recipient_sms_map or "{}")
        except (ValueError, TypeError):
            mapping = {}
        candidate = mapping.get(email) or ""
        if not candidate:
            partner = self.env["res.partner"].sudo().search(
                [("email", "=ilike", email)], limit=1)
            candidate = partner.mobile or partner.phone or "" if partner else ""
        return sms.normalize_na(candidate) or ""

    def _otp_channel_for(self, email):
        """The channel actually used for this recipient: 'sms' only when the
        transfer elected SMS, the tenant has it configured, AND a phone is
        known — otherwise 'email' (safe fallback, never a lock-out)."""
        self.ensure_one()
        if self.recipient_otp_channel == "sms" and sms.configured(self.env) \
                and self._phone_for_recipient(email):
            return "sms"
        return "email"

    def _send_recipient_otp(self, email, ip=None, ua=None):
        """Send a fresh code to a declared recipient over the elected channel
        (SMS when available, else e-mail). Returns ``(hash, expiry_datetime)``
        for the controller to stash in the session, or (None, None) when the
        email is not one of the recipients.

        Private on purpose: it returns an OTP-derived hash and triggers an
        outbound email/SMS, so it must not be reachable over RPC (a read-only
        ``group_securetransfer_user`` could otherwise recover the 6-digit code
        offline and spam the recipient). Only the public /otp-request controller
        calls it, on a token-resolved transfer, behind a send rate limit."""
        self.ensure_one()
        if not self._is_recipient_email(email):
            return None, None
        email = email_normalize(email)
        # Plafond par destinataire, en base : les limiteurs du
        # contrôleur sont par IP et en mémoire, ils ne bornent rien face à des
        # IP qui tournent. La ligne d'audience ancre déjà le destinataire ; on
        # la crée dès la demande pour y compter les envois.
        member = self._audience_join("email", email, ip=ip, ua=ua)
        if member:
            allowed, reason = member._may_receive_otp()
            if not allowed:
                self._log("otp_fail", actor=email, ip=ip, ua=ua,
                          note=self._log_env()._("Code request refused (%s)") % reason)
                return None, None
        code = "%06d" % secrets.randbelow(1_000_000)
        expiry = fields.Datetime.now() + timedelta(minutes=15)
        channel = self._otp_channel_for(email)
        # Requested from the page: its language is the fallback for an
        # address that matches no contact.
        lang = self._lang_for_email(email, fallback=self.env.lang)
        if channel == "sms":
            sent = self.with_context(lang=lang)._otp_sms(
                self._phone_for_recipient(email), code)
            if not sent:
                # SMS refused/unreachable — fall back so the recipient is never
                # locked out of a transfer addressed to them.
                channel = "email"
        if channel == "email":
            self.with_context(lang=lang)._otp_email(email, code, "recipient")
        if member:
            member._record_otp_sent()
        log_env = self._log_env()
        self._log("otp_sent", actor=email, ip=ip, ua=ua,
                  note=log_env._("Confirmation code sent to the recipient (%s)")
                  % (log_env._("SMS") if channel == "sms" else log_env._("email")))
        return self._otp_hash(code), expiry

    # ------------------------------------------------------------------ password
    def _set_password(self, pw):
        self.ensure_one()
        self.sudo().write({"password_hash": _pwd_ctx.hash(pw)})

    def _check_password(self, pw):
        """Constant-time verification (passlib). A transfer without a
        password always passes — the caller logs password_ok/password_fail."""
        self.ensure_one()
        stored = self.sudo().password_hash
        if not stored:
            return True
        try:
            return _pwd_ctx.verify(pw or "", stored)
        except ValueError:
            return False

    # ------------------------------------------------------------------ availability / download
    def _is_available(self):
        """(available, reason). Reasons feed the neutral public pages:
        not_found / expired / suspended — never internal detail."""
        self.ensure_one()
        if self.state in ("draft", "cancelled"):
            return False, "not_found"
        if self.state == "suspended":
            return False, "suspended"
        if self.state in ("expired", "deleted"):
            return False, "expired"
        if self.expiry_date and self.expiry_date <= fields.Datetime.now():
            return False, "expired"
        if self.max_downloads and self.download_count >= self.max_downloads:
            return False, "expired"
        return True, ""

    def _recipient_list(self):
        """The recipient addresses, split and trimmed. Every caller that mails
        recipients must go through this and send ONE mail each: the raw
        comma-joined field in an email_to would expose the whole list to each
        of them."""
        self.ensure_one()
        return [e.strip() for e in (self.recipient_emails or "").split(",")
                if e.strip()]

    def _downloadable_files(self):
        """The files a recipient may actually fetch: upload verified and not
        flagged by the scanner. Single source of truth for the listing page,
        the per-file download route and the burn accounting — they must agree
        on "what this transfer offers", or burn fires on a file nobody could
        have downloaded."""
        self.ensure_one()
        return self.file_ids.filtered(
            lambda f: f.state == "verified" and f.scanned in ("none", "clean"))

    def _register_download(self, file, ip, ua, member=None):
        """Account one download under the row lock: re-check availability
        (two racing downloads must not both pass a budget of 1), bump the
        counters, expire when the budget is spent.

        ``member`` est la ligne d'audience du visiteur, en mode ouvert : son
        budget personnel est vérifié SOUS LE MÊME VERROU que le budget global,
        sans quoi deux téléchargements concurrents passeraient tous les deux
        un budget de 1."""
        self.ensure_one()
        self._lock_row()
        available, _reason = self._is_available()
        if not available:
            raise UserError(_("This transfer is no longer available."))
        if member:
            if member.state == "blocked":
                raise UserError(_("This transfer is no longer available."))
            left = member._download_budget_left()
            if left is not None and left <= 0:
                raise UserError(_(
                    "You have reached the number of downloads allowed for "
                    "this link."))
            member._register_download()
        self.download_count += 1
        file.download_count += 1
        self._log("download", file=file, ip=ip, ua=ua,
                  actor=member.display_identity if member else None,
                  note=file.filename)
        # Notify the sender on the FIRST download (once per transfer, not per
        # file — avoids a burst of notices on a multi-file transfer).
        if self.notify_on_download and self.download_count == 1 and self.sender_email:
            self._notify_download()
        # Burn-after-download: the link dies once the recipient HAS THE WHOLE
        # transfer, not on the first file. A browser fetches one file per
        # request, so burning on the first download strands every remaining
        # attachment — the recipient is left with file 1 of N and a dead link,
        # which is exactly what the public label promises not to do
        # ("dès qu'un destinataire a téléchargé les fichiers", plural).
        # Files that were never downloadable (upload error, scanner hit) are
        # excluded, otherwise they would hold the burn open forever.
        # The presigned GET already issued (short TTL) finishes the in-flight
        # download; the purge cron then removes the S3 objects. (A stricter
        # immediate purge is impossible with the direct-to-S3 redirect model —
        # deleting now would 404 the in-flight fetch.)
        if self.burn_after_download:
            pending = self._downloadable_files().filtered(
                lambda f: not f.download_count)
            if not pending:
                self.state = "expired"
                self._log("expired",
                          note=self._log_env()._("Destroyed after reading (burn)"))
        elif self.max_downloads and self.download_count >= self.max_downloads:
            self.state = "expired"
            self._log("expired", note=self._log_env()._("Download budget exhausted"))

    # ── Watermarked delivery ──────────────────────────────────────────────────
    # Above this size Odoo declines to stamp and hands out the plain presigned
    # redirect instead: stamping loads the whole PDF in memory, and no document
    # is worth an OOM on a shared worker.
    _WATERMARK_MAX_BYTES = 30 * 1024 * 1024

    def _watermark_lines(self, file, ip=None, actor=None):
        """The three lines stamped on the page: who, when, whose brand.

        "Who" is, in order of how much it proves:

        * ``actor`` — l'identité que le visiteur a CONFIRMÉE par code dans
          cette session. C'est le cas nominal en audience ouverte, et le
          meilleur des trois : elle est prouvée, et elle est individuelle ;
        * le destinataire, quand le transfert n'en nomme qu'un ;
        * l'IP demandeuse — le repli, quand plusieurs destinataires se
          partagent le lien et qu'aucun nom n'est vrai.

        ⚠ Avant l'audience ouverte, un transfert à plusieurs destinataires ne
        pouvait estamper qu'une IP : le filigrane ne désignait donc personne.
        Le passage par le code change cela sans rien coûter.
        """
        self.ensure_one()
        recipients = self._recipient_list()
        who = actor or (recipients[0] if len(recipients) == 1 else (
            ("IP " + ip) if ip else _("recipient")))
        return [
            _("Downloaded by %s") % who,
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            _("%s (confidential)") % (self.brand_id.name or ""),
        ]

    def _stamped_download_bytes(self, file, ip=None, actor=None):
        """Stamped PDF bytes for this download, or None to serve the plain S3
        redirect (watermark off for the brand, not a PDF, too large, or the
        stamping failed). A stamping failure must never cost the recipient the
        download — it degrades to the unstamped file, it does not 500."""
        self.ensure_one()
        if not self.brand_id.watermark_downloads or not file._is_pdf():
            return None
        size = file.size_confirmed or file.size or 0
        if size and size > self._WATERMARK_MAX_BYTES:
            return None
        try:
            src = s3.client(self.env).get_object(
                Bucket=s3.params(self.env)["bucket"], Key=file.s3_key,
            )["Body"].read()
            return pdf_watermark.stamp_pdf_bytes(
                src, self._watermark_lines(file, ip, actor=actor))
        except Exception:  # noqa: BLE001 — S3/PDF: log and fall back, never fail
            _logger.exception(
                "bf_securetransfer: watermarking failed for %s / file #%s; "
                "serving the plain redirect", self.name, file.id)
            return None

    def _notify_download(self):
        """Queue the 'your transfer was downloaded' notice to the sender."""
        self.ensure_one()
        tmpl = self.env.ref(
            "bf_securetransfer.mail_template_download_notice",
            raise_if_not_found=False,
        )
        if not tmpl:
            return
        lang = self._lang_for_email(self.sender_email)
        subject = self.with_context(lang=lang)._email_subject_line("notice")
        tmpl.sudo().with_context(lang=lang).send_mail(
            self.id, force_send=False,
            email_values={"subject": subject} if subject else None,
        )
        self._wake_mail_queue()
        self._log("notified", actor="system",
                  note=self._log_env()._("Download notice sent to the sender"))

    def _share_url(self):
        self.ensure_one()
        return "%s/s/%s" % (
            self.brand_id._share_base_url(), self.sudo().token,
        )

    # ------------------------------------------------------- chatter hygiene
    # The e-mails carrying the share link are stored ON the transfer
    # (mail.mail _inherits mail.message), so their rendered body lands in the
    # record's chatter and stays there for good. That copy went around both
    # guards the module puts on the capability: the ``token`` field is
    # manager-only, and every backend look is supposed to go through the
    # journaled reveal wizard. Anyone allowed to READ a transfer could read a
    # live link out of the chatter instead, and nothing recorded that they did.
    #
    # When a recipient code holds the content, the link alone opens nothing, so
    # the copy is harmless and is left in place. Without that code, the link IS
    # the credential — it gets blotted out of everything the record retains.
    def _keeps_share_link(self):
        """Whether this transfer may retain its raw link in the chatter."""
        self.ensure_one()
        return self._recipient_otp_required()

    def _redact_chatter_links(self, force=False):
        """Mask the share token in every message body retained on these
        transfers. Returns the number of bodies rewritten.

        Re-evaluated on every pass rather than decided once at send time: a
        transfer protected only by the instance-wide setting must have its old
        messages blotted too the day that setting is turned back off.
        """
        Message = self.env["mail.message"].sudo()
        Mail = self.env["mail.mail"].sudo()
        rewritten = 0
        for rec in self.sudo():
            token = rec.token
            if not token or (not force and rec._keeps_share_link()):
                continue
            hit = False
            for msg in Message.search([("model", "=", rec._name),
                                       ("res_id", "=", rec.id)]):
                body = msg.body or ""
                if token not in body:
                    continue
                # A queued e-mail still needs its real link to reach the
                # recipient: leave it alone and let the sweep take it once the
                # queue reports it sent (or gave up).
                mail = Mail.search([("mail_message_id", "=", msg.id)], limit=1)
                if mail and mail.state in ("outgoing", "exception"):
                    continue
                msg.write({"body": body.replace(token, SHARE_TOKEN_MASK)})
                if mail and token in (mail.body_html or ""):
                    mail.write({
                        "body_html": mail.body_html.replace(
                            token, SHARE_TOKEN_MASK),
                    })
                hit = True
                rewritten += 1
            if hit:
                rec._log(
                    "link_redacted", actor="system",
                    note=rec._log_env()._(
                        "Share link masked in the transfer log (no "
                        "recipient code guards access to it)."),
                )
        return rewritten

    @api.model
    def _cron_redact_chatter_links(self, limit=2000):
        """Safety net behind the post-send hook: catches the messages it could
        not touch (mail still queued, send retried, instance setting flipped
        off after the fact) and anything imported outside the send path."""
        Message = self.env["mail.message"].sudo()
        messages = Message.search(
            [("model", "=", self._name), ("body", "ilike", "%/s/%")],
            order="id desc", limit=limit,
        )
        transfers = self.browse(sorted(set(messages.mapped("res_id")))).exists()
        rewritten = transfers._redact_chatter_links()
        if rewritten:
            _logger.info(
                "bf_securetransfer: %s message body(ies) redacted "
                "(share link no longer retained in the chatter)", rewritten)
        return rewritten

    # ------------------------------------------------------------------ emails
    def _lang_for_email(self, email, fallback=None):
        """The matching Odoo contact's language when the email is a known
        res.partner, else ``fallback``, else the transfer's own locale. Lets
        each message go out in the recipient's language rather than a single
        transfer-wide one. ``fallback`` serves a reader who is not a party to
        the transfer (the abuse desk: the company's language)."""
        self.ensure_one()
        email = email_normalize(email or "") or ""
        if email:
            partner = self.env["res.partner"].sudo().search(
                [("email", "=ilike", email)], limit=1)
            if partner and partner.lang:
                return active_lang(self.env, partner.lang)
        return active_lang(self.env, fallback or self.locale)

    def _render_lang(self):
        """The transfer's locale as a language this database can render in."""
        self.ensure_one()
        return active_lang(self.env, self.locale)

    def _log_env(self):
        """The environment the access-log notes are written in.

        A note is frozen text in an append-only, hash-chained log, and the
        access certificate prints it as is, rendered in ``_render_lang()``.
        Writing it in the language of whoever triggered the event (an anonymous
        visitor, a cron, an operator) would mix languages in the one document
        the sender keeps. Write ``note=self._log_env()._("…")``: ``_()`` alone
        reads the caller's language, not this one.
        """
        self.ensure_one()
        return self.with_context(lang=self._render_lang()).env

    def _email_subject_line(self, kind):
        """The subject line to force on an outgoing mail, or None.

        None when the sender wrote no subject: the template's own subject then
        stands untouched, so a transfer that carries none behaves exactly as
        before. When they did write one it LEADS — an inbox list shows the
        first few dozen characters, and « TRF-2026-00123 » told the recipient
        nothing about what was sent.

        Called on a record whose context already carries the destination
        language (``_lang_for_email``), which is what makes ``_()`` resolve
        per recipient rather than per server locale.
        """
        self.ensure_one()
        subject = (self.subject or "").strip()
        if not subject:
            return None
        who = self.sender_name or self.sender_email or ""
        ref = self.name
        if kind == "link":
            return _("%(subject)s: %(sender)s sent you files (%(ref)s)",
                     subject=subject, sender=who, ref=ref)
        if kind == "secure":
            return _("%(subject)s: %(sender)s sent you a secure message "
                     "(%(ref)s)",
                     subject=subject, sender=who, ref=ref)
        if kind == "receipt":
            return _("Your transfer is online: %(subject)s (%(ref)s)",
                     subject=subject, ref=ref)
        if kind == "notice":
            return _("Your files were downloaded: %(subject)s (%(ref)s)",
                     subject=subject, ref=ref)
        return None

    def _reply_to_allowed(self):
        """Whether this transfer's sender may be put behind the recipient's
        « Reply » button.

        ⚠ On a brand that accepts ANY sender — the open public tier, which is
        what a real deployment actually runs — the form already lets a
        stranger have a branded, DKIM-signed mail delivered to ten addresses of
        their choosing. Adding their address as Reply-To would finish the job:
        the answer would reach them instead of coming back to the brand
        mailbox, which is today the only place that abuse surfaces. The header
        is therefore set only where the destination is not the sender's to
        choose:

          • a drop page (``fixed_recipient``) — the mail can only ever reach
            the page owner, who asked for the deposit. This is the case the
            header exists for: replying to whoever dropped you a file;
          • a brand (or instance) that RESTRICTS senders — an allowlist is the
            operator saying, in so many words, that they vouch for whoever
            passes it;
          • a send originated in the backend by an internal user.
        """
        self.ensure_one()
        brand = self.brand_id
        if brand.fixed_recipient:
            return True
        if (self.ip_created or "") == "backend":
            return True
        return bool((brand._effective_sender_allowlist() or "").strip())

    def _reply_to_sender(self):
        """Reply-To pointing at the human who sent the transfer, when the
        instance vouches for them (see ``_reply_to_allowed``).

        Without it, hitting « Reply » on a notification answers the brand
        mailbox (``email_from`` — a catch-all on most tenants), so the reply
        lands nowhere near the person who shared the files. The From stays the
        brand address, so SPF/DKIM/DMARC are untouched: only the courtesy
        header moves.
        """
        self.ensure_one()
        if not self.sender_email or not self._reply_to_allowed():
            return False
        return formataddr((self.sender_name or "", self.sender_email))

    def _send_link_emails(self, recipients_only=False):
        """Queue the branded emails: the download link to EACH recipient (in
        that recipient's contact language when known) and the receipt to the
        sender (in the sender's language). The password is NEVER included; the
        share link always rides the brand domain, never web.base.url.

        ``recipients_only`` skips the sender receipt — a manual re-send exists
        because a RECIPIENT lost the mail; the sender already has theirs."""
        link_tmpl = self.env.ref(
            "bf_securetransfer.mail_template_transfer_link",
            raise_if_not_found=False,
        )
        # Two situations must NOT carry the message body in the notification —
        # in clear, it would sit in the recipient's inbox and defeat the point:
        #   • the content is held behind a recipient code — which is the
        #     per-transfer force flag OR the instance-wide setting, i.e.
        #     _recipient_otp_required(), the SAME predicate the download
        #     controller gates on. Testing only the per-transfer flag here
        #     silently defeated the instance-wide setting: the gate held the
        #     files, but the notification still carried the message body and
        #     the filename/size listing into the recipient's inbox;
        #   • the transfer is message-only (no files — the message ITSELF is the
        #     payload, e.g. a secure note or a password typed in « Message seul »).
        # Both use the link-only "secure message awaits you" template, whose body
        # is never emailed: it is revealed only on the branded page via the link.
        secure_tmpl = self.env.ref(
            "bf_securetransfer.mail_template_secure_message",
            raise_if_not_found=False,
        )
        receipt_tmpl = self.env.ref(
            "bf_securetransfer.mail_template_transfer_receipt",
            raise_if_not_found=False,
        )
        for rec in self:
            recipients = rec._recipient_list()
            message_only = not rec.file_ids and bool((rec.message or "").strip())
            use_secure = (rec._recipient_otp_required() or message_only) and secure_tmpl
            tmpl = secure_tmpl if use_secure else link_tmpl
            reply_to = rec._reply_to_sender()
            if tmpl:
                # One email per recipient so each renders in their own language.
                for r in recipients:
                    lang = rec._lang_for_email(r)
                    values = {"email_to": r}
                    if reply_to:
                        values["reply_to"] = reply_to
                    subject = rec.with_context(lang=lang)._email_subject_line(
                        "secure" if use_secure else "link")
                    if subject:
                        values["subject"] = subject
                    tmpl.sudo().with_context(lang=lang).send_mail(
                        rec.id, force_send=False, email_values=values)
            if receipt_tmpl and not recipients_only:
                lang = rec._lang_for_email(rec.sender_email)
                subject = rec.with_context(lang=lang)._email_subject_line("receipt")
                receipt_tmpl.sudo().with_context(lang=lang).send_mail(
                    rec.id, force_send=False,
                    email_values={"subject": subject} if subject else None)
            actor = rec.env.context.get("st_resend_actor")
            log_env = rec._log_env()
            rec._log(
                "emailed",
                actor=actor,
                note=(log_env._("Link manually re-sent to: %s")
                      % (rec.recipient_emails or "")) if actor else
                (log_env._("Link sent to: %s; receipt to: %s")
                 % (rec.recipient_emails or log_env._("(none, link-only mode)"),
                    rec.sender_email)),
            )
        self._wake_mail_queue()

    def _wake_mail_queue(self):
        """Run the mail queue now rather than at its next tick.

        Every branded e-mail above is queued (``force_send=False``), and a
        queued mail.mail waits for « Mail: Email Queue Manager », which ticks
        every five minutes on a single cron worker. Measured on a production
        tenant: a median of 107 s between finalize and delivery, up to 327 s,
        while bf_sign, which force-sends, delivered in under a second.

        Queuing itself is kept on purpose. ``force_send=True`` would talk SMTP
        inside the current transaction, before the commit: the public finalize
        would wait on the mail server for every recipient, and a rollback after
        the send would leave a recipient holding a link to a transfer that was
        never saved. ``_trigger()`` only writes an ir.cron.trigger row and
        notifies the cron worker AFTER the commit (postcommit hook), so the mail
        still leaves once the transfer exists, a few seconds later instead of
        minutes. Same pattern as bf_meeting's direct report send.
        """
        cron = self.env.ref("mail.ir_cron_mail_scheduler_action",
                            raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    # ------------------------------------------------------------------ operator buttons
    def action_expire_now(self):
        """Kill the link immediately; the purge cron reclaims the objects."""
        for rec in self:
            if rec.state != "active":
                raise UserError(_("Only an active transfer can be expired."))
            rec.state = "expired"
            rec._log(
                "expired", actor=self.env.user.login,
                note=rec._log_env()._("Manual expiry"),
            )
        return True

    def action_suspend(self):
        """Abuse kill-switch: the link goes dark but nothing is purged yet
        (evidence preservation)."""
        for rec in self:
            if rec.state not in ("active", "expired"):
                raise UserError(_(
                    "Only an active or expired transfer can be suspended."
                ))
            rec.state = "suspended"
            rec._log(
                "suspended", actor=self.env.user.login,
                note=rec._log_env()._("Manual suspension (abuse)"),
            )
        return True

    def _suspend_for_abuse(self, ip=None, ua=None):
        """Auto-suspend on an abuse report: the link goes dark IMMEDIATELY and
        stays dark until an admin reactivates or purges. Idempotent — only an
        active/expired transfer flips (a suspended/deleted one is left as is).
        Nothing is purged (evidence preservation)."""
        self.ensure_one()
        if self.state in ("active", "expired"):
            self.sudo().state = "suspended"
            self._log(
                "suspended", ip=ip, ua=ua, actor="abuse_report",
                note=self._log_env()._(
                    "Automatically suspended following an abuse "
                    "report, awaiting review by the administrator"),
            )

    def action_reactivate(self):
        """Admin restores a wrongly-reported transfer (suspended → active),
        provided it is still within its expiry and its files are intact."""
        for rec in self:
            if rec.state != "suspended":
                raise UserError(_("Only a suspended transfer can be "
                                  "reactivated."))
            if rec.purged_at or not rec.file_ids.filtered(
                    lambda f: f.state == "verified"):
                raise UserError(_(
                    "This transfer can no longer be reactivated: its "
                    "files have been purged."))
            if rec.expiry_date and rec.expiry_date < fields.Datetime.now():
                raise UserError(_(
                    "This transfer has expired; it cannot be reactivated."))
            rec.state = "active"
            rec._log(
                "reactivated", actor=self.env.user.login,
                note=rec._log_env()._("Reactivated after review (report dismissed)"),
            )
        return True

    def action_purge_now(self):
        """Expire if needed, then purge the S3 objects immediately. Manager
        only — this is destructive on the storage side."""
        if not self.env.user.has_group(
            "bf_securetransfer.group_securetransfer_manager"
        ):
            raise UserError(_(
                "Action restricted to secure transfer managers."
            ))
        for rec in self:
            if rec.state == "active":
                rec.state = "expired"
                rec._log(
                    "expired", actor=self.env.user.login,
                    note=rec._log_env()._("Manual expiry (immediate purge)"),
                )
            if rec.state not in ("draft", "expired", "suspended"):
                continue  # already deleted/cancelled — nothing to purge
            rec._purge_s3(actor=self.env.user.login)
        return True

    def action_reveal_link(self):
        """Open the journaled reveal wizard (the only sanctioned backend
        path to the share link — the token itself is manager-only)."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Reveal the share link"),
            "res_model": "secure.transfer.reveal.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_transfer_id": self.id},
        }

    def action_require_recipient_otp(self):
        """Arm the recipient code on a transfer that was already sent.

        The link itself does not change: the next visitor has to prove they
        hold one of the recipient addresses before the message or the files
        are rendered. What already left is NOT recalled — an e-mail sent
        before the gate was armed keeps whatever it carried, which is exactly
        why the option belongs on the public form too.

        ⚠ A method without a leading underscore is an RPC surface: the button's
        ``groups=`` attribute only guards the UI. The securetransfer USER group
        is read-only on secure.transfer (ir.model.access.csv), so writing the
        flag in sudo would have let a read-only account change a transfer over
        XML-RPC. The check below plus the plain (non-sudo) write put the ACL
        back in charge — same posture as the reveal wizard.
        """
        if not self.env.user.has_group(
                "bf_securetransfer.group_securetransfer_manager"):
            raise UserError(_(
                "Action restricted to secure transfer managers."
            ))
        for rec in self:
            if rec.state not in ("active", "expired", "suspended"):
                raise UserError(_(
                    "Only an active, expired or suspended transfer can be "
                    "made to require a code."
                ))
            if rec.force_recipient_otp:
                continue
            if not rec._recipient_list():
                raise UserError(_(
                    "This transfer has no recorded recipient (link-only "
                    "mode): nobody could receive the code. Expire it "
                    "instead."
                ))
            rec.force_recipient_otp = True
            rec._log(
                "otp_forced", actor=self.env.user.login,
                note=rec._log_env()._(
                    "Recipient code required after the fact; emails "
                    "already sent are not recalled."),
            )
            rec.message_post(body=_(
                "Recipient code required after the fact: the link no "
                "longer opens without a code sent to one of the recipient "
                "addresses. Emails already sent are not recalled."
            ))
        return True

    def action_resend_emails(self):
        """Re-send the link e-mail to the recipients of an active transfer.

        The case this exists for is « the recipient never got it / deleted it »
        — so the sender receipt is NOT re-sent: it would be noise, and it
        carries the share link a second time into a mailbox that already has
        it. Journaled like the first send (same ``emailed`` event), with the
        operator recorded as the actor.

        ⚠ A method without a leading underscore is an RPC surface: the button's
        ``groups=`` only guards the UI. The securetransfer USER group is
        read-only on secure.transfer, so without this check a read-only account
        could make the server mail the share link over XML-RPC.
        """
        if not self.env.user.has_group(
                "bf_securetransfer.group_securetransfer_manager"):
            raise UserError(_(
                "Action restricted to secure transfer managers."
            ))
        for rec in self:
            if rec.state != "active":
                raise UserError(_(
                    "Emails can only be resent for an active transfer."
                ))
            if not rec._recipient_list():
                raise UserError(_(
                    "This transfer has no recorded recipient (\"link "
                    "only\" mode): there is nobody to re-send the link "
                    "to. Use \"Reveal the link\" and forward it yourself."
                ))
            rec.with_context(
                st_resend_actor=self.env.user.login,
            )._send_link_emails(recipients_only=True)
        return True

    # ------------------------------------------------------------------ expiry extension
    def _extension_choices(self):
        """The retention durations this transfer could still be moved to:
        what the brand offers, strictly longer than what it already has.
        Empty when it already sits on the longest tier the brand grants."""
        self.ensure_one()
        if not self.brand_id:
            return []
        offered = self.brand_id._effective_limits()["expiry_choices"]
        return [d for d in offered if d > (self.retention_days or 0)]

    def extend_expiry(self, days):
        """Move the deadline out to another retention tier the brand offers.

        Why a tier and not a free date: the S3 lifecycle net posted on the
        bucket is computed from the LONGEST retention any brand may grant
        (``_orphan_expiry_days``). A hand-picked date beyond that grid promises
        the client a day the storage provider will not honour — the files would
        be gone while the link still said « disponible jusqu'au ». Staying on
        the grid keeps the promise inside the net.

        Reopens an expired transfer when its objects are still there. Refuses
        the two cases where a later date would change nothing: a burn already
        consumed, and a download budget already spent.
        """
        if not self.env.user.has_group(
                "bf_securetransfer.group_securetransfer_manager"):
            raise UserError(_(
                "Action restricted to secure transfer managers."
            ))
        try:
            days = int(days)
        except (TypeError, ValueError):
            raise UserError(_("Invalid extension duration."))
        for rec in self:
            if rec.state not in ("active", "expired"):
                raise UserError(_(
                    "Only an active or expired transfer can be extended."
                ))
            if rec.purged_at:
                raise UserError(_(
                    "This transfer's files have already been purged from "
                    "storage: extending the expiry would not bring them "
                    "back."
                ))
            if days not in rec._extension_choices():
                raise UserError(_(
                    "Duration not offered by \"%(brand)s\", or shorter "
                    "than the current one (%(current)s day(s)). Available "
                    "choices: %(choices)s.",
                    brand=rec.brand_id.display_name,
                    current=rec.retention_days,
                    choices=", ".join(str(d) for d in rec._extension_choices())
                    or _("none: the transfer is already at the longest "
                         "duration this service offers"),
                ))
            if rec.burn_after_download and rec.state == "expired":
                raise UserError(_(
                    "This transfer was set to \"destroy after reading\" "
                    "and it was read: extending it would reopen content "
                    "the sender asked to destroy. Create a new send "
                    "instead."
                ))
            if rec.max_downloads and rec.download_count >= rec.max_downloads:
                raise UserError(_(
                    "This transfer's download budget is spent (%(done)s / "
                    "%(budget)s): a later date would not reopen it. Raise "
                    "\"Max. downloads\" first.",
                    done=rec.download_count, budget=rec.max_downloads,
                ))
            # Anchored on the finalize date, not on today: the retention a
            # transfer carries has to keep meaning « N days of availability
            # from the send », which is what the lifecycle net assumes.
            base = rec.finalized_at or rec.create_date or fields.Datetime.now()
            new_expiry = base + timedelta(days=days)
            if new_expiry <= fields.Datetime.now():
                raise UserError(_(
                    "Even extended to %(days)s day(s), this transfer "
                    "stays past due (sent on %(sent)s). Create a new send "
                    "instead.",
                    days=days,
                    sent=fields.Datetime.to_string(base),
                ))
            was_expired = rec.state == "expired"
            vals = {"retention_days": days, "expiry_date": new_expiry}
            if was_expired:
                vals["state"] = "active"
            rec.write(vals)
            log_env = rec._log_env()
            rec._log(
                "extended", actor=self.env.user.login,
                note=log_env._(
                    "Expiry moved to %(days)s day(s): new expiry on "
                    "%(date)s%(reopened)s",
                    days=days,
                    date=fields.Datetime.to_string(new_expiry),
                    reopened=log_env._(" (transfer reopened)") if was_expired else "",
                ),
            )
        return True

    def action_extend_expiry(self):
        """Open the extension wizard (the sanctioned, journaled path — the
        expiry date itself is read-only on the form).

        The guard is redundant twice over (the wizard's ACL is manager-only and
        ``extend_expiry`` re-checks), but this method is an RPC surface like
        the others on this model, and « refuses at the last moment » is a worse
        posture than « refuses at the door »."""
        self.ensure_one()
        if not self.env.user.has_group(
                "bf_securetransfer.group_securetransfer_manager"):
            raise UserError(_(
                "Action restricted to secure transfer managers."
            ))
        return {
            "type": "ir.actions.act_window",
            "name": _("Extend the expiry"),
            "res_model": "secure.transfer.extend.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_transfer_id": self.id},
        }

    def action_export_log(self):
        """Export this transfer's Loi 25 access trail as a downloadable CSV.
        The first line records the hash-chain verdict (verify_chain) so the
        exported file is self-attesting. Returns an ir.actions.act_url that
        downloads the generated ir.attachment."""
        self.ensure_one()
        # verify_chain() re-derives the whole per-transfer chain from any one
        # of its log rows (ensure_one on the log side), so call it on a single
        # entry — never the full recordset.
        chain_ok = self.access_log_ids[:1].verify_chain() if self.access_log_ids else True
        buf = io.StringIO()
        writer = csv.writer(buf)
        # The first line is for the operator who exports (their language);
        # the column names below are the file's schema and do not vary with
        # the reader's language.
        writer.writerow([
            self.env._("# Access log: %s", self.name),
            self.env._("Integrity chain: %s",
                       self.env._("INTACT") if chain_ok else self.env._("BROKEN")),
        ])
        writer.writerow([
            "horodatage_utc", "action", "acteur", "ip",
            "user_agent", "fichier", "note", "empreinte",
        ])
        for e in self.access_log_ids.sorted("id"):
            writer.writerow([
                e.timestamp_utc or "", e.action or "", e.actor or "",
                e.ip or "", e.user_agent or "",
                e.file_id.filename if e.file_id else "",
                e.note or "", e.entry_hash or "",
            ])
        data = buf.getvalue().encode("utf-8-sig")  # BOM → Excel opens UTF-8
        att = self.env["ir.attachment"].create({
            "name": self.env._("access-log-%s.csv", self.name or self.id),
            "type": "binary",
            "datas": base64.b64encode(data),
            "mimetype": "text/csv",
            "res_model": self._name,
            "res_id": self.id,
        })
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % att.id,
            "target": "self",
        }

    def action_verify_log_chain(self):
        """Operator button: verify this transfer's access-log hash chain and
        report the verdict as a notification."""
        self.ensure_one()
        ok = self.access_log_ids[:1].verify_chain() if self.access_log_ids else True
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Log verification"),
                "message": _("Integrity chain intact for %s.", self.name)
                if ok else
                _("ALERT: the integrity chain of %s is BROKEN.", self.name),
                "type": "success" if ok else "danger",
                "sticky": not ok,
            },
        }

    # ----------------------------------------------------- access certificate
    def _certificate_context(self):
        """Everything the access certificate prints, resolved server-side.

        The CSV export answers « give me the rows »; this answers « prove to a
        third party what happened ». Same trail, but self-attesting: the chain
        verdict, its two endpoints, and the exact recipe to recompute it are
        printed alongside the entries, so the client does not have to take our
        word for the hashes.
        """
        self.ensure_one()
        entries = self.access_log_ids.sorted("id")
        Log = self.env["secure.transfer.access.log"]
        labels = dict(
            Log._fields["action"]._description_selection(self.env)
        )
        rows = [{
            "seq": i,
            "timestamp": e.timestamp_utc or "",
            "action": labels.get(e.action, e.action or ""),
            "actor": e.actor or "",
            "ip": e.ip or "",
            "user_agent": (e.user_agent or "")[:60],
            "file": e.file_id.filename if e.file_id else "",
            "note": e.note or "",
            "hash": e.entry_hash or "",
        } for i, e in enumerate(entries, start=1)]
        # Les horodatages sont rendus ici, en UTC explicite, et pas par t-field :
        # le journal est déjà en UTC, et une date sans fuseau (« 07/13/2026 »)
        # ne prouve rien sur une pièce qu'on oppose à quelqu'un.
        def utc(dt):
            return dt.strftime("%Y-%m-%d %H:%M:%S UTC") if dt else "—"

        return {
            "rows": rows,
            "intact": entries[:1].verify_chain() if entries else True,
            "first_hash": entries[:1].entry_hash or "" if entries else "",
            "last_hash": entries[-1:].entry_hash or "" if entries else "",
            "created_at": utc(self.create_date),
            "expiry_at": utc(self.expiry_date),
            "generated_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"),
            "generated_by": self.env.user.name,
        }

    def action_print_certificate(self):
        """Operator button: the branded, opposable access certificate (PDF).

        ``config=False`` on purpose: for an admin whose company has no document
        layout configured, ``report_action`` hijacks the print and returns the
        « configure your layout » wizard instead of the PDF. This certificate
        never uses the company layout — it wears the CLIENT's brand — so that
        check is beside the point here, and it would simply block the button.
        """
        self.ensure_one()
        return self.env.ref(
            "bf_securetransfer.action_report_access_certificate"
        ).report_action(self, config=False)

    # ------------------------------------------------------------------ purge
    def _purge_s3(self, actor=None):
        """Delete this transfer's S3 footprint (abort pending MPUs, batch
        delete the objects — NoSuchKey counts as success), then flip the
        state: deleted (or cancelled for a harvested draft). Metadata and
        the access trail are KEPT. Idempotent; returns True on full success.

        Per-key failures increment purge_error_count and schedule an admin
        activity when it crosses 5 (exactly once — no activity spam);
        endpoint-unreachable errors propagate so the calling cron can abort
        its run cleanly."""
        self.ensure_one()
        files = self.file_ids.filtered(lambda f: f.state != "purged")
        for f in files.filtered("s3_upload_id"):
            # mpu_abort raises on endpoint errors — let the cron abort.
            s3.mpu_abort(self.env, f.s3_key, f.s3_upload_id)
            f.s3_upload_id = False
        keys = [f.s3_key for f in files if f.s3_key]
        failed = s3.delete_keys(self.env, keys) if keys else []
        if failed:
            self.purge_error_count += 1
            _logger.error(
                "bf_securetransfer: purge en échec pour %s (tentative %s) — "
                "clés restantes : %s",
                self.name, self.purge_error_count, failed,
            )
            if self.purge_error_count == 5:
                # Written in the ADMIN's language and created in an env that
                # carries it: the caller is the purge cron (OdooBot).
                admin = self.env.ref("base.user_admin")
                this = self.with_context(lang=active_lang(
                    self.env, admin.lang or self.company_id.partner_id.lang))
                this.activity_schedule(
                    "mail.mail_activity_data_todo",
                    user_id=admin.id,
                    summary=this.env._("S3 purge repeatedly failing: %s", self.name),
                    note=this.env._(
                        "5 purge attempts have failed for this transfer. "
                        "Check the bucket and the remaining keys: %s", failed,
                    ),
                )
            return False
        files.write({"state": "purged"})
        was_draft = self.state == "draft"
        self.write({
            "state": "cancelled" if was_draft else "deleted",
            "purged_at": fields.Datetime.now(),
        })
        log_env = self._log_env()
        self._log(
            "purged", actor=actor,
            note=log_env._("Abandoned draft: %s object(s) deleted from storage")
            % len(keys) if was_draft
            else log_env._("%s object(s) deleted from storage; metadata and log kept")
            % len(keys),
        )
        return True

    # ------------------------------------------------------------------ crons
    @api.model
    def _cron_purge_expired(self):
        """Daily 03:15 — flip overdue actives to expired, then purge the S3
        objects of every expired transfer. Idempotent: a killed run simply
        catches up at the next pass. An unreachable endpoint aborts the run
        cleanly instead of burning error counters."""
        now = fields.Datetime.now()
        for rec in self.search([
            ("state", "=", "active"),
            ("expiry_date", "!=", False),
            ("expiry_date", "<=", now),
        ]):
            rec.state = "expired"
            rec._log("expired", note=rec._log_env()._("Expiry date reached"))

        for rec in self.search([("state", "=", "expired")]):
            try:
                rec._purge_s3()
            except UserError as e:
                # S3 not configured (fresh install) — nothing purgeable yet.
                _logger.warning(
                    "bf_securetransfer: purge interrompue — %s", e,
                )
                return
            except Exception as e:  # noqa: BLE001 — classify, then re-raise
                if s3.is_endpoint_error(e):
                    _logger.warning(
                        "bf_securetransfer: endpoint S3 injoignable — run de "
                        "purge interrompu, reprise au prochain passage.",
                    )
                    return
                raise

    @api.model
    def _gc_drafts_domain(self, cutoff):
        """Brouillons que le ménage horaire peut récolter.

        Un module qui garde volontairement un brouillon ouvert plus longtemps que
        le délai (une demande de documents qui attend ses pièces jusqu'à son temps
        limite) en retire les siens en étendant cette méthode.
        """
        return [("state", "=", "draft"), ("create_date", "<=", cutoff)]

    @api.model
    def _cron_gc_drafts(self):
        """Hourly — harvest abandoned drafts (abort MPUs, delete uploaded
        objects, state cancelled) and sweep the bucket for orphaned MPUs
        that no live draft claims (48 h grace)."""
        ttl = s3._int_param(self.env, "draft_ttl_hours", 24)
        cutoff = fields.Datetime.now() - timedelta(hours=ttl)
        for rec in self.search(self._gc_drafts_domain(cutoff)):
            try:
                rec._purge_s3()
            except UserError as e:
                _logger.warning(
                    "bf_securetransfer: GC des brouillons interrompu — %s", e,
                )
                return
            except Exception as e:  # noqa: BLE001 — classify, then re-raise
                if s3.is_endpoint_error(e):
                    _logger.warning(
                        "bf_securetransfer: endpoint S3 injoignable — GC des "
                        "brouillons interrompu.",
                    )
                    return
                raise

        # -- bucket-side sweep: MPUs nobody claims anymore
        try:
            # Scoped to THIS tenant's key prefix: other tenants sharing the
            # bucket sweep their own prefix — never each other's uploads.
            stale = s3.list_stale_mpus(
                self.env, s3.key_prefix(self.env) + "/", MPU_SWEEP_GRACE_HOURS,
            )
            live_ids = set(
                self.env["secure.transfer.file"].sudo().search([
                    ("s3_upload_id", "!=", False),
                ]).mapped("s3_upload_id")
            )
            for mpu in stale:
                if mpu["upload_id"] not in live_ids:
                    s3.mpu_abort(self.env, mpu["key"], mpu["upload_id"])
                    _logger.info(
                        "bf_securetransfer: MPU orphelin annulé (%s)",
                        mpu["key"],
                    )

            # -- bucket-side sweep: OBJECTS with no live DB row (re-PUT after a
            #    remove, a delete that half-failed, a rolled-back finalize).
            #    Scoped to this tenant's prefix, with the same grace window so
            #    an in-flight upload is never deleted. probes/ keys are the
            #    setup action's own scratch space — skip them.
            prefix = s3.key_prefix(self.env) + "/"
            objects = s3.list_objects(self.env, prefix, MPU_SWEEP_GRACE_HOURS)
            if objects:
                known = set(
                    self.env["secure.transfer.file"].sudo().search([
                        ("state", "in", ("pending", "uploading", "uploaded",
                                         "verified")),
                    ]).mapped("s3_key")
                )
                orphans = [
                    o["key"] for o in objects
                    if o["key"] not in known
                    and not o["key"].startswith(prefix + "probes/")
                ]
                if orphans:
                    failed = s3.delete_keys(self.env, orphans)
                    _logger.info(
                        "bf_securetransfer: %d objet(s) orphelin(s) supprimé(s)"
                        ", %d échec(s)", len(orphans) - len(failed), len(failed),
                    )
        except UserError:
            return  # S3 not configured — nothing to sweep
        except Exception as e:  # noqa: BLE001 — classify, then re-raise
            if s3.is_endpoint_error(e):
                _logger.warning(
                    "bf_securetransfer: endpoint S3 injoignable — balayage "
                    "MPU interrompu.",
                )
                return
            raise

    @api.model
    def _cron_gc_logs(self):
        """Weekly — hard GC of long-dead transfers after the log retention
        period. The ONLY path that ever deletes log entries (DB cascade
        under the st_gc context)."""
        days = s3._int_param(self.env, "log_retention_days", 365)
        cutoff = fields.Datetime.now() - timedelta(days=days)
        doomed = self.search([
            ("state", "in", ("deleted", "cancelled")),
            "|",
            "&", ("purged_at", "!=", False), ("purged_at", "<=", cutoff),
            "&", ("purged_at", "=", False), ("write_date", "<=", cutoff),
        ])
        if doomed:
            count = len(doomed)
            doomed.with_context(st_gc=True).unlink()
            _logger.info(
                "bf_securetransfer: GC dur — %s transfert(s) et leur journal "
                "supprimés après la période de rétention (%s jours).",
                count, days,
            )
