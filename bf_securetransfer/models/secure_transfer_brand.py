"""Brands for the public transfer pages, resolved by the request Host.

One record per skin: the free default (« Propulsé par ... ») plus one paid
record per customer domain. Brands are product data, NOT res.company rows —
The host tenant is often single-company and paid skins are just customer records.

By default a brand aligns with the house branding (bf_branding /
bluefox_branding + bf_onboarding_base) when present: unset colours, logo,
favicon and the e-mail tagline/footer fall back to the company's
report_brand_* / favicon / brand_email_* fields — all read via getattr so the
module stays installable without those modules.

Each brand can also carry a sender allowlist (anti-piggyback): a branded
instance may restrict who is allowed to send from it (see _sender_allowed).
"""
import logging
import re
import unicodedata

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.http import request
from odoo.tools import config, email_normalize

from . import s3

_logger = logging.getLogger(__name__)

# Retention choices offered on the public form, capped per brand.
EXPIRY_CHOICES = (1, 7, 30)

# What a Host header may contain before it is used to look up a brand.
# Deliberately narrow: letters, digits, dot, hyphen. Notably excludes the SQL
# LIKE metacharacters % and _, which "=ilike" would otherwise honour.
_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")

# What « Powered by » names when a brand leaves its own name empty: the
# product, never the company served.
POWERED_BY_DEFAULT = "Symbifox"


