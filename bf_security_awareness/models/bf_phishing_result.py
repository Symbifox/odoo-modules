import logging
import secrets
from urllib.parse import quote

from psycopg2 import IntegrityError

from odoo import api, fields, models

from .reader_lang import company_env

_logger = logging.getLogger(__name__)

# Forward-only state ordering. A result never moves backwards: an "opened"
# result that is later clicked becomes "clicked"; a click after a submit stays
# "submitted". This keeps the worst observed outcome as the record's state.
STATE_ORDER = {
    "pending": 0,
    "sent": 1,
    "opened": 2,
    "clicked": 3,
    "submitted": 4,
}


class BfPhishingResult(models.Model):
    """One row per (campaign, person). Doubles as the mass-mailing target
    model: each row carries a unique ``token`` and an ``email``, so the native
    ``mailing.mailing`` engine renders a per-recipient ``/phish/<token>`` link
    and produces a ``mailing.trace`` we can read delivery/bounce state from.

    SECURITY NOTE: there is deliberately NO field that can hold a submitted
    username or password value. Only booleans and field *lengths* exist. The
    "password is never stored" guarantee is structural, not behavioural.
    """

    _name = "bf.phishing.result"
    _description = "Phishing simulation result"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"
    _primary_email = "email"  # used by mass-mailing recipient resolution

    campaign_id = fields.Many2one(
        "bf.phishing.campaign", string="Campaign",
        required=True, ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(
        related="campaign_id.company_id", store=True, index=True,
    )
    partner_id = fields.Many2one(
        "res.partner", string="Person", required=True,
        ondelete="cascade", index=True, tracking=True,
    )
    email = fields.Char(string="Target email", required=True)
    token = fields.Char(
        string="Token", required=True, index=True, copy=False, readonly=True,
        default=lambda self: secrets.token_urlsafe(32),
    )
    profile_id = fields.Many2one(
        "bf.security.profile", string="Risk profile",
        ondelete="set null", index=True,
    )

    # --- State machine --------------------------------------------------
    state = fields.Selection(
        [
            ("pending", "Waiting to be sent"),
            ("sent", "Sent"),
            ("opened", "Opened"),
            ("clicked", "Clicked"),
            ("submitted", "Credentials submitted"),
        ],
        string="Status", default="pending", required=True, tracking=True,
    )
    reported = fields.Boolean(string="Reported the phishing", tracking=True)
    bounced = fields.Boolean(string="Bounced (rejected)", tracking=True)

    scheduled_send = fields.Datetime(
        string="Scheduled send",
        help="Scheduled send time (campaign spreading). The scheduled job "
             "releases the lures whose time has come.")
    sent_datetime = fields.Datetime(string="Sent on")
    opened_datetime = fields.Datetime(string="Opened on")
    clicked_datetime = fields.Datetime(string="Clicked on")
    submitted_datetime = fields.Datetime(string="Submitted on")
    reported_datetime = fields.Datetime(string="Reported on")

    # --- Credential-capture-safe fields (NO value is ever stored) -------
    credentials_submitted = fields.Boolean(string="Submitted the fake form")
    submitted_username_len = fields.Integer(string="Entered username length")
    submitted_password_len = fields.Integer(string="Entered password length")

    # --- Forensic-light -------------------------------------------------
    click_ip = fields.Char(string="Click IP")
    click_country_id = fields.Many2one("res.country", string="Click country")
    user_agent = fields.Char(string="User agent")

    # --- Links ----------------------------------------------------------
    mailing_trace_id = fields.Many2one(
        "mailing.trace", string="Mailing trace",
        compute="_compute_mailing_trace_id", store=False,
    )
    training_assignment_id = fields.Many2one(
        "bf.training.assignment", string="Assigned training",
        ondelete="set null",
    )

    landing_url = fields.Char(
        string="Trap link", compute="_compute_urls", store=False)
    open_pixel_url = fields.Char(compute="_compute_urls", store=False)
    report_url = fields.Char(compute="_compute_urls", store=False)
    qr_src = fields.Char(
        string="QR code source", compute="_compute_urls", store=False,
        help="QR code image URL (encodes the trap link) through Odoo's "
             "public barcode controller.")

    _sql_constraints = [
        ("token_uniq", "unique(token)", "The token must be unique."),
        ("campaign_partner_uniq", "unique(campaign_id, partner_id)",
         "This person is already targeted by this campaign."),
    ]

    # ------------------------------------------------------------------ #
    # Compute
    # ------------------------------------------------------------------ #
    def _base_url(self):
        return self.env["ir.config_parameter"].sudo().get_param(
            "web.base.url", "").rstrip("/")

    @api.depends("token")
    def _compute_urls(self):
        base = self._base_url()
        for rec in self:
            if rec.token:
                rec.landing_url = "%s/phish/%s" % (base, rec.token)
                rec.open_pixel_url = "%s/phish/%s/open.png" % (base, rec.token)
                rec.report_url = "%s/phish/%s/report" % (base, rec.token)
                rec.qr_src = (
                    "%s/report/barcode/?barcode_type=QR&value=%s"
                    "&width=180&height=180"
                    % (base, quote(rec.landing_url, safe="")))
            else:
                rec.landing_url = rec.open_pixel_url = rec.report_url = False
                rec.qr_src = False

    def _compute_mailing_trace_id(self):
        Trace = self.env["mailing.trace"]
        for rec in self:
            rec.mailing_trace_id = Trace.search(
                [("model", "=", self._name), ("res_id", "=", rec.id)],
                order="id desc", limit=1,
            )

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #
    @api.model_create_multi
    def create(self, vals_list):
        Profile = self.env["bf.security.profile"]
        for vals in vals_list:
            if not vals.get("token"):
                vals["token"] = secrets.token_urlsafe(32)
            if vals.get("partner_id") and not vals.get("profile_id"):
                vals["profile_id"] = Profile._get_or_create(
                    vals["partner_id"]).id
        return super().create(vals_list)

    # ------------------------------------------------------------------ #
    # State transitions — all idempotent and forward-only
    # ------------------------------------------------------------------ #
    def _advance_state(self, new_state):
        """Move state forward only; return True if it changed."""
        self.ensure_one()
        if STATE_ORDER[new_state] > STATE_ORDER[self.state]:
            self.state = new_state
            return True
        return False

    # The notes below come from the public phishing page (public user) or an
    # employee's report: the security team reads them, in its company's language.
    def register_sent(self):
        for rec in self:
            if rec._advance_state("sent"):
                rec.sent_datetime = fields.Datetime.now()

    def register_open(self):
        for rec in self:
            if not rec.opened_datetime:
                rec.opened_datetime = fields.Datetime.now()
            rec._advance_state("opened")

    def register_click(self, ip=None, country_id=None, user_agent=None):
        for rec in self:
            if not rec.clicked_datetime:
                rec.clicked_datetime = fields.Datetime.now()
            if ip and not rec.click_ip:
                rec.click_ip = ip[:64]
            if country_id and not rec.click_country_id:
                rec.click_country_id = country_id
            if user_agent and not rec.user_agent:
                rec.user_agent = user_agent[:256]
            rec._advance_state("clicked")
            team = company_env(rec, rec.company_id)
            team.message_post(body=team.env._("Clicked the trap link."))
            rec._maybe_assign_training()

    def register_submit(self, username_len=None, password_len=None):
        """Record that the fake form was submitted. NEVER receives or stores
        the actual values — only integer lengths, and only if the template
        enabled length capture."""
        for rec in self:
            rec.credentials_submitted = True
            if username_len is not None:
                rec.submitted_username_len = int(username_len)
            if password_len is not None:
                rec.submitted_password_len = int(password_len)
            if not rec.submitted_datetime:
                rec.submitted_datetime = fields.Datetime.now()
            rec._advance_state("submitted")
            team = company_env(rec, rec.company_id)
            team.message_post(body=team.env._("Submitted the fake login form."))
            rec._maybe_assign_training()

    def register_report(self):
        for rec in self:
            if not rec.reported:
                rec.reported = True
                rec.reported_datetime = fields.Datetime.now()
                team = company_env(rec, rec.company_id)
                team.message_post(body=team.env._("Reported the email as phishing."))

    def register_bounce(self):
        for rec in self:
            rec.bounced = True

    # ------------------------------------------------------------------ #
    # Remediation
    # ------------------------------------------------------------------ #
    def _maybe_assign_training(self):
        self.ensure_one()
        campaign = self.campaign_id
        channel = campaign.template_id.training_channel_id
        if not campaign.auto_assign_training or not channel:
            return
        if self.training_assignment_id:
            return
        Assignment = self.env["bf.training.assignment"]
        # Avoid duplicate open assignments for the same person + course.
        existing = Assignment.search([
            ("partner_id", "=", self.partner_id.id),
            ("channel_id", "=", channel.id),
            ("state", "not in", ("done", "cancelled")),
        ], limit=1)
        if existing:
            self.training_assignment_id = existing
            return
        # Two concurrent failures for the same (partner, channel) can both reach
        # this point; the unique constraint protects the DB. Create inside a
        # savepoint and fall back to the existing row on conflict. This also
        # covers re-failing after a previously *completed* assignment (which the
        # search above intentionally skips).
        try:
            with self.env.cr.savepoint():
                assignment = Assignment.create({
                    "partner_id": self.partner_id.id,
                    "channel_id": channel.id,
                    "source_result_id": self.id,
                    # 🔴 Le clic arrive par la page publique, en sudo depuis
                    # l'utilisateur public : par défaut, c'est lui qui « assignait »,
                    # et ses relances allaient à un compte archivé.
                    "assigned_by_id": campaign.create_uid.id,
                })
            assignment.action_enroll()
        except IntegrityError:
            assignment = Assignment.search([
                ("partner_id", "=", self.partner_id.id),
                ("channel_id", "=", channel.id),
            ], limit=1)
        self.training_assignment_id = assignment

    # ------------------------------------------------------------------ #
    # Trace sync (delivery / bounce) — driven by a campaign cron
    # ------------------------------------------------------------------ #
    def _sync_from_traces(self):
        """Pick up bounces from the native mailing traces. Opens/clicks/submits
        are captured in real time by the /phish controller, so we only reconcile
        delivery failures here."""
        for rec in self:
            trace = rec.mailing_trace_id
            if trace and trace.trace_status in ("bounce", "error", "cancel"):
                rec.register_bounce()
