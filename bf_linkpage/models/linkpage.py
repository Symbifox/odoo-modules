"""La page de liens elle-même."""

import base64
import io
import logging
import re
import secrets
import unicodedata
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.tools import format_date
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Le préfixe des pages publiques. Il est DÉLIBÉRÉMENT distinct de la racine :
# un slug servi à `/<slug>` entre en collision avec le routage du site (les
# pages website, /shop, /blog), et le préfixe de langue est retiré avant le
# routage, ce qui rend la collision intermittente donc difficile à voir. Un
# préfixe dédié coûte deux caractères dans le QR et supprime la classe entière.
URL_PREFIX = "/l"

# Les slugs qu'on refuse : ils entreraient en conflit avec les sous-chemins du
# module lui-même ou masqueraient une page technique.
RESERVED_SLUGS = {"new", "qr", "static", "index", "admin", "api", "l"}

_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

# L'alphabet des slugs opaques, celui des pages d'organisation. Les caractères
# qui se confondent à l'oeil et à la voix sont retirés (0/o, 1/l/i), parce
# qu'un slug finit par être dicté au téléphone ou recopié d'une impression.
# Trois groupes de quatre sur 31 signes valent 59 bits : deviner l'adresse
# d'une page coûte plus cher que de la demander.
_OPAQUE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_OPAQUE_GROUPS = 3
_OPAQUE_GROUP_LEN = 4

# Part du côté occupée par le logo incrusté au centre du QR, et la limite au-delà
# de laquelle la correction d'erreur de niveau H ne reconstruit plus le code.
# Mesurées au décodeur indépendant le 2026-08-30 : 34 % passe encore, 40 % non.
LOGO_RATIO = 0.22
LOGO_MAX_RATIO = 0.30