class SecureTransferBrand(models.Model):
    _name = "secure.transfer.brand"
    _description = "Secure transfer: brand"
    _order = "sequence, id"

    name = fields.Char(string="Name", required=True)
    sequence = fields.Integer(string="Sequence", default=10)
    active = fields.Boolean(string="Active", default=True)
    is_default = fields.Boolean(
        string="Default brand",
        help="Brand served when the incoming domain matches no brand. "
             "Only one active brand can be the default.",
    )
    domain = fields.Char(
        string="Domain",
        index=True,
        help="Bare host (no scheme or port) on which this brand is "
             "served, e.g. \"secret.example.com\". Empty for the "
             "default brand.",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Customer",
        help="Client billed for this brand (anchor for recurring billing).",
    )
    tier = fields.Selection(
        selection=[("free", "Free"), ("paid", "Paid")],
        string="Tier",
        default="free",
        required=True,
    )

    # -- billing (paid tier). The recurring invoice is issued by Blue Fox
    #    (bf_subscription lives on the BF tenant); these fields track the
    #    arrangement on the brand record that serves the product.
    billing_active = fields.Boolean(
        string="Active subscription",
        help="The paid brand is covered by a recurring subscription.",
    )
    billing_ref = fields.Char(
        string="Subscription ref.",
        help="Reference of the bf_subscription subscription that bills "
             "this client.",
    )
    price_year = fields.Float(
        string="Annual price ($)",
        help="Annual recurring price of the paid tier (benchmark: $350-500).",
    )

    # -- visuals (all optional → fallback to company report_brand_*)
    logo = fields.Binary(string="Logo", attachment=True)
    favicon = fields.Binary(string="Favicon", attachment=True)
    color_primary = fields.Char(
        string="Primary color",
        help="Hex, e.g. #1689c3. Empty = company's brand color.",
    )
    color_dark = fields.Char(
        string="Dark color",
        help="Hex, e.g. #0f4960. Empty = company's dark color.",
    )
    powered_by = fields.Boolean(
        string="Show \"Powered by\"",
        default=True,
        help="\"Powered by\" footer on the public page and emails. "
             "Unchecked for paid white-label brands.",
    )
    powered_by_name = fields.Char(
        string="Name credited",
        default=POWERED_BY_DEFAULT,
        help="Name after \"Powered by\" on the public page, the emails and the "
             "access certificate. Empty = Symbifox. It never reads the company's "
             "name: the company served is not the one credited.",
    )
    email_from = fields.Char(
        string="Email sender",
        help="Sending address for this brand's emails. Empty = server's "
             "default sender.",
    )

    # -- sender allowlist (anti-piggyback): who is allowed to SEND from this
    #    brand/instance. Empty = open to anyone (the free public tier). A paid
    #    branded instance (secrets.client.com) sets this so only the client's
    #    own people can use it, never a stranger piggybacking on their skin.
    sender_allowlist = fields.Text(
        string="Authorized senders",
        help="One entry per line (or comma-separated). Full address "
             "(jean@client.com) or whole domain (@client.com or "
             "client.com). EMPTY = open to everyone (public free tier). "
             "Filled = only these senders can send from this brand; this "
             "prevents a third party from using an instance that isn't "
             "theirs.",
    )
    # -- recipient allowlist (anti-piggyback, destination side): restrict WHO
    #    can be a recipient. A client instance set to « @client.com » can only
    #    send to the client's own people — it can't be abused to relay files to
    #    arbitrary external addresses.
    recipient_allowlist = fields.Text(
        string="Authorized recipients",
        help="Same format as senders (full address or domain). EMPTY = no "
             "restriction. Filled = files can ONLY be sent to these "
             "addresses/domains; this locks the instance to its own use.",
    )

    # -- slug-addressed public page, served at /to/<slug>. The slug ALONE
    #    publishes the page; fixed_recipient is an independent, optional lock:
    #      slug only                  -> open page, free sender and recipients
    #      slug + fixed_recipient     -> drop page ("Dropbox"-style): the
    #         recipient is FORCED server-side (create + finalize), so nobody can
    #         redirect a drop to another address.
    #    Empty slug = a normal Host-served brand, reachable at /secrets only.
    slug = fields.Char(
        string="Page identifier (slug)",
        index=True,
        help="Publishes this brand at /to/<slug> (e.g. \"jane\" → "
             "/to/jane). Lowercase letters, digits and hyphens. Empty "
             "= brand served by domain only.",
    )
    fixed_recipient = fields.Char(
        string="Single recipient (drop page)",
        help="Optional. When filled, the page becomes a drop page: all "
             "sends go ONLY to this address, regardless of what the "
             "sender enters, and the recipient field is hidden. Empty = "
             "open page, the sender chooses their recipients.",
    )
    fixed_recipient_name = fields.Char(
        string="Recipient's display name",
        help="Name shown on the drop page (\"Secure send to …\"). Empty = "
             "the address is displayed.",
    )
    # Employee behind an auto-provisioned personal drop page (see res.users).
    # Anchors lifecycle sync (rename / e-mail change / archive) and prevents
    # duplicate pages. Empty on manually-created or company-general brands.
    owner_user_id = fields.Many2one(
        "res.users",
        string="Employee (auto page)",
        ondelete="set null",
        index=True,
        copy=False,
        help="Filled when this drop page was created automatically for an "
             "employee. Used for resynchronization and archiving; "
             "prevents duplicates.",
    )
    # Native, Shlink-free public URL of the slug-addressed page. Computed,
    # never stored: it tracks the slug and the resolved share base (brand
    # domain → public base URL → web.base.url) so the admin always sees the
    # live link.
    page_url = fields.Char(
        string="Public page URL",
        compute="_compute_page_url",
        help="Native public /to/<slug> address for this brand (no "
             "external shortener). Empty until a slug is set.",
    )

    # -- limits (0 = default from settings for the tier)
    max_transfer_mb = fields.Integer(
        string="Max. transfer size (MB)",
        default=0,
        help="0 = tier default value (settings).",
    )
    max_files = fields.Integer(
        string="Max. number of files",
        default=0,
        help="0 = default value (settings).",
    )
    max_retention_days = fields.Integer(
        string="Max. retention (days)",
        default=0,
        help="0 = tier default value (settings).",
    )
    allow_password = fields.Boolean(
        string="Password allowed", default=True,
    )
    allow_recipient_otp = fields.Boolean(
        string="Recipient code offered", default=True,
        help="Lets the sender require, from the public page, a one-time "
             "code from the recipient before the content is shown. No "
             "effect when the instance setting already requires it for "
             "every send: the box is then shown ticked and locked.",
    )

    # -- Audience ouverte (salle de données) ---------------------------------
    # Éteint par défaut, et sur les DEUX plans : la marque doit l'offrir, et
    # l'expéditeur doit le choisir. Un lien d'audience ouverte accepte une
    # adresse que personne n'a validée en amont — c'est un pouvoir qu'on
    # accorde, pas un défaut qu'on subit.
    allow_open_audience = fields.Boolean(
        string="Open audience offered", default=False,
        help="Allows sends whose link names no recipient: the visitor "
             "enters their address and confirms it with a one-time code. "
             "Useful for a data room or for sharing with a group whose "
             "addresses are not all known.",
    )
    audience_domains = fields.Text(
        string="Allowed domains (open audience)",
        help="Allowlist applied to self-declared addresses: full address, "
             "or domain (\"@client.com\"), one per line or "
             "comma-separated. Empty = any address is allowed, within the "
             "visitor limit.",
    )
    audience_max_default = fields.Integer(
        string="Max. visitors (default)", default=50,
        help="Maximum number of distinct addresses allowed on an open "
             "audience transfer, when the send does not set another. 0 = "
             "unlimited (not recommended: the cap is what keeps a leaked "
             "link from becoming an email relay).",
    )
    allow_audience_sms = fields.Boolean(
        string="Code by SMS for open audiences", default=False,
        help="Lets an open audience visitor request their code on their "
             "mobile. Off by default: each SMS has a real cost and the "
             "number is supplied by the visitor themselves.",
    )

    watermark_downloads = fields.Boolean(
        string="Watermark on download",
        default=False,
        help="Stamps an identifying watermark (recipient, timestamp, "
             "brand) on every PDF downloaded through this brand. Odoo "
             "then serves the bytes itself instead of redirecting to "
             "storage. No effect on non-PDF files.",
    )

    # ⚠ Ces deux champs ont longtemps porté « (Phase 2) » dans leur libellé,
    # alors que les deux chemins sont implantés (`_notify_download()`,
    # `_register_download()`) et couverts par des essais. L'étiquette a fait
    # croire à une fonction absente — et un client est resté sans avis de
    # téléchargement parce que personne n'avait pensé à allumer le champ.
    # Les vraies conditions pour que l'avis parte sont le gabarit
    # `mail_template_download_notice` et le `from_filter` du serveur sortant.
    notify_on_download = fields.Boolean(
        string="Notify on download", default=False,
        help="Emails the sender the first time a recipient downloads. "
             "Copied onto each new transfer at creation and read-only "
             "there, so this is the only place it can be turned on.",
    )
    allow_burn = fields.Boolean(
        string="Offer destroy after reading", default=False,
    )

    # ----------------------------------------------------------- rappel des absents
    # Opt-in délibéré (défaut faux) : faire partir des courriels à des clients
    # sans que personne l'ait demandé est le genre de défaut qu'on ne se fait
    # pas pardonner. Même forme que `bf_document_reminder_*`, que ce socle
    # côtoie déjà sur deux locataires.
    st_absentee_reminder_enabled = fields.Boolean(
        string="Automatically remind recipients who never came",
        default=False,
        help="Sends the share link again to a named recipient who has not "
             "even asked for their code, following the delay and the "
             "maximum below. A recipient who downloaded, or who is merely "
             "stuck on their code, is never reminded.",
    )
    st_absentee_reminder_delay_hours = fields.Integer(
        string="Delay before reminding (h)",
        default=48,
        help="Counted from the send, or from the last time the link was "
             "sent again — manually or automatically.",
    )
    st_absentee_reminder_max = fields.Integer(
        string="Maximum number of reminders",
        default=1,
        help="Budget of AUTOMATIC reminders per recipient. The operator's "
             "manual re-sends do not use it up.",
    )

    # ⚠ L'interrupteur fait partie des dépendances : sans lui, poser un délai
    # et un maximum à zéro pendant que le rappel est éteint, puis l'allumer,
    # ne revalidait rien.
    @api.constrains("st_absentee_reminder_enabled",
                    "st_absentee_reminder_delay_hours",
                    "st_absentee_reminder_max")
    def _check_st_absentee_reminder(self):
        for rec in self:
            if not rec.st_absentee_reminder_enabled:
                continue
            if rec.st_absentee_reminder_delay_hours < 1:
                raise ValidationError(_(
                    "The delay before reminding must be at least one hour, "
                    "otherwise the scheduled job reminds at every run."))
            if rec.st_absentee_reminder_max < 1:
                raise ValidationError(_(
                    "A maximum of zero reminders amounts to turning "
                    "automatic reminders off."))

    _sql_constraints = [
        ("domain_uniq", "unique(domain)",
         "This domain is already associated with another brand."),
        ("slug_uniq", "unique(slug)",
         "This page identifier (slug) is already used by another brand."),
    ]

    _SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

    @api.constrains("slug")
    def _check_slug_format(self):
        for rec in self.filtered("slug"):
            if not self._SLUG_RE.match(rec.slug):
                raise ValidationError(_(
                    "The page identifier (slug) \"%s\" is invalid: "
                    "lowercase letters, digits and hyphens only, no "
                    "spaces.",
                    rec.slug))

    @api.constrains("fixed_recipient")
    def _check_fixed_recipient(self):
        for rec in self.filtered("fixed_recipient"):
            if not email_normalize(rec.fixed_recipient):
                raise ValidationError(_(
                    "The single recipient \"%s\" is not a valid email address.", rec.fixed_recipient))

    @api.constrains("is_default", "active")
    def _check_single_default(self):
        for rec in self.filtered(lambda b: b.is_default and b.active):
            other = self.search_count([
                ("is_default", "=", True),
                ("active", "=", True),
                ("id", "!=", rec.id),
            ])
            if other:
                raise ValidationError(_(
                    "Only one active brand can be the default brand."
                ))

    # ------------------------------------------------------------------ slug + url
    @api.model
    def _st_slugify(self, text):
        """ASCII-fold + kebab-case a free-text label into a slug candidate
        matching _SLUG_RE (lowercase, digits, hyphens; leading/trailing and
        repeated hyphens stripped). Returns '' when nothing usable remains."""
        s = unicodedata.normalize("NFKD", text or "")
        s = s.encode("ascii", "ignore").decode("ascii").lower()
        s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
        return re.sub(r"-{2,}", "-", s)

    @api.model
    def _st_unique_slug(self, seed):
        """A free slug derived from `seed`: the slugified base, then base-2,
        base-3… Checks archived brands too (slug is globally unique)."""
        base = self._st_slugify(seed) or "page"
        Brand = self.with_context(active_test=False).sudo()
        candidate, n = base, 1
        while Brand.search_count([("slug", "=", candidate)]):
            n += 1
            candidate = "%s-%s" % (base, n)
        return candidate

    @api.model_create_multi
    def create(self, vals_list):
        # A drop page (fixed_recipient set) with no slug auto-gets one from its
        # name — creating a page never requires typing a slug by hand.
        for vals in vals_list:
            if vals.get("fixed_recipient") and not vals.get("slug"):
                seed = (vals.get("name") or vals.get("fixed_recipient_name")
                        or (vals.get("fixed_recipient") or "").split("@")[0])
                vals["slug"] = self._st_unique_slug(seed)
        brands = super().create(vals_list)
        if any(vals.get("max_retention_days") for vals in vals_list):
            self.env["secure.transfer"]._trigger_lifecycle_sync()
        return brands

    def write(self, vals):
        res = super().write(vals)
        # 🔴 La règle de cycle de vie du seau est calculée sur la plus
        # longue rétention qu'une marque accorde. La relever sans reposer la
        # règle laissait le fournisseur effacer des envois encore promis.
        if vals.get("max_retention_days"):
            self.env["secure.transfer"]._trigger_lifecycle_sync()
        return res

    @api.depends("slug", "domain")
    def _compute_page_url(self):
        for rec in self:
            if rec.slug:
                rec.page_url = "%s/to/%s" % (rec._share_base_url(), rec.slug)
            else:
                rec.page_url = False

    # ------------------------------------------------------------------ host resolution
    @api.model
    def _resolve_for_host(self, host):
        """Find the active brand whose domain matches a request Host (port
        stripped, case-insensitive). Returns a recordset (empty if no match).

        Mirrors bf.policy.org._resolve_for_host — the proven multi-tenant
        Host router."""
        if not host:
            return self.browse()
        # X-Forwarded-Host can be a comma chain; keep the first, drop any port.
        h = host.split(",")[0].split(":")[0].strip().lower()
        if not h:
            return self.browse()
        # A Host header is entirely attacker-controlled, and "=ilike" passes
        # its value through as a LIKE PATTERN — it does NOT escape % and _.
        # An unescaped "Host: %" therefore matched the first brand holding a
        # domain, handing over its skin, its limits and its email_from.
        # Reject anything that is not a plain hostname rather than escaping:
        # no legitimate domain contains a LIKE metacharacter, so this is a
        # tighter guarantee than quoting them.
        if not _HOSTNAME_RE.match(h):
            return self.browse()
        return self.sudo().search([("domain", "=ilike", h)], limit=1)

    @api.model
    def _resolve_for_slug(self, slug):
        """Find the active brand published at /to/<slug>. Returns a recordset
        (empty if no match). A slug is enough to publish the page; whether the
        page is a locked drop page or an open one is decided by
        fixed_recipient, not here."""
        s = (slug or "").strip().lower()
        if not s:
            return self.browse()
        return self.sudo().search([
            ("slug", "=", s),
            ("active", "=", True),
        ], limit=1)

    def _drop_recipient(self):
        """Normalized forced recipient for a drop-page brand, else ''."""
        self.ensure_one()
        return email_normalize(self.fixed_recipient or "") or ""

    def _drop_recipient_label(self):
        """What to show on the drop page as the destination: the configured
        display name, else the address itself."""
        self.ensure_one()
        return (self.fixed_recipient_name or "").strip() \
            or self._drop_recipient()

    @api.model
    def _from_request(self):
        """The brand to serve for the current HTTP request. Never empty:
        falls back to the default brand, then to any active brand."""
        host = ""
        if request:
            # ⚠️ Avec `proxy_mode`, ProxyFix a déjà versé X-Forwarded-Host
            # dans `host` en gardant le DERNIER saut, celui de notre
            # mandataire. L'en-tête brut, dont `_resolve_for_host` garde le
            # PREMIER élément, laissait le client choisir la marque (et ses
            # plafonds) en se plaçant devant. Sans `proxy_mode`
            # (dev), le dernier saut de l'en-tête tient lieu de ProxyFix.
            httprequest = request.httprequest
            if config.get("proxy_mode"):
                host = httprequest.host or ""
            else:
                forwarded = httprequest.headers.get("X-Forwarded-Host") or ""
                host = forwarded.split(",")[-1].strip() or httprequest.host or ""
        brand = self._resolve_for_host(host)
        if not brand:
            brand = self.sudo().search([("is_default", "=", True)], limit=1)
        if not brand:
            brand = self.sudo().search([], order="sequence, id", limit=1)
        return brand

    # ------------------------------------------------------------------ effective config
    def _effective_limits(self):
        """Limits applied to this brand: explicit values, else the tier
        defaults from ir.config_parameter."""
        self.ensure_one()
        # Local import: the brand module is loaded BEFORE secure_transfer (see
        # models/__init__), and the ceiling belongs where it is enforced.
        from .secure_transfer import MAX_DOWNLOAD_BUDGET
        env = self.env
        paid = self.tier == "paid"
        default_mb = s3._int_param(
            env,
            "default_paid_max_transfer_mb" if paid else "default_free_max_transfer_mb",
            20480 if paid else 2048,
        )
        default_days = s3._int_param(
            env,
            "default_paid_max_retention_days" if paid else "default_free_max_retention_days",
            90 if paid else 7,
        )
        default_files = s3._int_param(env, "default_max_files", 25)
        max_mb = self.max_transfer_mb or default_mb
        max_days = self.max_retention_days or default_days
        choices = [d for d in EXPIRY_CHOICES if d <= max_days] or [max_days]
        return {
            "max_bytes": int(max_mb) * 1024 * 1024,
            "max_files": int(self.max_files or default_files),
            "max_retention_days": int(max_days),
            "allow_password": bool(self.allow_password),
            "allow_burn": bool(self.allow_burn),
            "allow_recipient_otp": bool(self.allow_recipient_otp),
            # The instance-wide setting already holds EVERY transfer behind a
            # recipient code: the public form then shows the option ticked and
            # locked rather than letting a sender believe they can opt out.
            "recipient_otp_forced": bool(
                env["secure.transfer"].sudo()._needs_recipient_otp_param()),
            "max_download_budget": MAX_DOWNLOAD_BUDGET,
            "expiry_choices": choices,
            # Audience ouverte : ce que la marque offre. Le formulaire public
            # ne s'en sert pas (le mode s'ouvre depuis le backend seulement,
            # cf. secure.transfer.audience_mode) mais l'assistant d'envoi et
            # les contrôles serveur lisent la même source.
            "allow_open_audience": bool(self.allow_open_audience),
            "allow_audience_sms": bool(self.allow_audience_sms),
            "audience_max_default": int(self.audience_max_default or 0),
        }

    # ------------------------------------------------------------------ audience
    def _audience_allowlist(self):
        """Liste blanche des adresses auto-déclarées pour cette marque.

        Repli sur la liste des destinataires : une instance qui restreint déjà
        à qui elle peut écrire n'a aucune raison d'être plus ouverte quand
        l'adresse vient du visiteur. L'inverse serait un contournement de la
        politique anti-rebond par le simple choix du mode."""
        self.ensure_one()
        return (self.audience_domains or "").strip() \
            or self._effective_recipient_allowlist()

    def _audience_allowed(self, email):
        """Whether ``email`` may join an open audience served by this brand."""
        self.ensure_one()
        return self._email_matches_list(email, self._audience_allowlist())

    def _powered_by_label(self):
        """The name after « Powered by », the same on every surface. Never the
        company's: appointment_brand_name also names the public booking pages,
        so it cannot carry the product's name."""
        self.ensure_one()
        return (self.powered_by_name or "").strip() or POWERED_BY_DEFAULT

    def _visuals(self):
        """Resolved visual identity, shared by the public QWeb pages and the
        mail templates so both can never drift. Image URLs are RELATIVE, so a
        page always serves its own images from its own origin; the mail
        templates, which need absolute URLs, prefix them with
        `_share_base_url()` themselves.

        By default a brand aligns with the house branding (bf_branding /
        bluefox_branding) when present: unset colours/logo/favicon and the
        e-mail tagline/footer all fall back to the company's report_brand_* +
        favicon + brand_email_* fields. Everything is read with getattr so the
        module stays installable without those modules."""
        self.ensure_one()
        company = self.company_id

        def cfield(name):
            return getattr(company, name, False)

        # Logo: brand binary (served same-origin via /web/image) → company
        # report_brand_logo (same-origin) → appointment_brand_logo_url (the
        # curated PUBLIC logo, possibly on another host — its host is exposed
        # as logo_host so the page CSP can whitelist it).
        logo_url = (
            self._public_image_url(self._name, self.id, "logo")
            or self._public_image_url("res.company", company.id, "report_brand_logo")
            or (cfield("appointment_brand_logo_url") or "")
        )
        logo_host = ""
        if logo_url.startswith(("http://", "https://")):
            try:
                from urllib.parse import urlparse
                p = urlparse(logo_url)
                logo_host = "%s://%s" % (p.scheme, p.netloc)
            except Exception:  # noqa: BLE001
                logo_host = ""
        favicon_url = (
            self._public_image_url(self._name, self.id, "favicon")
            or self._public_image_url("res.company", company.id, "favicon")
            or logo_url
            or ""
        )
        return {
            "name": self.name or company.name,
            # PUBLIC branding first (appointment_brand_*, the same fields the
            # bf_appointment public pages use — e.g. a client's navy #1186c0),
            # then the backend report_brand_* (bf_onboarding_base), then a
            # hardcoded default. getattr keeps every source dependency-free.
            "primary": self.color_primary
            or cfield("appointment_brand_primary")
            or cfield("report_brand_primary")
            or "#29ABE2",
            "dark": self.color_dark
            or cfield("appointment_brand_dark")
            or cfield("report_brand_dark")
            or "#2E3132",
            "logo_url": logo_url,
            "logo_host": logo_host,
            "favicon_url": favicon_url,
            "powered_by": bool(self.powered_by),
            # One source for the page, the four emails and the certificate.
            # It used to read the company SERVED (appointment_brand_name, then
            # res.company.name): a client credited itself, or a third party's
            # domain name ended up credited on the page.
            "powered_by_name": self._powered_by_label(),
            # Public tagline first, then the e-mail tagline; footer for e-mails.
            "tagline": cfield("appointment_brand_tagline")
            or cfield("brand_email_tagline") or "",
            "footer_html": cfield("brand_email_footer_html") or "",
        }

    # ------------------------------------------------------------------ onboarding
    def action_apply_cors(self):
        """Re-apply the bucket CORS so this brand's custom domain is covered
        (union of every active brand domain + params). Run after adding or
        changing a paid domain. Idempotent."""
        self.ensure_one()
        merged = s3.apply_cors(self.env)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("CORS applied"),
                "message": _("Allowed origins: %s") % ", ".join(merged),
                "type": "success",
                "sticky": False,
            },
        }

    # ------------------------------------------------------------------ allowlists
    @staticmethod
    def _email_matches_list(email, raw):
        """True when ``email`` matches any entry of the ``raw`` allowlist text.
        Empty list = open (matches everything). Entries: full address, or a
        domain as ``@client.com`` / ``client.com``. One per line or comma-sep."""
        raw = (raw or "").strip()
        if not raw:
            return True
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            return False
        domain = email.rsplit("@", 1)[1]
        for token in raw.replace(",", "\n").split("\n"):
            entry = token.strip().lower()
            if not entry:
                continue
            if entry.startswith("@"):
                if domain == entry[1:]:
                    return True
            elif "@" in entry:
                if email == entry:
                    return True
            elif domain == entry:  # bare domain
                return True
        return False

    def _effective_sender_allowlist(self):
        """This brand's sender allowlist, or the tenant-wide default when the
        brand leaves it empty (so the operator can set one global policy in
        Settings and let brands override)."""
        self.ensure_one()
        return (self.sender_allowlist or "").strip() \
            or s3.param(self.env, "default_sender_allowlist", "") or ""

    def _effective_recipient_allowlist(self):
        self.ensure_one()
        return (self.recipient_allowlist or "").strip() \
            or s3.param(self.env, "default_recipient_allowlist", "") or ""

    def _sender_allowed(self, email):
        """Whether ``email`` may SEND from this brand (anti-piggyback).
        Brand list, else the tenant-wide default (Settings)."""
        self.ensure_one()
        return self._email_matches_list(email, self._effective_sender_allowlist())

    def _recipient_allowed(self, email):
        """Whether ``email`` may be a RECIPIENT of this brand (anti-piggyback,
        destination side). Brand list, else the tenant-wide default."""
        self.ensure_one()
        return self._email_matches_list(email, self._effective_recipient_allowlist())

    def _public_image_url(self, model, res_id, fname):
        """Relative /web/image URL for a binary field, served to anonymous
        visitors — same origin as whatever page asks for it. The backing attachment is flipped public=True (these are
        public-facing logos by definition — same trick as website images).
        Guards on field existence so the res.company fallback works without
        bf_onboarding_base."""
        self.ensure_one()
        if fname not in self.env[model]._fields:
            return False
        record = self.env[model].sudo().browse(res_id)
        if not record.exists() or not record[fname]:
            return False
        att = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", model),
            ("res_field", "=", fname),
            ("res_id", "=", res_id),
        ], limit=1)
        if not att:
            return False
        if not att.public:
            att.write({"public": True})
        # Adresse RELATIVE, donc toujours la même origine que la page qui la
        # demande. Bâtie sur `_share_base_url()`, elle pointait le domaine de la
        # marque : une page servie ailleurs (environnement d'essai, `public_base_url` différent,
        # domaine pas encore branché) affichait une image brisée. Les courriels
        # ont besoin d'absolu et le font déjà eux-mêmes, en préfixant par
        # `base` = `_share_base_url()` quand l'adresse ne commence pas par http.
        return "/web/image/%s" % att.id

    def _share_base_url(self):
        """Base URL of the public pages for this brand, no trailing slash.
        The brand domain wins; then bf_securetransfer.public_base_url; then
        web.base.url as a last resort. NEVER web.base.url first: on some hosts it
        is frozen to the backend domain (projets.example.com) and must
        not leak into share links."""
        self.ensure_one()
        if self.domain:
            return "https://%s" % self.domain.strip().strip("/").lower()
        base = (s3.param(self.env, "public_base_url", "") or "").strip()
        if not base:
            base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        base = (base or "").rstrip("/")
        if base and not base.startswith(("http://", "https://")):
            base = "https://" + base
        return base
