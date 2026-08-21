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
from odoo.tools import email_normalize

from . import s3

_logger = logging.getLogger(__name__)

# Retention choices offered on the public form, capped per brand.
EXPIRY_CHOICES = (1, 7, 30)

# What a Host header may contain before it is used to look up a brand.
# Deliberately narrow: letters, digits, dot, hyphen. Notably excludes the SQL
# LIKE metacharacters % and _, which "=ilike" would otherwise honour.
_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")


class SecureTransferBrand(models.Model):
    _name = "secure.transfer.brand"
    _description = "Transfert sécurisé — Marque"
    _order = "sequence, id"

    name = fields.Char(string="Nom", required=True)
    sequence = fields.Integer(string="Séquence", default=10)
    active = fields.Boolean(string="Actif", default=True)
    is_default = fields.Boolean(
        string="Marque par défaut",
        help="Marque servie quand le domaine d'arrivée ne correspond à "
             "aucune marque. Une seule marque active peut être par défaut.",
    )
    domain = fields.Char(
        string="Domaine",
        index=True,
        help="Hôte nu (sans schéma ni port) sur lequel cette marque est "
             "servie, p. ex. « secret.example.com ». Vide pour la "
             "marque par défaut.",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Société",
        required=True,
        default=lambda self: self.env.company,
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Client",
        help="Client facturé pour cette marque (ancrage de la facturation "
             "récurrente).",
    )
    tier = fields.Selection(
        selection=[("free", "Gratuit"), ("paid", "Payant")],
        string="Palier",
        default="free",
        required=True,
    )

    # -- billing (paid tier). The recurring invoice is issued by Blue Fox
    #    (bf_subscription lives on the BF tenant); these fields track the
    #    arrangement on the brand record that serves the product.
    billing_active = fields.Boolean(
        string="Abonnement actif",
        help="La marque payante est couverte par un abonnement récurrent.",
    )
    billing_ref = fields.Char(
        string="Réf. abonnement (BF)",
        help="Référence de l'abonnement bf_subscription émis par Blue Fox "
             "pour ce client.",
    )
    price_year = fields.Float(
        string="Prix annuel ($)",
        help="Prix récurrent annuel du palier payant (repère : 350-500 $).",
    )

    # -- visuals (all optional → fallback to company report_brand_*)
    logo = fields.Binary(string="Logo", attachment=True)
    favicon = fields.Binary(string="Favicon", attachment=True)
    color_primary = fields.Char(
        string="Couleur principale",
        help="Hex, p. ex. #1689c3. Vide = couleur de marque de la société.",
    )
    color_dark = fields.Char(
        string="Couleur foncée",
        help="Hex, p. ex. #0f4960. Vide = couleur foncée de la société.",
    )
    powered_by = fields.Boolean(
        string="Afficher « Propulsé par »",
        default=True,
        help="Pied de page « Propulsé par » sur la page publique et les "
             "courriels. Décoché pour les marques payantes en marque blanche.",
    )
    email_from = fields.Char(
        string="Expéditeur des courriels",
        help="Adresse d'expédition des courriels de cette marque. Vide = "
             "expéditeur par défaut du serveur.",
    )

    # -- sender allowlist (anti-piggyback): who is allowed to SEND from this
    #    brand/instance. Empty = open to anyone (the free public tier). A paid
    #    branded instance (secrets.client.com) sets this so only the client's
    #    own people can use it, never a stranger piggybacking on their skin.
    sender_allowlist = fields.Text(
        string="Expéditeurs autorisés",
        help="Une entrée par ligne (ou séparées par des virgules). Adresse "
             "complète (jean@client.com) ou domaine entier (@client.com ou "
             "client.com). VIDE = ouvert à tous (palier gratuit public). "
             "Renseigné = seuls ces expéditeurs peuvent envoyer depuis cette "
             "marque — empêche qu'un tiers utilise une instance qui n'est pas "
             "la sienne.",
    )
    # -- recipient allowlist (anti-piggyback, destination side): restrict WHO
    #    can be a recipient. A client instance set to « @client.com » can only
    #    send to the client's own people — it can't be abused to relay files to
    #    arbitrary external addresses.
    recipient_allowlist = fields.Text(
        string="Destinataires autorisés",
        help="Même format que les expéditeurs (adresse complète ou domaine). "
             "VIDE = aucune restriction. Renseigné = les fichiers ne peuvent "
             "être envoyés QU'À ces adresses/domaines — verrouille l'instance "
             "à son propre usage.",
    )

    # -- slug-addressed public page, served at /to/<slug>. The slug ALONE
    #    publishes the page; fixed_recipient is an independent, optional lock:
    #      slug only                  -> open page, free sender and recipients
    #      slug + fixed_recipient     -> drop page ("Dropbox"-style): the
    #         recipient is FORCED server-side (create + finalize), so nobody can
    #         redirect a drop to another address.
    #    Empty slug = a normal Host-served brand, reachable at /secrets only.
    slug = fields.Char(
        string="Identifiant de page (slug)",
        index=True,
        help="Publie cette marque à /to/<slug> (p. ex. « depot » → "
             "/to/depot). Minuscules, chiffres et tirets. Vide = marque "
             "servie par domaine seulement.",
    )
    fixed_recipient = fields.Char(
        string="Destinataire unique (page de dépôt)",
        help="Optionnel. Quand renseigné, la page devient une page de dépôt : "
             "tous les envois vont UNIQUEMENT à cette adresse, quel que soit "
             "ce que saisit l'expéditeur, et le champ destinataire est masqué. "
             "Vide = page ouverte, l'expéditeur choisit ses destinataires.",
    )
    fixed_recipient_name = fields.Char(
        string="Nom affiché du destinataire",
        help="Nom montré sur la page de dépôt (« Envoi sécurisé à … »). "
             "Vide = l'adresse est affichée.",
    )
    # Employee behind an auto-provisioned personal drop page (see res.users).
    # Anchors lifecycle sync (rename / e-mail change / archive) and prevents
    # duplicate pages. Empty on manually-created or company-general brands.
    owner_user_id = fields.Many2one(
        "res.users",
        string="Employé (page auto)",
        ondelete="set null",
        index=True,
        copy=False,
        help="Renseigné quand cette page de dépôt a été créée automatiquement "
             "pour un employé. Sert à la resynchronisation et à l'archivage ; "
             "empêche les doublons.",
    )
    # Native, Shlink-free public URL of the slug-addressed page. Computed,
    # never stored: it tracks the slug and the resolved share base (brand
    # domain → public base URL → web.base.url) so the admin always sees the
    # live link.
    page_url = fields.Char(
        string="URL de la page publique",
        compute="_compute_page_url",
        help="Adresse publique native /to/<slug> de cette marque (aucun "
             "raccourcisseur externe). Vide tant qu'aucun slug n'est défini.",
    )

    # -- limits (0 = default from settings for the tier)
    max_transfer_mb = fields.Integer(
        string="Taille max. d'un transfert (Mo)",
        default=0,
        help="0 = valeur par défaut du palier (paramètres).",
    )
    max_files = fields.Integer(
        string="Nombre max. de fichiers",
        default=0,
        help="0 = valeur par défaut (paramètres).",
    )
    max_retention_days = fields.Integer(
        string="Rétention max. (jours)",
        default=0,
        help="0 = valeur par défaut du palier (paramètres).",
    )
    allow_password = fields.Boolean(
        string="Mot de passe autorisé", default=True,
    )
    allow_recipient_otp = fields.Boolean(
        string="Code destinataire offert", default=True,
        help="Laisse l'expéditeur exiger, depuis la page publique, un code à "
             "usage unique du destinataire avant l'affichage du contenu. Sans "
             "effet quand le réglage d'instance l'exige déjà pour tous les "
             "envois : la case est alors montrée cochée et verrouillée.",
    )

    # -- Audience ouverte (salle de données) ---------------------------------
    # Éteint par défaut, et sur les DEUX plans : la marque doit l'offrir, et
    # l'expéditeur doit le choisir. Un lien d'audience ouverte accepte une
    # adresse que personne n'a validée en amont — c'est un pouvoir qu'on
    # accorde, pas un défaut qu'on subit.
    allow_open_audience = fields.Boolean(
        string="Audience ouverte offerte", default=False,
        help="Autorise les envois dont le lien ne nomme aucun destinataire : "
             "le visiteur déclare son adresse et confirme par code à usage "
             "unique. Utile pour une salle de données ou une diffusion à un "
             "groupe dont on ne connaît pas toutes les adresses.",
    )
    audience_domains = fields.Text(
        string="Domaines admis (audience ouverte)",
        help="Liste blanche appliquée aux adresses auto-déclarées : adresse "
             "complète, ou domaine (« @client.com »), une par ligne ou "
             "séparées par des virgules. Vide = toute adresse est admise, "
             "dans la limite du nombre de visiteurs.",
    )
    audience_max_default = fields.Integer(
        string="Visiteurs max. (défaut)", default=50,
        help="Nombre maximal d'adresses distinctes admises sur un transfert "
             "en audience ouverte, quand l'envoi n'en fixe pas d'autre. "
             "0 = illimité (déconseillé : c'est le plafond qui empêche un "
             "lien fuité de servir de relais de courriel).",
    )
    allow_audience_sms = fields.Boolean(
        string="Code par SMS en audience ouverte", default=False,
        help="Laisse un visiteur d'audience ouverte demander son code sur son "
             "mobile. Éteint par défaut : chaque SMS a un coût réel et le "
             "numéro est fourni par le visiteur lui-même.",
    )

    watermark_downloads = fields.Boolean(
        string="Filigrane au téléchargement",
        default=False,
        help="Estampe un filigrane d'identification (destinataire, horodatage, "
             "marque) sur chaque PDF téléchargé via cette marque. Odoo sert "
             "alors les octets lui-même au lieu de rediriger vers le stockage. "
             "Sans effet sur les fichiers non-PDF.",
    )

    # -- Phase 2 placeholders (schema ready, no breaking migration later)
    notify_on_download = fields.Boolean(
        string="Notifier au téléchargement (Phase 2)", default=False,
    )
    allow_burn = fields.Boolean(
        string="Destruction après lecture (Phase 2)", default=False,
    )

    _sql_constraints = [
        ("domain_uniq", "unique(domain)",
         "Ce domaine est déjà associé à une autre marque."),
        ("slug_uniq", "unique(slug)",
         "Cet identifiant de page (slug) est déjà utilisé par une autre marque."),
    ]

    _SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

    @api.constrains("slug")
    def _check_slug_format(self):
        for rec in self.filtered("slug"):
            if not self._SLUG_RE.match(rec.slug):
                raise ValidationError(_(
                    "L'identifiant de page (slug) « %s » est invalide : "
                    "minuscules, chiffres et tirets seulement, sans espace.",
                    rec.slug))

    @api.constrains("fixed_recipient")
    def _check_fixed_recipient(self):
        for rec in self.filtered("fixed_recipient"):
            if not email_normalize(rec.fixed_recipient):
                raise ValidationError(_(
                    "Le destinataire unique « %s » n'est pas une adresse "
                    "courriel valide.", rec.fixed_recipient))

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
                    "Une seule marque active peut être la marque par défaut."
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
        return super().create(vals_list)

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
            host = (
                request.httprequest.headers.get("X-Forwarded-Host")
                or request.httprequest.host
                or ""
            )
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

    def _visuals(self):
        """Resolved visual identity, shared by the public QWeb pages and the
        mail templates so both can never drift. Image URLs are absolute on
        the brand domain (mail clients need absolute URLs).

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
            or "#29ABE1",
            "dark": self.color_dark
            or cfield("appointment_brand_dark")
            or cfield("report_brand_dark")
            or "#2D3031",
            "logo_url": logo_url,
            "logo_host": logo_host,
            "favicon_url": favicon_url,
            "powered_by": bool(self.powered_by),
            # « Propulsé par » must credit the hosting OPERATOR, never the
            # served brand. Prefer the company's PUBLIC brand name
            # (appointment_brand_name — the public brand) over
            # res.company.name, which is often the legal entity.
            "powered_by_name": cfield("appointment_brand_name")
            or company.name or self.name,
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
                "title": _("CORS appliqué"),
                "message": _("Origines autorisées : %s") % ", ".join(merged),
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
        """Absolute /web/image URL for a binary field, served to anonymous
        visitors. The backing attachment is flipped public=True (these are
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
        return "%s/web/image/%s" % (self._share_base_url(), att.id)

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
