# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from datetime import date as date_type, timedelta

from markupsafe import Markup

from odoo import api, fields, models

from .hosting_lang import reader_env, user_env

DMARC_DOH_ENDPOINT = "https://cloudflare-dns.com/dns-query"
DMARC_PROVIDER_LABEL = "Cloudflare DoH"

_logger = logging.getLogger(__name__)


class HostingDomain(models.Model):
    _name = "hosting.domain"
    _description = "Domain"
    _order = "date_expiration asc, name"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    # Identity
    name = fields.Char(
        string="Domain name",
        required=True,
        tracking=True,
        help="Domain FQDN (e.g. example.com)",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Client",
        required=True,
        tracking=True,
        domain="[('is_company', '=', True)]",
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Assignee",
        default=lambda self: self.env.user,
        tracking=True,
    )

    # Registrar / DNS
    registrar = fields.Char(
        string="Registrar",
        tracking=True,
    )
    dns_provider = fields.Char(
        string="DNS provider",
        tracking=True,
    )

    # Domain dates
    date_registration = fields.Date(
        string="Registration date",
    )
    date_expiration = fields.Date(
        string="Expiry date",
        tracking=True,
    )
    days_until_expiration = fields.Integer(
        string="Days until expiry",
        compute="_compute_days_until_expiration",
    )
    auto_renew = fields.Boolean(
        string="Automatic renewal",
        default=False,
        tracking=True,
    )
    domain_activity_created = fields.Boolean(
        string="Expiration activity created",
        default=False,
        copy=False,
    )

    # SSL
    ssl_type = fields.Selection(
        selection=[
            ("none", "None"),
            ("letsencrypt", "Let's Encrypt"),
            ("commercial", "Commercial"),
            ("self_signed", "Self-signed"),
        ],
        string="SSL type",
        default="none",
        tracking=True,
    )
    ssl_issuer = fields.Char(
        string="SSL issuer",
    )
    ssl_expiry_date = fields.Date(
        string="SSL expiration",
        tracking=True,
    )
    days_until_ssl_expiry = fields.Integer(
        string="Days until SSL expiration",
        compute="_compute_days_until_ssl_expiry",
    )
    ssl_activity_created = fields.Boolean(
        string="SSL activity created",
        default=False,
        copy=False,
    )

    # Cost
    annual_cost = fields.Float(
        string="Annual cost",
        digits=(10, 2),
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )

    # State
    state = fields.Selection(
        selection=[
            ("active", "Active"),
            ("expiring_soon", "Expiring soon"),
            ("expired", "Expired"),
            ("transferred", "Transferred"),
        ],
        string="State",
        default="active",
        required=True,
        tracking=True,
    )

    # Relations
    service_ids = fields.One2many(
        comodel_name="hosting.service",
        inverse_name="domain_id",
        string="Services",
    )
    service_count = fields.Integer(
        string="Service count",
        compute="_compute_service_count",
    )

    # DMARC monitoring
    dmarc_managed = fields.Boolean(
        string="DMARC monitored",
        default=False,
        tracking=True,
        help="Enable DMARC monitoring for this domain (daily check of the "
             "_dmarc TXT record and alert on drift).",
    )
    dmarc_policy = fields.Selection(
        selection=[
            ("none", "none (monitoring)"),
            ("quarantine", "quarantine"),
            ("reject", "reject"),
        ],
        string="Expected DMARC policy",
        tracking=True,
    )
    dmarc_rua = fields.Char(
        string="Expected RUA recipient",
        tracking=True,
        help="Expected email address for aggregate reports (without the "
             "'mailto:' prefix).",
    )
    dmarc_pct = fields.Integer(
        string="Percentage (pct)",
        default=100,
        help="Percentage of messages subject to the policy (1-100). 100 = "
             "DMARC default.",
    )
    dmarc_dns_value_expected = fields.Char(
        string="Expected DMARC TXT",
        compute="_compute_dmarc_dns_value_expected",
        store=True,
    )
    dmarc_dns_value_observed = fields.Char(
        string="Observed DMARC TXT",
        readonly=True,
        copy=False,
    )
    dmarc_last_dns_check = fields.Datetime(
        string="Last DNS check",
        readonly=True,
        copy=False,
    )
    dmarc_drift = fields.Boolean(
        string="DMARC drift",
        compute="_compute_dmarc_drift",
        store=True,
    )
    dmarc_drift_notified = fields.Boolean(
        string="Drift notified",
        default=False,
        copy=False,
    )
    dmarc_next_review_date = fields.Date(
        string="Next DMARC review",
        tracking=True,
        help="Date to review the policy (for example, move from "
             "quarantine to reject).",
    )
    dmarc_review_activity_created = fields.Boolean(
        string="Review activity created",
        default=False,
        copy=False,
    )

    # Meta
    notes = fields.Html(
        string="Notes",
    )
    active = fields.Boolean(
        string="Active",
        default=True,
    )

    _sql_constraints = [
        ("name_uniq", "UNIQUE(name)", "This domain name already exists!"),
    ]

    # ------------------------------------------------------------------
    # Computed fields
    # ------------------------------------------------------------------

    @api.depends("date_expiration")
    def _compute_days_until_expiration(self):
        today = fields.Date.today()
        for record in self:
            if record.date_expiration:
                record.days_until_expiration = (record.date_expiration - today).days
            else:
                record.days_until_expiration = 0

    @api.depends("ssl_expiry_date")
    def _compute_days_until_ssl_expiry(self):
        today = fields.Date.today()
        for record in self:
            if record.ssl_expiry_date:
                record.days_until_ssl_expiry = (record.ssl_expiry_date - today).days
            else:
                record.days_until_ssl_expiry = 0

    def _compute_service_count(self):
        for record in self:
            record.service_count = len(record.service_ids)

    @api.depends("dmarc_managed", "dmarc_policy", "dmarc_rua", "dmarc_pct")
    def _compute_dmarc_dns_value_expected(self):
        for record in self:
            if not (record.dmarc_managed and record.dmarc_policy and record.dmarc_rua):
                record.dmarc_dns_value_expected = False
                continue
            parts = [
                "v=DMARC1",
                f"p={record.dmarc_policy}",
                f"rua=mailto:{record.dmarc_rua.strip()}",
            ]
            pct = record.dmarc_pct if record.dmarc_pct else 100
            if pct != 100:
                parts.append(f"pct={pct}")
            record.dmarc_dns_value_expected = "; ".join(parts)

    @api.depends("dmarc_dns_value_expected", "dmarc_dns_value_observed", "dmarc_managed")
    def _compute_dmarc_drift(self):
        for record in self:
            if not record.dmarc_managed or not record.dmarc_dns_value_expected:
                record.dmarc_drift = False
                continue
            if not record.dmarc_dns_value_observed:
                record.dmarc_drift = False
                continue
            expected = self._dmarc_normalize(self._dmarc_parse_txt(record.dmarc_dns_value_expected))
            observed = self._dmarc_normalize(self._dmarc_parse_txt(record.dmarc_dns_value_observed))
            # Compare only the tags we manage; ignore extras like ruf, fo, adkim, aspf, sp
            managed_tags = ("v", "p", "rua", "pct")
            record.dmarc_drift = any(
                expected.get(t) != observed.get(t) for t in managed_tags
            )

    # ------------------------------------------------------------------
    # DMARC helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _dmarc_parse_txt(value):
        """Parse a DMARC TXT record into a normalized dict {tag: value}.

        Whitespace and casing are normalized so semantic equality survives
        formatting differences (e.g. 'p=quarantine;rua=...' vs
        'p=quarantine; rua=...').
        """
        if not value:
            return {}
        result = {}
        for chunk in value.split(";"):
            chunk = chunk.strip()
            if not chunk or "=" not in chunk:
                continue
            tag, _, val = chunk.partition("=")
            tag = tag.strip().lower()
            val = val.strip()
            if tag == "rua":
                # Normalize comma-separated mailto: list
                addrs = [a.strip().lower() for a in val.split(",") if a.strip()]
                val = ",".join(sorted(addrs))
            elif tag == "v":
                val = val.upper()
            else:
                val = val.lower()
            result[tag] = val
        return result

    @staticmethod
    def _dmarc_normalize(tags):
        """Fill in DMARC defaults so missing tags compare equal to explicit ones."""
        normalized = dict(tags)
        normalized.setdefault("pct", "100")
        return normalized

    @staticmethod
    def _dmarc_doh_lookup(domain_name):
        """Query the _dmarc TXT record via Cloudflare DoH.

        Returns the first TXT string that starts with 'v=DMARC1', or None.
        Raises on transport / parse errors so the caller can log & continue.
        """
        import requests  # noqa: PLC0415

        response = requests.get(
            DMARC_DOH_ENDPOINT,
            params={"name": f"_dmarc.{domain_name}", "type": "TXT"},
            headers={"Accept": "application/dns-json"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        # DoH JSON: status 0 = NOERROR, 3 = NXDOMAIN
        if data.get("Status") not in (0, 3):
            raise ValueError(f"DoH status {data.get('Status')}")
        for answer in data.get("Answer", []):
            # TXT records come quoted; strip outer quotes and join split strings
            raw = answer.get("data", "").strip()
            # Some servers return '"v=DMARC1; p=..."', others may split long records
            # into multiple quoted segments: '"v=DMARC1; p=..." "rua=..."'
            joined = ""
            in_quote = False
            buf = []
            for ch in raw:
                if ch == '"':
                    in_quote = not in_quote
                    continue
                if in_quote:
                    buf.append(ch)
                else:
                    if buf:
                        joined += "".join(buf)
                        buf = []
            if buf:
                joined += "".join(buf)
            if not joined:
                joined = raw.strip('"')
            if joined.lower().startswith("v=dmarc1"):
                return joined
        return None

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_view_services(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Services - %s", self.name),
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("domain_id", "=", self.id)],
            "context": {"default_domain_id": self.id},
        }

    def action_set_active(self):
        self.write({"state": "active"})

    def action_set_transferred(self):
        self.write({"state": "transferred"})

    def action_dmarc_check_now(self):
        """Force a DMARC DNS lookup for the selected domain(s) and post chatter."""
        for record in self:
            if not record.dmarc_managed:
                env = reader_env(record.env)
                record.message_post(
                    body=Markup("<p>%s</p>") % env._(
                        "DMARC: monitoring disabled for this domain, check skipped."),
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                )
                continue
            record._dmarc_run_check(post_chatter=True)
        return True

    def _dmarc_run_check(self, post_chatter=False):
        """Run a single DMARC DNS lookup for this record."""
        self.ensure_one()
        # Notes au fil lues par l'équipe d'hébergement (bouton ou travail
        # planifié) : langue de la société. Markup : un corps str est échappé.
        env = reader_env(self.env)
        try:
            observed = self._dmarc_doh_lookup(self.name)
        except Exception as exc:
            _logger.warning("DMARC : échec lookup pour %s — %s", self.name, exc)
            if post_chatter:
                self.message_post(
                    body=Markup("<p>%s</p>") % env._(
                        "DMARC: DNS check failed via %(provider)s — %(error)s",
                        provider=DMARC_PROVIDER_LABEL, error=exc,
                    ),
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                )
            return
        previous = self.dmarc_dns_value_observed or ""
        self.write({
            "dmarc_dns_value_observed": observed or "",
            "dmarc_last_dns_check": fields.Datetime.now(),
        })
        if post_chatter:
            if observed:
                self.message_post(
                    body=Markup(env._(
                        "<p>DMARC: TXT observed via %(provider)s — "
                        "<code>%(observed)s</code></p>"
                    )) % {"provider": DMARC_PROVIDER_LABEL, "observed": observed},
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                )
            else:
                self.message_post(
                    body=Markup(env._(
                        "<p>DMARC: no <code>_dmarc.%(domain)s</code> record "
                        "found via %(provider)s.</p>"
                    )) % {"domain": self.name, "provider": DMARC_PROVIDER_LABEL},
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                )
        # dmarc_drift recomputes automatically via @api.depends on the write above
        if not self.dmarc_drift and self.dmarc_drift_notified:
            self.dmarc_drift_notified = False
            self.message_post(
                body=Markup("<p>%s</p>") % env._(
                    "DMARC: alignment restored with the expected value."),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
        elif self.dmarc_drift and previous != (observed or ""):
            # Drift state changed — log it on the chatter even outside the cron
            self.message_post(
                body=Markup(env._(
                    "<p>DMARC: drift detected.<br/>"
                    "Expected: <code>%(expected)s</code><br/>"
                    "Observed: <code>%(observed)s</code></p>"
                )) % {
                    "expected": self.dmarc_dns_value_expected,
                    "observed": observed or env._("(no TXT)"),
                },
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------

    @api.model
    def _cron_check_domain_expirations(self):
        """Check domain + SSL expirations, create activities, auto-expire."""
        ICP = self.env["ir.config_parameter"].sudo()
        domain_warn_days = int(
            ICP.get_param("hosting.domain_expiration_warning_days", "60")
        )
        ssl_warn_days = int(
            ICP.get_param("hosting.ssl_expiration_warning_days", "30")
        )
        today = fields.Date.today()

        # --- Domain expiration activities ---
        domain_warning_date = today + timedelta(days=domain_warn_days)
        expiring_domains = self.search([
            ("state", "in", ("active", "expiring_soon")),
            ("date_expiration", "<=", domain_warning_date),
            ("date_expiration", ">=", today),
            ("domain_activity_created", "=", False),
        ])

        activity_type_domain = self.env.ref(
            "hosting_management.mail_activity_type_domain_expiration",
            raise_if_not_found=False,
        )

        for domain in expiring_domains:
            # Activité dans la langue de l'assigné, créée dans son env (objet de l'avis).
            assignee = domain.user_id or self.env.user
            aenv = user_env(self.env, assignee)
            aenv["mail.activity"].create({
                "res_model_id": self.env["ir.model"]._get_id("hosting.domain"),
                "res_id": domain.id,
                "activity_type_id": activity_type_domain.id if activity_type_domain else False,
                "summary": aenv._(
                    "Domain expires in %s days", domain.days_until_expiration),
                "note": aenv._(
                    "The domain %(domain)s for %(partner)s will expire on "
                    "%(date)s. Please proceed with the renewal.",
                    domain=domain.name, partner=domain.partner_id.name,
                    date=domain.date_expiration,
                ),
                "date_deadline": domain.date_expiration,
                "user_id": assignee.id,
            })
            domain.domain_activity_created = True
            if domain.state == "active":
                domain.state = "expiring_soon"

        # --- SSL expiration activities ---
        ssl_warning_date = today + timedelta(days=ssl_warn_days)
        ssl_expiring = self.search([
            ("state", "in", ("active", "expiring_soon")),
            ("ssl_type", "!=", "none"),
            ("ssl_expiry_date", "<=", ssl_warning_date),
            ("ssl_expiry_date", ">=", today),
            ("ssl_activity_created", "=", False),
        ])

        activity_type_ssl = self.env.ref(
            "hosting_management.mail_activity_type_ssl_expiration",
            raise_if_not_found=False,
        )

        for domain in ssl_expiring:
            assignee = domain.user_id or self.env.user
            aenv = user_env(self.env, assignee)
            aenv["mail.activity"].create({
                "res_model_id": self.env["ir.model"]._get_id("hosting.domain"),
                "res_id": domain.id,
                "activity_type_id": activity_type_ssl.id if activity_type_ssl else False,
                "summary": aenv._(
                    "SSL certificate expires in %s days", domain.days_until_ssl_expiry),
                "note": aenv._(
                    "The SSL certificate for %(domain)s (%(type)s) will expire on "
                    "%(date)s. Please proceed with the renewal.",
                    domain=domain.name, type=domain.ssl_type,
                    date=domain.ssl_expiry_date,
                ),
                "date_deadline": domain.ssl_expiry_date,
                "user_id": assignee.id,
            })
            domain.ssl_activity_created = True

        # --- Auto-expire past-due domains ---
        expired_domains = self.search([
            ("state", "in", ("active", "expiring_soon")),
            ("date_expiration", "<", today),
        ])
        if expired_domains:
            expired_domains.write({"state": "expired"})
            _logger.info(
                "Auto-expire : %d domaine(s) marque(s) comme expire(s)",
                len(expired_domains),
            )

        # --- ntfy push summary for newly flagged expirations ---
        if expiring_domains or ssl_expiring or expired_domains:
            Ntfy = self.env["hosting.ntfy"]
            # Alerte lue par l'équipe d'hébergement : langue de la société.
            env = reader_env(self.env)
            body_lines = []
            if expired_domains:
                body_lines.append(env._("Expired domains: %s", len(expired_domains)))
                for d in expired_domains[:5]:
                    body_lines.append(f"- {d.name} ({d.date_expiration})")
            if expiring_domains:
                body_lines.append("")
                body_lines.append(
                    env._("Domains close to expiry: %s", len(expiring_domains)))
                for d in expiring_domains[:5]:
                    body_lines.append(env._(
                        "- %(domain)s — %(days)s d (%(date)s)",
                        domain=d.name, days=d.days_until_expiration,
                        date=d.date_expiration,
                    ))
            if ssl_expiring:
                body_lines.append("")
                body_lines.append(
                    env._("SSL certificates close to expiry: %s", len(ssl_expiring)))
                for d in ssl_expiring[:5]:
                    body_lines.append(env._(
                        "- %(domain)s — %(days)s d (%(date)s)",
                        domain=d.name, days=d.days_until_ssl_expiry,
                        date=d.ssl_expiry_date,
                    ))
            has_expired = bool(expired_domains)
            Ntfy.send(
                title=(
                    env._("DOMAINS/SSL: expirations detected — domains already expired")
                    if has_expired
                    else env._("DOMAINS/SSL: expirations detected")
                ),
                body="\n".join(body_lines),
                priority="urgent" if has_expired else "high",
                tags="lock,calendar",
            )

    @api.model
    def _cron_dmarc_check(self):
        """Daily DMARC DNS drift check for all opted-in domains."""
        domains = self.search([
            ("dmarc_managed", "=", True),
            ("state", "in", ("active", "expiring_soon")),
            ("dmarc_dns_value_expected", "!=", False),
        ])
        if not domains:
            return

        new_drifts = self.env["hosting.domain"]
        new_reviews = self.env["hosting.domain"]
        today = fields.Date.today()
        activity_type_drift = self.env.ref(
            "hosting_management.mail_activity_type_dmarc_drift",
            raise_if_not_found=False,
        )
        activity_type_review = self.env.ref(
            "hosting_management.mail_activity_type_dmarc_review",
            raise_if_not_found=False,
        )

        # Notes au fil et alerte lues par l'équipe d'hébergement : langue de la société.
        env = reader_env(self.env)
        for domain in domains:
            previous_drift = domain.dmarc_drift
            domain._dmarc_run_check(post_chatter=False)
            assignee = domain.user_id or self.env.user
            aenv = user_env(self.env, assignee)
            if domain.dmarc_drift and not domain.dmarc_drift_notified:
                new_drifts |= domain
                domain.message_post(
                    body=Markup(env._(
                        "<p>DMARC: drift detected during the daily check.<br/>"
                        "Expected: <code>%(expected)s</code><br/>"
                        "Observed: <code>%(observed)s</code></p>"
                    )) % {
                        "expected": domain.dmarc_dns_value_expected,
                        "observed": domain.dmarc_dns_value_observed or env._("(no TXT)"),
                    },
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                )
                if activity_type_drift:
                    # Activité dans la langue de l'assigné, créée dans son env.
                    aenv["mail.activity"].create({
                        "res_model_id": self.env["ir.model"]._get_id("hosting.domain"),
                        "res_id": domain.id,
                        "activity_type_id": activity_type_drift.id,
                        "summary": aenv._("DMARC drift — %s", domain.name),
                        "note": Markup(aenv._(
                            "Expected: %(expected)s<br/>Observed: %(observed)s"
                        )) % {
                            "expected": domain.dmarc_dns_value_expected,
                            "observed": domain.dmarc_dns_value_observed
                            or aenv._("(no TXT)"),
                        },
                        "date_deadline": today,
                        "user_id": assignee.id,
                    })
                domain.dmarc_drift_notified = True
            elif not domain.dmarc_drift and previous_drift:
                # Drift was already cleared inside _dmarc_run_check — nothing extra
                pass

            # Review cadence
            if (
                domain.dmarc_next_review_date
                and domain.dmarc_next_review_date <= today
                and not domain.dmarc_review_activity_created
            ):
                new_reviews |= domain
                if activity_type_review:
                    aenv["mail.activity"].create({
                        "res_model_id": self.env["ir.model"]._get_id("hosting.domain"),
                        "res_id": domain.id,
                        "activity_type_id": activity_type_review.id,
                        "summary": aenv._("DMARC: policy review — %s", domain.name),
                        "note": aenv._(
                            "Current policy: %s. Review for hardening "
                            "(e.g. quarantine → reject).", domain.dmarc_policy,
                        ),
                        "date_deadline": domain.dmarc_next_review_date,
                        "user_id": assignee.id,
                    })
                domain.dmarc_review_activity_created = True

        if new_drifts or new_reviews:
            Ntfy = self.env["hosting.ntfy"]
            body_lines = []
            if new_drifts:
                body_lines.append(env._("DMARC drifts: %s", len(new_drifts)))
                for d in new_drifts[:5]:
                    body_lines.append(f"- {d.name} ({d.dmarc_policy})")
            if new_reviews:
                if body_lines:
                    body_lines.append("")
                body_lines.append(env._("DMARC reviews due: %s", len(new_reviews)))
                for d in new_reviews[:5]:
                    body_lines.append(f"- {d.name} — {d.dmarc_next_review_date}")
            Ntfy.send(
                title=(
                    env._("DMARC: drift detected")
                    if new_drifts
                    else env._("DMARC: reviews due")
                ),
                body="\n".join(body_lines),
                priority="high" if new_drifts else "default",
                tags="shield,envelope",
            )

    @api.model
    def _cron_sync_cloudflare_domains(self):
        """Synchroniser les domaines depuis l'API Cloudflare Registrar."""
        try:
            import requests  # noqa: PLC0415
        except ImportError:
            _logger.error("Le module 'requests' est requis pour la synchronisation Cloudflare.")
            return

        ICP = self.env["ir.config_parameter"].sudo()
        cf_email = ICP.get_param("hosting.cloudflare_email", "")
        cf_api_key = ICP.get_param("hosting.cloudflare_api_key", "")
        cf_account_id = ICP.get_param("hosting.cloudflare_account_id", "")

        if not cf_email or not cf_api_key or not cf_account_id:
            _logger.info(
                "Sync Cloudflare : identifiants non configures, synchronisation ignoree."
            )
            return

        headers = {
            "X-Auth-Email": cf_email,
            "X-Auth-Key": cf_api_key,
            "Content-Type": "application/json",
        }

        url = f"https://api.cloudflare.com/client/v4/accounts/{cf_account_id}/registrar/domains"

        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            _logger.error("Sync Cloudflare : erreur API - %s", exc)
            return

        data = response.json()
        if not data.get("success"):
            errors = data.get("errors", [])
            _logger.error("Sync Cloudflare : erreur API - %s", errors)
            return

        domains_data = data.get("result", [])
        updated = 0
        created = 0

        for cf_domain in domains_data:
            domain_name = cf_domain.get("name", "")
            if not domain_name:
                continue
            try:
                result = self._sync_single_cloudflare_domain(cf_domain, domain_name)
                if result == "updated":
                    updated += 1
                elif result == "created":
                    created += 1
            except Exception:
                _logger.exception(
                    "Sync Cloudflare : erreur lors du traitement de %s", domain_name
                )

        ICP.set_param(
            "hosting.last_cloudflare_sync",
            fields.Datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        _logger.info(
            "Sync Cloudflare terminee : %d domaine(s) mis a jour, %d cree(s) sur %d retourne(s).",
            updated,
            created,
            len(domains_data),
        )

    @api.model
    def _sync_single_cloudflare_domain(self, cf_data, domain_name):
        """Synchroniser un seul domaine depuis les donnees Cloudflare.

        Returns:
            str: 'updated', 'created', or 'skipped'
        """
        # Parse expiration date
        expires_at = cf_data.get("expires_at", "")
        expiration_date = False
        if expires_at:
            try:
                expiration_date = date_type.fromisoformat(expires_at[:10])
            except (ValueError, TypeError):
                _logger.warning(
                    "Sync Cloudflare : date d'expiration invalide pour %s : %s",
                    domain_name,
                    expires_at,
                )

        auto_renew = cf_data.get("auto_renew", False)
        registrar = "Cloudflare"

        # Search existing domain
        existing = self.search([("name", "=ilike", domain_name)], limit=1)

        # Notes au fil d'un travail planifié : langue de la société.
        env = reader_env(self.env)
        if existing:
            vals = {}
            if expiration_date and existing.date_expiration != expiration_date:
                vals["date_expiration"] = expiration_date
            if existing.auto_renew != auto_renew:
                vals["auto_renew"] = auto_renew
            if existing.registrar != registrar:
                vals["registrar"] = registrar

            if vals:
                existing.write(vals)
                # Build chatter message
                changes = []
                if "date_expiration" in vals:
                    changes.append(env._("Expiration: %s", expiration_date))
                if "auto_renew" in vals:
                    changes.append(
                        env._("Auto-renewal: Yes") if auto_renew
                        else env._("Auto-renewal: No")
                    )
                if "registrar" in vals:
                    changes.append(env._("Registrar: %s", registrar))
                existing.message_post(
                    body=Markup("<p>%s</p>") % env._(
                        "Cloudflare sync — %s", ", ".join(changes)),
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                )
                return "updated"
            return "skipped"

        # Domain not found — create it
        new_domain = self.create({
            "name": domain_name,
            "partner_id": self.env.company.partner_id.id,
            "registrar": registrar,
            "date_expiration": expiration_date,
            "auto_renew": auto_renew,
            "state": "active",
        })
        new_domain.message_post(
            body=Markup("<p>%s</p>") % env._(
                "Domain created automatically by the Cloudflare sync."),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        return "created"