class BfLinkpage(models.Model):
    _name = "bf.linkpage"
    _description = "Link page"
    # `mail.activity.mixin` en plus du fil : la passe d'avertissement pose une
    # activité sur la page dont l'échéance approche. ⚠️ Créer une activité sur
    # un modèle qui n'a que `mail.thread` passe sans broncher à l'écriture, puis
    # casse le TABLEAU DES ACTIVITÉS entier (`ir.model.is_mail_activity` est
    # faux, et la référence posée par `mail_activity_board` devient invalide).
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(
        string="Title",
        required=True,
        tracking=True,
        help="The title shown at the top of the public page.",
    )
    slug = fields.Char(
        string="URL slug",
        required=True,
        copy=False,
        index=True,
        tracking=True,
        help=("The part of the address after %s/. Lowercase letters, "
              "digits and hyphens.") % URL_PREFIX,
    )
    active = fields.Boolean(default=True)

    kind = fields.Selection(
        [
            ("owner", "Linked to a person"),
            ("org", "Linked to an organization"),
            ("oneoff", "One-off"),
        ],
        string="Page type",
        default="owner",
        required=True,
        tracking=True,
        help="A linked page lives as long as the person. An organization "
             "page gathers a client company's access points: its address "
             "is opaque and it has an expiry date. A one-off page has no "
             "owner: it has an expiry.",
    )

    partner_id = fields.Many2one(
        "res.partner",
        string="Contact",
        tracking=True,
        ondelete="cascade",
        help="The person whose links this page gathers. The dynamic "
             "sources (email, phone, website) are read from this record.",
    )
    user_id = fields.Many2one(
        "res.users",
        string="User",
        tracking=True,
        ondelete="set null",
        help="Needed to resolve the person's appointment page, which goes "
             "through their resource.",
    )

    # Le conseiller d'une page d'organisation. Il vit SUR LA PAGE, comme
    # `booking_slug` et `meet_url`, et pour la même raison : un lien venu d'un
    # gabarit est supprimé puis recréé à chaque rafraîchissement, donc rien de
    # propre à une page ne peut vivre sur ses liens.
    #
    # Mesuré sur une base réelle avant d'en faire un champ : le vendeur
    # (`res.partner.user_id`) n'est renseigné que sur une minorité de fiches
    # clientes, et sur aucune de celles qui portent un mandat vivant. Déduire
    # le conseiller aurait donc résolu là où ça ne sert pas, et pas là où ça
    # sert.
    advisor_user_id = fields.Many2one(
        "res.users",
        string="Advisor",
        tracking=True,
        ondelete="set null",
        help="The person on our side this organization contacts: their "
             "calendar, email and secure upload are what the page "
             "resolves. Suggested at creation from the record's "
             "salesperson, then frozen: the salesperson can change for "
             "billing reasons without the page having to follow.",
    )

    template_id = fields.Many2one(
        "bf.linkpage.template",
        string="Template",
        tracking=True,
        ondelete="set null",
        help="The set of links placed at creation. Applying a template "
             "OVERWRITES the inherited links but keeps the links added by "
             "hand.",
    )

    # -- présentation --------------------------------------------------------
    headline = fields.Char(
        string="Subtitle",
        translate=True,
        help="One line under the title. The role, the city, whatever you like.",
    )
    bio = fields.Text(string="Presentation", translate=True)
    avatar = fields.Image(string="Photo", max_width=512, max_height=512)
    accent_color = fields.Char(
        string="Accent color",
        default="#29ABE2",
        help="Hexadecimal. Used for the public page buttons and the "
             "branded QR code.",
    )
    layout = fields.Selection(
        [
            ("cards", "Cards"),
            ("soft", "Raised cards"),
            ("minimal", "Minimal"),
            ("pills", "Solid buttons"),
            ("mono", "Technical"),
        ],
        string="Layout",
        default="cards",
        required=True,
        help="The look of the links. Independent of the light/dark theme: "
             "all four layouts exist in both tones.",
    )
    theme = fields.Selection(
        [
            ("auto", "Device setting"),
            ("light", "Light"),
            ("dark", "Dark"),
        ],
        default="auto",
        required=True,
        string="Theme",
        help="\"Device setting\" follows the visitor's system preference, "
             "and gives them a toggle on the page. The other two impose "
             "the theme; the toggle is still offered but only persists on "
             "their side.",
    )
    show_theme_toggle = fields.Boolean(
        string="Offer the light/dark toggle",
        default=True,
        help="The visitor's choice is kept in THEIR browser only. Nothing "
             "is written here, and nothing follows them from one device "
             "to another.",
    )
    show_vcard = fields.Boolean(
        string="Offer the business card",
        default=True,
        help="An \"Add to my contacts\" button on the public page. No "
             "effect on a one-off page, which belongs to no one.",
    )
    show_company_logo = fields.Boolean(
        string="Show the company logo",
        default=True,
    )

    # -- cycle de vie --------------------------------------------------------
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("published", "Published"),
            ("closed", "Closed"),
        ],
        default="draft",
        required=True,
        tracking=True,
        string="State",
    )
    date_expiry = fields.Datetime(
        string="Expires on",
        tracking=True,
        copy=False,
        help="After this date the page returns a 404. Empty = no expiry.",
    )
    expiry_warned_on = fields.Date(
        string="Expiry announced on",
        readonly=True,
        copy=False,
        help="The expiry date for which a warning has already been sent. "
             "The announced DATE is kept rather than a boolean: extending "
             "the expiry changes the date, so the next warning goes out "
             "on its own, with no action or reset.",
    )
    is_expired = fields.Boolean(
        string="Expired",
        compute="_compute_is_expired",
        search="_search_is_expired",
        help="Computed: expiry is a DATE, not a state to maintain. No "
             "cron needs to run for a page to close.",
    )
    is_live = fields.Boolean(
        string="Live",
        compute="_compute_is_expired",
        help="Published, not archived and not expired.",
    )

    # Deux valeurs qui appartiennent à la PERSONNE, et qui vivent donc sur la
    # page et non sur ses liens. Un lien venu d'un gabarit est supprimé puis
    # recréé à chaque rafraîchissement : le personnaliser sur la page ne
    # survivrait pas au premier passage. Posées ici, elles survivent, et le
    # gabarit reste le même pour tout le monde.
    booking_slug = fields.Char(
        string="Appointment type",
        help="The slug of the appointment type to open, when you do not "
             "want the one the search would pick. Leave empty to take the "
             "first public type linked to the person's resource, which is "
             "not necessarily the one they put in their signature.",
    )
    meet_url = fields.Char(
        string="Instant meeting room",
        help="The address of a permanent room (Talk, video conference). "
             "It is not derived from any other module: it is an address "
             "specific to the person, pasted here once.",
    )

    # La photo affichée n'est pas forcément CELLE de la page. On préfère une
    # chaîne de replis à une copie : copier l'image du contact la figerait le
    # jour où elle change, et une page de liens dont la photo date de deux ans
    # est exactement le genre de détail que personne ne pense à corriger.
    has_photo = fields.Boolean(compute="_compute_has_photo")

    # -- le code QR, réglable depuis la fiche --------------------------------
    qr_branded = fields.Boolean(
        string="Logo in the center",
        default=True,
        help="The logo hides some of the code's modules. Error correction "
             "compensates, and the share covered is capped at what stays "
             "readable.",
    )
    qr_logo = fields.Image(
        string="QR code logo",
        max_width=512,
        max_height=512,
        help="Leave empty to use the company logo. A square image gives "
             "the best result: it is resized without being cropped.",
    )
    qr_logo_plate = fields.Selection(
        [
            ("back", "Code background"),
            ("fill", "Code color"),
            ("none", "None"),
        ],
        string="Plate behind the logo",
        default="back",
        required=True,
        help="The small square placed under the logo. \"Code color\" is "
             "useful when the logo and the background share the same hue: "
             "a blue logo on a blue background disappears. \"None\" "
             "places the logo directly on the code.",
    )
    qr_scale = fields.Selection(
        [("s", "Small (screen)"), ("m", "Medium"), ("l", "Large (print)")],
        string="Size",
        default="m",
        required=True,
    )
    qr_fill_color = fields.Char(
        string="Code color",
        default="#000000",
        help="The color of the modules. It must stay clearly DARKER than "
             "the background: a light code on a dark background cannot be "
             "read by most devices.",
    )
    qr_back_color = fields.Char(
        string="Code background",
        default="#FFFFFF",
    )
    qr_preview = fields.Image(
        string="Preview",
        compute="_compute_qr_preview",
        help="Actual rendering, with the settings above. What you see "
             "here is exactly what will be downloaded.",
    )
    qr_warning = fields.Char(compute="_compute_qr_preview")

    link_ids = fields.One2many("bf.linkpage.link", "page_id", string="Links")
    link_count = fields.Integer(compute="_compute_link_count")
    visible_link_count = fields.Integer(
        string="Displayed links",
        compute="_compute_link_count",
        help="The links whose source actually resolves. A gap with the "
             "total count is the only way to see that a source is silent.",
    )

    visit_count = fields.Integer(string="Visits", default=0, readonly=True, copy=False)
    last_visit = fields.Datetime(string="Last visit", readonly=True, copy=False)

    public_url = fields.Char(string="Public address", compute="_compute_public_url")

    _sql_constraints = [
        ("slug_uniq", "unique(slug)", "This URL slug is already taken."),
    ]

    # ── calculs ──────────────────────────────────────────────────────────────

    @api.depends("state", "active", "date_expiry")
    def _compute_is_expired(self):
        now = fields.Datetime.now()
        for page in self:
            page.is_expired = bool(page.date_expiry and page.date_expiry <= now)
            page.is_live = bool(
                page.active and page.state == "published" and not page.is_expired
            )

    def _search_is_expired(self, operator, value):
        if operator not in ("=", "!="):
            raise ValueError(_("Unsupported operator on \"Expired\"."))
        expired = value if operator == "=" else not value
        now = fields.Datetime.now()
        if expired:
            return [("date_expiry", "!=", False), ("date_expiry", "<=", now)]
        return ["|", ("date_expiry", "=", False), ("date_expiry", ">", now)]

    @api.depends("link_ids", "link_ids.active", "link_ids.resolved_url")
    def _compute_link_count(self):
        for page in self:
            links = page.link_ids.filtered("active")
            page.link_count = len(links)
            page.visible_link_count = len(links.filtered("resolved_url"))

    @api.depends("slug")
    def _compute_public_url(self):
        base = self._base_url()
        for page in self:
            page.public_url = "%s%s/%s" % (base, URL_PREFIX, page.slug) if page.slug else ""

    @api.model
    @api.depends("avatar", "partner_id", "user_id")
    def _compute_has_photo(self):
        for page in self:
            page.has_photo = bool(page._photo_payload())

    def _photo_payload(self):
        """La photo à servir, en base64, ou False.

        Ordre : la photo posée sur la page, puis celle du contact, puis celle
        du compte.

        `image_256` et non l'originale : la page affiche la photo dans un
        cercle de six rem, soit moins de 200 px même sur un écran à double
        densité, et 256 suffit encore à l'aperçu au partage. Mesuré sur la
        une fiche réelle : 408 Ko en taille 1024, 276 Ko en 512. Servir un
        demi-mégaoctet pour une vignette de 96 px est la sorte de gaspillage
        que personne ne remarque avant d'ouvrir la page sur un forfait mobile.
        """
        self.ensure_one()
        # ⚠️ `bin_size` : le client web lit les champs binaires en demandant
        # leur TAILLE LISIBLE plutôt que leur contenu, pour ne pas transporter
        # des mégaoctets dans un formulaire. Le champ rend alors b"32.99 Kb",
        # que tout traitement d'image prend pour des données et refuse. Le
        # défaut ne se voit QUE par le navigateur : en shell, le contexte n'est
        # pas posé et tout fonctionne. Il faut donc le forcer ici, au plus près
        # de la lecture, et non compter sur l'appelant.
        self = self.with_context(bin_size=False)
        if self.avatar:
            return self.avatar
        for record in (self.partner_id, self.user_id.partner_id):
            record = record.with_context(bin_size=False)
            if record and record.image_256:
                return record.image_256
        return False

    def _employee(self):
        """La fiche employé de la personne de cette page, ou un ensemble vide.

        Registre vérifié plutôt qu'importé : le module ne dépend pas de `hr`.
        """
        self.ensure_one()
        Employee = self.env.get("hr.employee")
        if Employee is None or not self.user_id:
            return None
        return self.env["hr.employee"].sudo().search(
            [("user_id", "=", self.user_id.id)], limit=1
        )

    def _company(self):
        """L'entreprise dont on montre le logo."""
        self.ensure_one()
        return (
            self.user_id.company_id
            or self.partner_id.company_id
            or self.env.company
        )

    # ── la carte de visite téléchargeable ────────────────────────────────────

    @staticmethod
    def _vcard_escape(valeur):
        """Échapper une valeur vCard.

        La virgule, le point-virgule et la barre oblique inverse sont des
        SÉPARATEURS dans le format. Un nom d'organisation qui en contient un,
        « Morin, Roy et Associés » par exemple, casse la fiche en deux champs
        chez qui l'importe, sans erreur nulle part.
        """
        return (
            (valeur or "")
            .replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\n", "\\n")
        )

    def _vcard_available(self):
        """Une carte n'a de sens que pour une page qui porte une personne."""
        self.ensure_one()
        return bool(self.show_vcard and self.kind == "owner" and self.partner_id)

    def _vcard(self):
        """La carte de visite de la personne de cette page, en vCard 3.0.

        3.0 et non 4.0 : c'est la version que les carnets d'adresses lisent
        tous, Apple et Android compris. La 4.0 est plus propre et moins reçue,
        et une carte qu'un téléphone refuse d'ouvrir ne sert à rien.

        L'adresse de la PAGE est incluse comme URL. C'est ce qui rend la carte
        durable : les coordonnées enregistrées vieillissent, le lien vers la
        page reste juste.
        """
        self.ensure_one()
        e = self._vcard_escape
        partner = self.partner_id
        nom = (partner.name or self.name or "").strip()
        morceaux = nom.split(" ", 1)
        prenom = morceaux[0] if morceaux else ""
        famille = morceaux[1] if len(morceaux) > 1 else ""
        societe = self.sudo()._company().name or ""

        lignes = [
            "BEGIN:VCARD",
            "VERSION:3.0",
            "N:%s;%s;;;" % (e(famille), e(prenom)),
            "FN:%s" % e(nom),
        ]
        if societe:
            lignes.append("ORG:%s" % e(societe))
        if self.headline:
            lignes.append("TITLE:%s" % e(self.headline))
        if partner.email:
            lignes.append("EMAIL;TYPE=INTERNET,WORK:%s" % e(partner.email))

        # Les mêmes numéros que la page, et dans le même ordre. Une carte qui
        # donnerait d'autres numéros que la page serait une deuxième vérité à
        # tenir à jour, et c'est exactement ce que ce module refuse ailleurs.
        # Le bureau vient de la fiche employé, à défaut de la société : c'est le
        # numéro d'une signature, et il n'est PAS sur la fiche contact.
        employe = self._employee()
        societe = self.sudo()._company()
        numeros = [
            ("WORK,VOICE", (employe.work_phone if employe else None) or societe.phone),
            ("WORK,VOICE,PREF", societe.mobile),
            ("CELL", partner.mobile or (employe.mobile_phone if employe else None)),
            ("HOME,VOICE", partner.phone),
        ]
        vus = set()
        for etiquette, valeur in numeros:
            if not valeur:
                continue
            # Un même numéro saisi à deux endroits ne doit pas sortir deux fois :
            # le carnet d'adresses le montrerait en double sans rien expliquer.
            cle = "".join(c for c in valeur if c.isdigit())
            if cle in vus:
                continue
            vus.add(cle)
            lignes.append("TEL;TYPE=%s:%s" % (etiquette, e(valeur)))
        lignes.append("URL:%s" % e(self.public_url))
        lignes.append("REV:%s" % fields.Datetime.now().strftime("%Y%m%dT%H%M%SZ"))
        lignes.append("END:VCARD")
        # CRLF : la spécification l'impose, et certains carnets d'adresses
        # refusent une carte en fins de ligne Unix.
        return ("\r\n".join(lignes) + "\r\n").encode("utf-8")

    def _base_url(self):
        return (
            self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        ).rstrip("/")

    # ── contraintes ──────────────────────────────────────────────────────────

    @api.constrains("slug")
    def _check_slug(self):
        for page in self:
            if not page.slug:
                continue
            if not _SLUG_RE.match(page.slug):
                raise ValidationError(_(
                    "The URL slug \"%s\" is not valid: lowercase letters, "
                    "digits and hyphens, without starting or ending with "
                    "a hyphen.", page.slug,
                ))
            if page.slug in RESERVED_SLUGS:
                raise ValidationError(_(
                    "The URL slug \"%s\" is reserved.", page.slug,
                ))

    @api.constrains("kind", "partner_id")
    def _check_owner(self):
        for page in self:
            if page.kind == "owner" and not page.partner_id:
                raise ValidationError(_(
                    "A page linked to a person needs a contact. Without a "
                    "contact, its dynamic sources have nothing to read."
                ))
            if page.kind != "org":
                continue
            if not page.partner_id:
                raise ValidationError(_(
                    "An organization page needs the organization's "
                    "record. Without it, none of its sources knows which "
                    "company it is about."
                ))
            # Une page d'organisation qui pointe une PERSONNE ne se distingue
            # d'une page personnelle que par une étiquette, et se met à
            # résoudre des sources qui ne lui sont pas destinées : le portail
            # de l'organisation, ses factures, ses projets. On refuse tôt.
            if not page.partner_id.is_company:
                raise ValidationError(_(
                    "\"%s\" is a person, not an organization. An "
                    "organization page is linked to the company record; "
                    "for a person, choose \"Linked to a person\".", page.partner_id.display_name,
                ))

    @api.constrains("accent_color")
    def _check_accent_color(self):
        for page in self:
            if page.accent_color and not re.match(r"^#[0-9A-Fa-f]{6}$", page.accent_color):
                raise ValidationError(_(
                    "The accent color must be a six-digit hexadecimal, "
                    "for example #29ABE2."
                ))

    # ── création ─────────────────────────────────────────────────────────────

    def _check_slug_free(self, slug, exclude=None):
        """Refuser un slug déjà pris, AVANT l'insertion.

        Le contrôle ne peut pas vivre dans un `@api.constrains` : la contrainte
        SQL d'unicité s'applique au moment du INSERT, donc elle lève une
        `UniqueViolation` brute avant que la moindre contrainte Python ne
        tourne. L'usager recevait une erreur de base de données sans moyen de
        deviner qu'une page ARCHIVÉE retenait le slug — l'unicité SQL, elle,
        ne connaît pas l'archivage.
        """
        if not slug:
            return
        domain = [("slug", "=", slug)]
        if exclude:
            domain.append(("id", "not in", exclude.ids))
        collision = self.with_context(active_test=False).sudo().search(domain, limit=1)
        if collision:
            raise ValidationError(_(
                "The URL slug \"%(slug)s\" is already taken by "
                "\"%(name)s\"%(etat)s.",
                slug=slug,
                name=collision.name,
                etat="" if collision.active else _(", an archived page"),
            ))

    @api.model_create_multi
    def create(self, vals_list):
        vus = set()
        for vals in vals_list:
            # L'ordre compte : le slug opaque se décide AVANT le slug déduit
            # du nom, sans quoi une page d'organisation naîtrait à l'adresse
            # `/l/entreprise-cliente-inc`, devinable par quiconque connaît la liste
            # des clients, et lisible par quiconque voit passer l'adresse.
            if not vals.get("slug") and vals.get("kind") == "org":
                vals["slug"] = self._generate_opaque_slug()
            if not vals.get("slug") and vals.get("name"):
                vals["slug"] = self._generate_slug(vals["name"])
            # Une page d'organisation naît avec une échéance, comme une page
            # ponctuelle. La différence est ailleurs : on avertit avant
            # qu'elle tombe (`_cron_warn_expiring`), parce qu'une page fermée
            # au milieu d'un mandat rend un 404 au client sans prévenir
            # personne chez nous.
            if vals.get("kind") == "org" and not vals.get("date_expiry"):
                vals["date_expiry"] = self._default_org_expiry()
            # « Absent » et « vidé » ne se confondent pas : proposer un
            # conseiller quand personne n'en a nommé est une commodité, le
            # reposer sur un champ qu'on vient délibérément de vider serait
            # une correction non demandée.
            if vals.get("kind") == "org" and "advisor_user_id" not in vals:
                vals["advisor_user_id"] = self._default_advisor(
                    vals.get("partner_id")
                )
            # Une page ponctuelle sans expiration est l'angle mort qu'on
            # refuse : elle reste ouverte parce que personne ne repasse. On
            # arme la date à la création plutôt que de compter sur un geste.
            if vals.get("kind") == "oneoff" and not vals.get("date_expiry"):
                vals["date_expiry"] = self._default_oneoff_expiry()
            if vals.get("slug"):
                if vals["slug"] in vus:
                    raise ValidationError(_(
                        "The URL slug \"%s\" appears twice in the same "
                        "submission.", vals["slug"],
                    ))
                vus.add(vals["slug"])
                self._check_slug_free(vals["slug"])
        pages = super().create(vals_list)
        for page in pages:
            if page.template_id:
                page._apply_template(with_visual=True)
        return pages

    @api.model
    def _default_oneoff_expiry(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "bf_linkpage.oneoff_expiry_days", "90"
        )
        # Un paramètre mal saisi ne doit pas empêcher de créer une page : il
        # doit retomber sur le délai par défaut. `get_param` rend d'ailleurs
        # False quand la clé est absente, ce qu'un int() prendrait mal.
        try:
            days = int(raw)
        except (TypeError, ValueError):
            _logger.warning(
                "bf_linkpage: bf_linkpage.oneoff_expiry_days vaut %r, "
                "qui n'est pas un nombre de jours ; repli sur 90.", raw,
            )
            days = 90
        if days <= 0:
            days = 90
        return fields.Datetime.now() + timedelta(days=days)

    @api.model
    def _generate_opaque_slug(self):
        """Un slug qui ne dit rien de l'organisation qu'il sert.

        Le slug d'une page personnelle est le nom de la personne, et c'est
        voulu : il vit sous un QR dans une signature, on le lit, on le tape.
        Celui d'une page d'organisation obéit à l'inverse : il voyage dans un
        courriel d'accueil et il est la seule chose qui tienne la porte. Un
        slug déduit du nom la laisserait ouverte à qui connaît le nom.

        `secrets` et non `random` : le second est un générateur reproductible,
        semé à l'ouverture du processus. Il ne convient pas à ce qui tient lieu
        de clé.
        """
        for _essai in range(20):
            slug = "-".join(
                "".join(
                    secrets.choice(_OPAQUE_ALPHABET)
                    for _car in range(_OPAQUE_GROUP_LEN)
                )
                for _groupe in range(_OPAQUE_GROUPS)
            )
            if slug in RESERVED_SLUGS:
                continue
            if not self.with_context(active_test=False).sudo().search_count(
                [("slug", "=", slug)]
            ):
                return slug
        # 59 bits : on n'arrive jamais ici autrement que par une base en
        # panne. Lever plutôt que rendre un slug dont on ne sait pas s'il est
        # libre, ce qui donnerait une page servie à la place d'une autre.
        raise ValidationError(_(
            "Could not draw a free address for this page. Try again, and "
            "report it if it happens again."
        ))

    @api.model
    def _default_org_expiry(self):
        """L'échéance posée sur une page d'organisation qui naît."""
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "bf_linkpage.org_expiry_days", "365"
        )
        try:
            days = int(raw)
        except (TypeError, ValueError):
            days = 365
        if days <= 0:
            return False
        return fields.Datetime.now() + timedelta(days=days)

    @api.model
    def _default_advisor(self, partner_id):
        """Le conseiller proposé à la création d'une page d'organisation.

        Trois chemins, et aucun n'est sûr, d'où un champ sur la page plutôt
        qu'un calcul : le vendeur de la fiche, à défaut le gestionnaire du
        projet actif le plus récent de l'organisation, à défaut la personne qui
        crée la page.
        """
        if partner_id:
            partner = self.env["res.partner"].browse(partner_id).exists()
            if partner and partner.user_id:
                return partner.user_id.id
            if partner:
                cibles = [partner.id] + partner.child_ids.ids
                projet = self.env["project.project"].sudo().search(
                    [("partner_id", "in", cibles), ("user_id", "!=", False)],
                    order="write_date desc", limit=1,
                ) if "project.project" in self.env else None
                if projet:
                    return projet.user_id.id
        return self.env.user.id

    @api.model
    def _generate_slug(self, value, exclude_id=None):
        """Un slug lisible et libre, dérivé d'un nom."""
        normalized = unicodedata.normalize("NFKD", value or "")
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
        base = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-") or "page"
        candidate, suffix = base, 1
        while True:
            domain = [("slug", "=", candidate)]
            if exclude_id:
                domain.append(("id", "!=", exclude_id))
            # `with_context(active_test=False)` : la contrainte SQL d'unicité
            # ignore l'archivage. Chercher sans archivés proposerait un slug
            # déjà pris par une page archivée, et l'insertion échouerait.
            taken = self.with_context(active_test=False).search_count(domain)
            if not taken and candidate not in RESERVED_SLUGS:
                return candidate
            suffix += 1
            candidate = "%s-%d" % (base, suffix)

    # ── gabarits ─────────────────────────────────────────────────────────────

    def _apply_template(self, with_visual=False):
        """Poser les liens du gabarit.

        Les liens déjà posés par un gabarit sont remplacés ; ceux ajoutés à la
        main sur la page sont laissés en place. Sans cette distinction,
        réappliquer un gabarit effacerait silencieusement le travail de la
        personne sur sa propre page.
        """
        Link = self.env["bf.linkpage.link"]
        for page in self:
            if not page.template_id:
                continue
            page.link_ids.filtered(lambda link: link.from_template).unlink()
            for line in page.template_id.line_ids:
                link = Link.create(line._link_values(page))
                line._copy_translations_to(link)
            # `with_visual` est FAUX par défaut, et c'est ce qui compte : la
            # passe périodique appelle cette méthode toutes les nuits. Si elle
            # reposait l'allure, la couleur ou la disposition choisie par
            # quelqu'un sur sa page serait défaite pendant son sommeil.
            if with_visual:
                page.write(page.template_id._visual_values())

    def action_apply_template(self):
        # Un clic explicite sur « Appliquer le gabarit » veut dire l'allure
        # AUSSI : c'est un geste délibéré, pas une passe de fond.
        self._apply_template(with_visual=True)
        return True

    # ── états ────────────────────────────────────────────────────────────────

    def write(self, vals):
        if vals.get("slug"):
            self._check_slug_free(vals["slug"], exclude=self)
        return super().write(vals)

    def action_publish(self):
        self.write({"state": "published"})
        return True

    def action_close(self):
        self.write({"state": "closed"})
        return True

    def action_renew(self):
        """Repousser l'échéance d'autant que la durée par défaut."""
        echeance = self._default_org_expiry()
        if not echeance:
            raise ValidationError(_(
                "No duration is configured for organization pages: "
                "nothing to extend."
            ))
        for page in self:
            page.date_expiry = echeance
            page.message_post(body=Markup("<p>%s</p>") % _(
                "Expiry extended to %s.",
                fields.Datetime.to_string(echeance)[:10],
            ))
        return True

    @api.model
    def _bf_reader_lang(self, user):
        """La langue d'une personne si elle est installée, sinon celle de la
        société, sinon l'anglais (la langue de la source)."""
        installees = {code for code, _nom in self.env["res.lang"].get_installed()}
        for lang in (user.lang if user else False, self.env.company.partner_id.lang):
            if lang in installees:
                return lang
        return "en_US"

    @api.model
    def _cron_warn_expiring(self):
        """Prévenir AVANT qu'une page d'organisation se ferme.

        Une page ponctuelle qui expire est un succès : elle a fait son temps,
        et l'oubli la ferme au lieu de la laisser ouverte. Une page
        d'organisation qui expire au milieu d'un mandat est l'inverse : le
        client tombe sur un 404 et personne chez nous ne l'apprend, puisque
        l'expiration est un CALCUL et non un état qu'un cron traverse.

        D'où cette passe, qui ne ferme rien : elle avertit. C'est la seule
        pièce du module qui doit tourner pour que l'échéance reste tenable.
        """
        get = self.env["ir.config_parameter"].sudo().get_param
        try:
            jours = int(get("bf_linkpage.org_warn_days", "30"))
        except (TypeError, ValueError):
            jours = 30
        maintenant = fields.Datetime.now()
        pages = self.search([
            ("kind", "=", "org"),
            ("state", "=", "published"),
            ("date_expiry", "!=", False),
            ("date_expiry", ">", maintenant),
            ("date_expiry", "<=", maintenant + timedelta(days=jours)),
        ])
        avertis = 0
        for page in pages:
            echeance = page.date_expiry.date()
            # La marque porte la DATE annoncée : une échéance repoussée
            # rearme l'avertissement sans qu'on ait à effacer quoi que ce soit.
            if page.expiry_warned_on == echeance:
                continue
            reste = (page.date_expiry - maintenant).days
            responsable = page.advisor_user_id or page.create_uid
            # Le travail planifié n'a pas d'usager : l'avis est écrit pour qui le
            # lira, la personne responsable de la page (avant : dans la langue
            # d'OdooBot), la date formatée dans sa langue.
            lu = page.with_context(lang=self._bf_reader_lang(responsable))
            corps = Markup("<p>%s</p>") % lu.env._(
                "This page closes on %(date)s, in %(jours)s day(s). After "
                "that date, its address returns a 404 to whoever received "
                "it. Use \"Extend expiry\" if the engagement continues.",
                date=format_date(lu.env, echeance),
                jours=reste,
            )
            page.message_post(body=corps)
            if responsable:
                page.activity_schedule(
                    "mail.mail_activity_data_todo",
                    date_deadline=echeance,
                    summary=lu.env._("Organization page to renew or close"),
                    note=corps,
                    user_id=responsable.id,
                )
            page.expiry_warned_on = echeance
            avertis += 1
        if avertis:
            _logger.info(
                "bf_linkpage: %s page(s) d'organisation près de l'échéance.",
                avertis,
            )
        return avertis

    def action_open_public(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": "%s/%s" % (URL_PREFIX, self.slug),
            "target": "new",
        }

    # ── résolution publique ──────────────────────────────────────────────────

    @api.model
    def _resolve_slug(self, slug):
        """Rendre la page servie par ce slug, ou un ensemble vide.

        Un slug inconnu, une page archivée, en brouillon, fermée ou expirée
        rendent tous LA MÊME CHOSE : rien. L'appelant en fait un 404. On ne
        distingue pas les cas côté public, sans quoi l'adresse deviendrait un
        oracle qui dit à un visiteur anonyme quels slugs existent.
        """
        if not slug:
            return self.browse()
        page = self.sudo().search([("slug", "=", slug)], limit=1)
        if not page or not page.is_live:
            return self.browse()
        return page

    def _register_visit(self):
        """Compter une visite sans faire échouer l'affichage.

        L'écriture se fait dans son propre point de reprise : une page publique
        doit s'afficher même si le compteur ne peut pas s'incrémenter.
        """
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                # Incrément fait par la base, pas en Python. Un
                # lire-modifier-écrire perd des visites dès que deux
                # visiteurs arrivent ensemble, et une page atteinte par QR
                # est justement lue en rafale après un envoi.
                self.env.cr.execute(
                    "UPDATE bf_linkpage SET visit_count = COALESCE(visit_count, 0) + 1, "
                    "last_visit = NOW() AT TIME ZONE 'UTC' WHERE id = %s",
                    (self.id,),
                )
                self.invalidate_recordset(["visit_count", "last_visit"])
        except Exception:  # noqa: BLE001
            _logger.warning("bf_linkpage: visite non comptée sur %s", self.slug)

    def _visible_links(self):
        """Tous les liens affichables, réseaux compris.

        Un lien dont la source ne résout pas est ABSENT, pas cassé.
        """
        self.ensure_one()
        return self.link_ids.filtered(lambda link: link.active and link.resolved_url)

    def _public_links(self):
        """Les liens principaux, en cartes."""
        return self._visible_links().filtered(lambda link: not link.is_social)

    def _social_links(self):
        """Les liens de réseaux, en rangée d'icônes."""
        return self._visible_links().filtered(lambda link: link.is_social)

    # ── QR ───────────────────────────────────────────────────────────────────

    @staticmethod
    def _luminance(couleur):
        """Luminance relative d'une couleur hexadécimale, ou None."""
        valeur = (couleur or "").strip().lstrip("#")
        if len(valeur) == 3:
            valeur = "".join(c * 2 for c in valeur)
        if len(valeur) != 6:
            return None
        try:
            canaux = [int(valeur[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
        except ValueError:
            return None
        lineaire = [
            c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
            for c in canaux
        ]
        return 0.2126 * lineaire[0] + 0.7152 * lineaire[1] + 0.0722 * lineaire[2]

    def _qr_colors(self):
        """Les deux couleurs à employer, et l'avertissement s'il y a lieu.

        Un code QR se lit par CONTRASTE, et pas n'importe lequel : la plupart
        des lecteurs attendent des modules sombres sur fond clair. Deux règles
        mécaniques, donc, plutôt qu'un avis de goût :

        1. le rapport de contraste doit atteindre 4:1, sous quoi le code ne se
           lit plus de façon fiable sur un écran, et encore moins imprimé ;
        2. le code doit être plus SOMBRE que le fond. Un code inversé est
           parfaitement contrasté et reste illisible pour beaucoup d'appareils.

        Quand une règle n'est pas tenue, on retombe sur le noir et blanc et on
        le DIT. Servir un code élégant qui ne scanne pas serait la pire des
        réponses : le défaut ne se voit qu'au moment où quelqu'un essaie.
        """
        self.ensure_one()
        fill = self.qr_fill_color or "#000000"
        back = self.qr_back_color or "#FFFFFF"
        l_fill = self._luminance(fill)
        l_back = self._luminance(back)
        if l_fill is None or l_back is None:
            return "#000000", "#FFFFFF", _(
                "Unrecognized color: the code is rendered in black on white."
            )
        if l_fill >= l_back:
            return "#000000", "#FFFFFF", _(
                "A code lighter than its background cannot be read by "
                "most devices. Rendered in black on white."
            )
        rapport = (l_back + 0.05) / (l_fill + 0.05)
        if rapport < 4.0:
            return "#000000", "#FFFFFF", _(
                "Insufficient contrast (%(r).1f:1, 4 is required). "
                "Rendered in black on white.", r=rapport,
            )
        return fill, back, False

    @api.depends("slug", "qr_branded", "qr_logo", "qr_scale", "qr_logo_plate",
                 "qr_fill_color", "qr_back_color")
    def _compute_qr_preview(self):
        for page in self:
            page.qr_warning = False
            page.qr_preview = False
            if not page.slug:
                continue
            try:
                _fill, _back, avis = page._qr_colors()
                if not avis and page.qr_branded:
                    # Un logo demandé mais inutilisable doit se voir SUR LA
                    # FICHE. C'est le seul endroit où la personne qui vient de
                    # le téléverser regardera.
                    _logo, avis = page._qr_logo()
                page.qr_warning = avis
                page.qr_preview = base64.b64encode(page._qr_png())
            except Exception as echec:  # noqa: BLE001
                # L'aperçu ne doit JAMAIS empêcher d'ouvrir la fiche : une
                # erreur ici rendrait le formulaire inaccessible, pour un
                # champ purement décoratif.
                _logger.warning("bf_linkpage: aperçu du QR impossible (%s)", echec)
                page.qr_warning = _("Preview unavailable.")

    def action_download_qr(self):
        """Télécharger le code QR tel qu'il est réglé sur la fiche."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "target": "self",
            "url": "/web/content/bf.linkpage/%s/qr_preview?download=true"
                   "&filename=qr-%s.png" % (self.id, self.slug),
        }

    _QR_BOX = {"s": 6, "m": 10, "l": 16}

    def _qr_png(self, branded=None, box_size=None):
        """Rendre le PNG du QR de cette page.

        `branded` incruste le logo de la société au centre, ce qui MASQUE des
        modules du code : la correction d'erreur doit compenser.

        Mesuré le 2026-08-30 avec un décodeur indépendant (zxing-cpp, pas la
        bibliothèque qui produit l'image), logo à 22 % du côté, URL de la
        forme `https://…/l/<slug>` :

            niveau L (7 %)   -> ILLISIBLE, même en pleine résolution
            niveau M (15 %)  -> lu jusqu'à 64 px de côté
            niveau Q (25 %)  -> lu jusqu'à 64 px
            niveau H (30 %)  -> lu jusqu'à 64 px

        M suffirait donc ; H est retenu pour la marge, parce que le QR finit
        imprimé et que l'encre, le papier et un logo plus grand mangent cette
        marge. La part du logo, elle, a une limite dure : au niveau H, 34 % du
        côté passe encore et 40 % ne passe plus. `LOGO_MAX_RATIO` la borne.
        """
        self.ensure_one()
        import qrcode
        from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_M
        from PIL import Image

        # Les arguments explicites l'emportent (le contrôleur et les tests s'en
        # servent) ; sinon on suit ce qui est réglé sur la fiche.
        if branded is None:
            branded = self.qr_branded
        if box_size is None:
            box_size = self._QR_BOX.get(self.qr_scale, 10)
        fill_color, back_color, _avis = self._qr_colors()

        qr = qrcode.QRCode(
            version=None,
            error_correction=ERROR_CORRECT_H if branded else ERROR_CORRECT_M,
            box_size=box_size,
            border=2,
        )
        qr.add_data(self.public_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color=fill_color, back_color=back_color).convert("RGB")

        if branded:
            logo, _raison = self._qr_logo()
            if logo is not None:
                side = int(min(img.size) * LOGO_RATIO)
                logo = logo.resize((side, side), Image.LANCZOS)
                if self.qr_logo_plate == "none":
                    # Sans cartouche : le logo est collé sur le code. Mesuré,
                    # ça se décode encore, parce que ce sont les MÊMES modules
                    # qui sont masqués dans les deux cas ; la cartouche n'ajoute
                    # au masquage que sa mince bordure.
                    position = (
                        (img.size[0] - side) // 2,
                        (img.size[1] - side) // 2,
                    )
                    img.paste(logo, position, logo if logo.mode == "RGBA" else None)
                else:
                    # Le cartouche ne prend jamais du blanc en dur : sur un code
                    # à fond coloré, un carré blanc au centre se voit comme une
                    # pièce rapportée. « fill » existe parce qu'un logo de la
                    # même teinte que le fond disparaît dessus : mesuré sur le
                    # renard bleu posé sur un fond bleu, il devenait illisible
                    # alors que le code, lui, se décodait toujours.
                    teinte = fill_color if self.qr_logo_plate == "fill" else back_color
                    backdrop = Image.new("RGB", (side + 16, side + 16), teinte)
                    backdrop.paste(logo, (8, 8), logo if logo.mode == "RGBA" else None)
                    position = (
                        (img.size[0] - backdrop.size[0]) // 2,
                        (img.size[1] - backdrop.size[1]) // 2,
                    )
                    img.paste(backdrop, position)

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()

    def _qr_logo(self):
        """Le logo à incruster, ou None. Rend aussi la RAISON de l'absence.

        Le silence était le vrai défaut ici. Un logo que la bibliothèque
        d'images ne sait pas ouvrir faisait produire un QR sans marque, avec
        pour seule trace un avertissement au journal que personne ne lit. Sur
        cette instance, le logo de la société est un SVG : le « QR à la
        marque » n'en a donc jamais porté, et rien ne le disait à qui l'avait
        téléversé.

        Le cas SVG est traité à part parce qu'il est le plus fréquent et le
        plus déroutant : le fichier est une image parfaitement valide, elle
        s'affiche partout ailleurs dans Odoo, et elle échoue seulement ici.
        Dire « ce n'est pas une image » serait faux et enverrait chercher au
        mauvais endroit.

        Rend `(image, raison)` : l'un des deux est toujours None.
        """
        self.ensure_one()
        from PIL import Image

        # ⚠️ `bin_size` : le client web lit les champs binaires en demandant
        # leur TAILLE LISIBLE plutôt que leur contenu, pour ne pas transporter
        # des mégaoctets dans un formulaire. Le champ rend alors b"32.99 Kb",
        # que tout traitement d'image prend pour des données et refuse. Le
        # défaut ne se voit QUE par le navigateur : en shell, le contexte n'est
        # pas posé et tout fonctionne. Il faut donc le forcer ici, au plus près
        # de la lecture, et non compter sur l'appelant.
        self = self.with_context(bin_size=False)
        raw = self.qr_logo or self._company().with_context(bin_size=False).logo
        if not raw:
            return None, _("No logo is available: the code comes out "
                           "unbranded.")
        octets = base64.b64decode(raw)
        entete = octets.lstrip()[:512]
        if entete[:5] == b"<?xml" or b"<svg" in entete:
            return None, _(
                "The logo is an SVG file, which the image generator "
                "cannot embed. Upload a PNG version in \"QR code logo\" "
                "next to it; the code comes out unbranded in the meantime."
            )
        try:
            return Image.open(io.BytesIO(octets)).convert("RGBA"), None
        except Exception as echec:  # noqa: BLE001
            _logger.warning("bf_linkpage: logo illisible (%s), QR sans marque", echec)
            return None, _(
                "The logo could not be read as an image. The code comes "
                "out unbranded."
            )
