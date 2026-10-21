"""Settings panel for the secure transfer service.

Non-secret S3 parameters and default limits, stored in ir.config_parameter
(``bf_securetransfer.`` prefix). The access/secret keys are deliberately NOT
here: they live in odoo.conf / the environment only (DB parameters travel in
dumps — a staging refresh would carry the prod credentials). The view shows
a status line + a help note instead.
"""
import logging
import os

from odoo import _, api, fields, models
from odoo.tools import config as odoo_config

from . import s3
from . import sms

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # ── S3 connection (non-secret) ───────────────────────────────────────────
    st_s3_endpoint_url = fields.Char(
        string="S3 endpoint",
        config_parameter="bf_securetransfer.s3_endpoint_url",
        help="URL of the S3-compatible endpoint (IDrive E2), e.g. "
             "https://<region>.idrivee2-XX.com",
    )
    st_s3_region = fields.Char(
        string="S3 region",
        config_parameter="bf_securetransfer.s3_region",
        default="ca-east-1",
        help="Bucket region. ca-east-1 = data residency in Canada.",
    )
    st_s3_bucket = fields.Char(
        string="S3 bucket",
        config_parameter="bf_securetransfer.s3_bucket",
        help="The bucket can be shared across tenants (each isolated by "
             "its key prefix below) or dedicated. Object lock DISABLED, "
             "otherwise the purge is impossible.",
    )
    st_s3_key_prefix = fields.Char(
        string="Key prefix (tenant)",
        config_parameter="bf_securetransfer.s3_key_prefix",
        default="transfers",
        help="Root of THIS tenant's S3 keys (e.g. transfers-acme / "
             "transfers-staging). On a shared bucket, "
             "this is what prevents one tenant's purge and sweeps from "
             "touching another's objects. Must be made UNIQUE per "
             "instance.",
    )
    st_s3_path_style = fields.Boolean(
        string="Path-style addressing",
        config_parameter="bf_securetransfer.s3_path_style",
        default=True,
        help="Required by most S3-compatible storage (including IDrive E2).",
    )
    st_cors_origins = fields.Char(
        string="CORS origins",
        config_parameter="bf_securetransfer.cors_origins",
        help="Origins allowed for browser PUTs, comma-separated, e.g. "
             "https://secret.example.com. Never \"*\". Applied by "
             "\"Configure the S3 bucket\".",
    )
    st_s3_creds_status = fields.Char(
        string="S3 access keys",
        readonly=True,
        compute="_compute_st_s3_creds_status",
        help="The keys (access/secret) are declared in odoo.conf "
             "(bf_securetransfer_s3_access_key / _secret_key) or in "
             "environment variables, never in the database.",
    )

    # ── public service ───────────────────────────────────────────────────────
    st_public_base_url = fields.Char(
        string="Fallback public URL",
        config_parameter="bf_securetransfer.public_base_url",
        help="Base for share links when the brand has no domain of its "
             "own, e.g. https://secret.example.com. NEVER let "
             "web.base.url leak into the links (it often points to the "
             "backend).",
    )
    st_public_upload_enabled = fields.Boolean(
        string="Public upload enabled",
        config_parameter="bf_securetransfer.public_upload_enabled",
        default=True,
        help="Master switch for the public /secrets page. Uncheck to "
             "close the service without uninstalling (e.g. an instance "
             "used internally only).",
    )
    st_autoprovision_user_pages = fields.Boolean(
        string="Create a drop page per employee",
        config_parameter="bf_securetransfer.autoprovision_user_pages",
        help="When an internal user is created (excluding service "
             "accounts, bots and API), automatically creates and "
             "maintains their personal /to/<slug> drop page (renaming, "
             "email, archiving tracked). Unchecked = pages created "
             "manually only.",
    )
    # ⚠ Pas de `default=` : c'était une adresse de Blue Fox, et elle
    # s'installait telle quelle chez chaque locataire. L'avis d'abus porte
    # l'expéditeur, la LISTE COMPLÈTE des destinataires, le motif et l'IP du
    # signalant — il n'a rien à faire dans la boîte d'une autre organisation.
    # Laissé vide, il tombe sur le courriel de la société du locataire
    # (voir secure.transfer._abuse_desk_email).
    st_abuse_email = fields.Char(
        string="Abuse desk email",
        config_parameter="bf_securetransfer.abuse_email",
        help="Address alerted (with full detail) when abuse is reported. "
             "The transfer is suspended automatically and a neutral "
             "notice also goes to the recipients. Left empty: the company "
             "email takes over.",
    )
    st_require_sender_otp = fields.Boolean(
        string="Sender confirmation by code (OTP)",
        config_parameter="bf_securetransfer.require_sender_otp",
        help="Requires the sender to confirm a code sent to their email "
             "before the transfer goes out; this proves they control the "
             "address (anti-spoofing / anti-piggyback).",
    )
    st_require_recipient_otp = fields.Boolean(
        string="Recipient confirmation by code (OTP)",
        config_parameter="bf_securetransfer.require_recipient_otp",
        help="Requires the recipient to enter a code sent to their email "
             "before downloading; only the intended recipient accesses "
             "the files (strengthens Law 25 compliance).",
    )
    # ── recipient OTP by SMS (VoIP.ms) ───────────────────────────────────────
    st_sms_enabled = fields.Boolean(
        string="Recipient code by SMS (VoIP.ms)",
        config_parameter="bf_securetransfer.sms_enabled",
        help="Allows sending the recipient's code by SMS instead of email "
             "(chosen at send time). Requires a VoIP.ms DID enabled for "
             "SMS and the API credentials declared in odoo.conf.",
    )
    st_sms_did = fields.Char(
        string="SMS sender number (DID)",
        config_parameter="bf_securetransfer.sms_did",
        help="VoIP.ms DID enabled for SMS, from which the codes are sent "
             "(10-digit North American number).",
    )
    st_sms_creds_status = fields.Char(
        string="VoIP.ms credentials",
        readonly=True,
        compute="_compute_st_sms_creds_status",
        help="The VoIP.ms API user and password are declared in odoo.conf "
             "(bf_securetransfer_voipms_user / _password) or in "
             "environment variables, never in the database.",
    )
    # Tenant-wide allowlists (default policy; a brand overrides with its own).
    st_default_sender_allowlist = fields.Char(
        string="Authorized senders (tenant default)",
        config_parameter="bf_securetransfer.default_sender_allowlist",
        help="Global sender policy for the whole instance. Full address "
             "(jean@client.com) or domain (@client.com), comma-separated. "
             "EMPTY = open. A brand with its own list overrides it.",
    )
    st_default_recipient_allowlist = fields.Char(
        string="Authorized recipients (tenant default)",
        config_parameter="bf_securetransfer.default_recipient_allowlist",
        help="Global recipient policy. Same format. Filled = files can "
             "only be sent to these addresses/domains across the whole "
             "instance. A brand with its own list overrides it.",
    )

    # ── default limits (0-override per brand) ────────────────────────────────
    st_default_free_max_transfer_mb = fields.Integer(
        string="Max. size, free tier (MB)",
        config_parameter="bf_securetransfer.default_free_max_transfer_mb",
        default=2048,
    )
    st_default_paid_max_transfer_mb = fields.Integer(
        string="Max. size, paid tier (MB)",
        config_parameter="bf_securetransfer.default_paid_max_transfer_mb",
        default=20480,
    )
    st_default_max_files = fields.Integer(
        string="Max. number of files per transfer",
        config_parameter="bf_securetransfer.default_max_files",
        default=25,
    )
    # ⚠ Not a brand limit — a MEMORY limit. Files composed in the backend pass
    # through the worker (the public page never does: browser → S3 direct), so
    # this bounds what one operator can make a worker hold at once. The model
    # clamps it to 100 Mo whatever is set here; bigger sends belong on the
    # public page.
    st_backend_max_upload_mb = fields.Integer(
        string="Max. attachments from the backend (MB)",
        config_parameter="bf_securetransfer.backend_max_upload_mb",
        default=25,
        help="Total size accepted by the backend \"New secure send\" "
             "wizard, all files combined. These bytes travel through Odoo "
             "(unlike the public page, which uploads straight to "
             "storage): hard-capped at 100 MB.",
    )
    st_default_free_max_retention_days = fields.Integer(
        string="Max. retention, free tier (days)",
        config_parameter="bf_securetransfer.default_free_max_retention_days",
        default=7,
    )
    st_default_paid_max_retention_days = fields.Integer(
        string="Max. retention, paid tier (days)",
        config_parameter="bf_securetransfer.default_paid_max_retention_days",
        default=90,
    )

    @api.depends_context("uid")
    def _compute_st_s3_creds_status(self):
        has_keys = bool(
            (os.environ.get("BF_SECURETRANSFER_S3_ACCESS_KEY")
             or odoo_config.get("bf_securetransfer_s3_access_key"))
            and (os.environ.get("BF_SECURETRANSFER_S3_SECRET_KEY")
                 or odoo_config.get("bf_securetransfer_s3_secret_key"))
        )
        label = (
            _("Configured (odoo.conf / environment)") if has_keys
            else _("Missing: declare them in odoo.conf, never in the database")
        )
        for rec in self:
            rec.st_s3_creds_status = label

    @api.depends_context("uid")
    def _compute_st_sms_creds_status(self):
        has = sms.has_credentials()
        label = (
            _("Configured (odoo.conf / environment)") if has
            else _("Missing: must be declared in odoo.conf, never in the "
                   "database")
        )
        for rec in self:
            rec.st_sms_creds_status = label

    def action_st_setup_bucket(self):
        """Idempotent bucket provisioning: apply CORS + lifecycle and probe
        every capability the module relies on (see s3.setup_bucket). The
        full report goes to the server log; the notification summarizes.
        Reads the SAVED parameters — save the settings first."""
        self.ensure_one()
        report = s3.setup_bucket(self.env)
        for check, result in report.items():
            _logger.info(
                "bf_securetransfer setup_bucket — %s: %s — %s",
                check, "OK" if result["ok"] else "ÉCHEC", result["detail"],
            )
        failed = {c: r for c, r in report.items() if not r["ok"]}
        if failed:
            message = _(
                "%(ok)s/%(total)s probes passed. Failed: %(detail)s (full "
                "report in the server log).",
                ok=len(report) - len(failed),
                total=len(report),
                detail="; ".join(
                    "%s — %s" % (c, r["detail"]) for c, r in failed.items()
                ),
            )
        else:
            message = _(
                "All %(total)s probes passed: CORS applied, bucket ready "
                "for the secure transfer.", total=len(report),
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("S3 bucket setup"),
                "message": message,
                "type": "success" if not failed else "warning",
                "sticky": True,
            },
        }
