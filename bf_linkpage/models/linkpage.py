"""La page de liens elle-même."""

import base64
import io
import logging
import re
import unicodedata
from datetime import timedelta

from odoo import _, api, fields, models
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

# Part du côté occupée par le logo incrusté au centre du QR, et la limite au-delà
# de laquelle la correction d'erreur de niveau H ne reconstruit plus le code.
# Mesurées au décodeur indépendant le 2026-08-30 : 34 % passe encore, 40 % non.
LOGO_RATIO = 0.22
LOGO_MAX_RATIO = 0.30


class BfLinkpage(models.Model):
    _name = "bf.linkpage"
    _description = "Page de liens"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(
        string="Titre",
        required=True,
        tracking=True,
        help="Le titre affiché en haut de la page publique.",
    )
    slug = fields.Char(
        string="Identifiant URL",
        required=True,
        copy=False,
        index=True,
        tracking=True,
        help="La partie de l'adresse après %s/. Minuscules, chiffres et "
             "traits d'union." % URL_PREFIX,
    )
    active = fields.Boolean(default=True)

    kind = fields.Selection(
        [
            ("owner", "Rattachée à une personne"),
            ("oneoff", "Ponctuelle"),
        ],
        string="Nature",
        default="owner",
        required=True,
        tracking=True,
        help="Une page rattachée vit aussi longtemps que la personne. Une page "
             "ponctuelle n'a pas de propriétaire : elle porte une expiration.",
    )

    partner_id = fields.Many2one(
        "res.partner",
        string="Contact",
        tracking=True,
        ondelete="cascade",
        help="La personne dont cette page rassemble les liens. Les sources "
             "dynamiques (courriel, téléphone, site) sont lues sur cette fiche.",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Utilisateur",
        tracking=True,
        ondelete="set null",
        help="Nécessaire pour résoudre la page de rendez-vous de la personne, "
             "qui passe par sa ressource.",
    )

    template_id = fields.Many2one(
        "bf.linkpage.template",
        string="Gabarit",
        tracking=True,
        ondelete="set null",
        help="Le jeu de liens posé à la création. Appliquer un gabarit ÉCRASE "
             "les liens hérités mais laisse les liens ajoutés à la main.",
    )

    # -- présentation --------------------------------------------------------
    headline = fields.Char(
        string="Sous-titre",
        translate=True,
        help="Une ligne sous le titre. Le rôle, la ville, ce qu'on veut.",
    )
    bio = fields.Text(string="Présentation", translate=True)
    avatar = fields.Image(string="Photo", max_width=512, max_height=512)
    accent_color = fields.Char(
        string="Couleur d'accent",
        default="#29ABE1",
        help="Hexadécimal. Sert aux boutons de la page publique et au QR à la marque.",
    )
    layout = fields.Selection(
        [
            ("cards", "Cartes"),
            ("soft", "Cartes en relief"),
            ("minimal", "Épurée"),
            ("pills", "Boutons pleins"),
            ("mono", "Technique"),
        ],
        string="Disposition",
        default="cards",
        required=True,
        help="L'allure des liens. Indépendante du thème clair/sombre : les "
             "quatre dispositions existent dans les deux tons.",
    )
    theme = fields.Selection(
        [
            ("auto", "Selon l'appareil"),
            ("light", "Clair"),
            ("dark", "Sombre"),
        ],
        default="auto",
        required=True,
        string="Thème",
        help="« Selon l'appareil » suit la préférence système du visiteur, et "
             "lui laisse un bascule sur la page. Les deux autres imposent le "
             "thème, le bascule reste offert mais ne persiste que chez lui.",
    )
    show_theme_toggle = fields.Boolean(
        string="Offrir le bascule clair/sombre",
        default=True,
        help="Le choix du visiteur est gardé dans SON navigateur seulement. "
             "Rien n'est écrit ici, et rien ne le suit d'un appareil à l'autre.",
    )
    show_vcard = fields.Boolean(
        string="Offrir la carte de visite",
        default=True,
        help="Un bouton « Ajouter à mes contacts » sur la page publique. "
             "Sans effet sur une page ponctuelle, qui ne porte personne.",
    )
    show_company_logo = fields.Boolean(
        string="Afficher le logo de l'entreprise",
        default=True,
    )

    # -- cycle de vie --------------------------------------------------------
    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("published", "Publiée"),
            ("closed", "Fermée"),
        ],
        default="draft",
        required=True,
        tracking=True,
        string="État",
    )
    date_expiry = fields.Datetime(
        string="Expire le",
        tracking=True,
        copy=False,
        help="Passé cette date la page rend un 404. Vide = pas d'expiration.",
    )
    is_expired = fields.Boolean(
        string="Expirée",
        compute="_compute_is_expired",
        search="_search_is_expired",
        help="Calculé : l'expiration est une DATE, pas un état à maintenir. "
             "Aucun cron n'a besoin de tourner pour qu'une page se ferme.",
    )
    is_live = fields.Boolean(
        string="En ligne",
        compute="_compute_is_expired",
        help="Publiée, non archivée et non expirée.",
    )

    # Deux valeurs qui appartiennent à la PERSONNE, et qui vivent donc sur la
    # page et non sur ses liens. Un lien venu d'un gabarit est supprimé puis
    # recréé à chaque rafraîchissement : le personnaliser sur la page ne
    # survivrait pas au premier passage. Posées ici, elles survivent, et le
    # gabarit reste le même pour tout le monde.
    booking_slug = fields.Char(
        string="Type de rendez-vous",
        help="Le slug du type de rendez-vous à ouvrir, quand on ne veut pas "
             "celui que la recherche choisirait. Laisser vide pour prendre le "
             "premier type public rattaché à la ressource de la personne, ce "
             "qui n'est pas forcément celui qu'elle met dans sa signature.",
    )
    meet_url = fields.Char(
        string="Salle de rencontre instantanée",
        help="L'adresse d'une salle permanente (Talk, visioconférence). Elle "
             "n'est déduite d'aucun autre module : c'est une adresse propre à "
             "la personne, qu'on colle ici une fois.",
    )

    # La photo affichée n'est pas forcément CELLE de la page. On préfère une
    # chaîne de replis à une copie : copier l'image du contact la figerait le
    # jour où elle change, et une page de liens dont la photo date de deux ans
    # est exactement le genre de détail que personne ne pense à corriger.
    has_photo = fields.Boolean(compute="_compute_has_photo")

    # -- le code QR, réglable depuis la fiche --------------------------------
    qr_branded = fields.Boolean(
        string="Logo au centre",
        default=True,
        help="Le logo masque des modules du code. La correction d'erreur "
             "compense, et la part occupée est bornée à ce qui reste lisible.",
    )
    qr_logo = fields.Image(
        string="Logo du code QR",
        max_width=512,
        max_height=512,
        help="Laisser vide pour le logo de la société. Une image carrée donne "
             "le meilleur résultat : elle est redimensionnée sans être rognée.",
    )
    qr_scale = fields.Selection(
        [("s", "Petit (écran)"), ("m", "Moyen"), ("l", "Grand (impression)")],
        string="Taille",
        default="m",
        required=True,
    )
    qr_fill_color = fields.Char(
        string="Couleur du code",
        default="#000000",
        help="La couleur des modules. Elle doit rester nettement plus SOMBRE "
             "que le fond : un code clair sur fond foncé n'est pas lu par la "
             "plupart des appareils.",
    )
    qr_back_color = fields.Char(
        string="Fond du code",
        default="#FFFFFF",
    )
    qr_preview = fields.Image(
        string="Aperçu",
        compute="_compute_qr_preview",
        help="Rendu réel, avec les réglages ci-dessus. Ce que vous voyez ici "
             "est exactement ce qui sera téléchargé.",
    )
    qr_warning = fields.Char(compute="_compute_qr_preview")

    link_ids = fields.One2many("bf.linkpage.link", "page_id", string="Liens")
    link_count = fields.Integer(compute="_compute_link_count")
    visible_link_count = fields.Integer(
        string="Liens affichés",
        compute="_compute_link_count",
        help="Les liens dont la source résout réellement. Un écart avec le "
             "nombre total est la seule façon de voir qu'une source est muette.",
    )

    visit_count = fields.Integer(string="Visites", default=0, readonly=True, copy=False)
    last_visit = fields.Datetime(string="Dernière visite", readonly=True, copy=False)

    public_url = fields.Char(string="Adresse publique", compute="_compute_public_url")

    _sql_constraints = [
        ("slug_uniq", "unique(slug)", "Cet identifiant URL est déjà pris."),
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
            raise ValueError(_("Opérateur non supporté sur « Expirée »."))
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
                    "L'identifiant URL « %s » n'est pas valide : minuscules, "
                    "chiffres et traits d'union, sans commencer ni finir par "
                    "un trait d'union.", page.slug,
                ))
            if page.slug in RESERVED_SLUGS:
                raise ValidationError(_(
                    "L'identifiant URL « %s » est réservé.", page.slug,
                ))

    @api.constrains("kind", "partner_id")
    def _check_owner(self):
        for page in self:
            if page.kind == "owner" and not page.partner_id:
                raise ValidationError(_(
                    "Une page rattachée à une personne a besoin d'un contact. "
                    "Sans contact, ses sources dynamiques n'ont rien à lire."
                ))

    @api.constrains("accent_color")
    def _check_accent_color(self):
        for page in self:
            if page.accent_color and not re.match(r"^#[0-9A-Fa-f]{6}$", page.accent_color):
                raise ValidationError(_(
                    "La couleur d'accent doit être un hexadécimal à six chiffres, "
                    "par exemple #29ABE1."
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
                "L'identifiant URL « %(slug)s » est déjà pris par « %(name)s »%(etat)s.",
                slug=slug,
                name=collision.name,
                etat="" if collision.active else _(", une page archivée"),
            ))

    @api.model_create_multi
    def create(self, vals_list):
        vus = set()
        for vals in vals_list:
            if not vals.get("slug") and vals.get("name"):
                vals["slug"] = self._generate_slug(vals["name"])
            # Une page ponctuelle sans expiration est l'angle mort qu'on
            # refuse : elle reste ouverte parce que personne ne repasse. On
            # arme la date à la création plutôt que de compter sur un geste.
            if vals.get("kind") == "oneoff" and not vals.get("date_expiry"):
                vals["date_expiry"] = self._default_oneoff_expiry()
            if vals.get("slug"):
                if vals["slug"] in vus:
                    raise ValidationError(_(
                        "L'identifiant URL « %s » apparaît deux fois dans le "
                        "même envoi.", vals["slug"],
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
                "Couleur non reconnue : le code est rendu en noir sur blanc."
            )
        if l_fill >= l_back:
            return "#000000", "#FFFFFF", _(
                "Un code plus clair que son fond n'est pas lu par la plupart "
                "des appareils. Rendu en noir sur blanc."
            )
        rapport = (l_back + 0.05) / (l_fill + 0.05)
        if rapport < 4.0:
            return "#000000", "#FFFFFF", _(
                "Contraste insuffisant (%(r).1f:1, il en faut 4). Rendu en "
                "noir sur blanc.", r=rapport,
            )
        return fill, back, False

    @api.depends("slug", "qr_branded", "qr_logo", "qr_scale",
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
                page.qr_warning = _("Aperçu indisponible.")

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
                # Le cartouche prend la couleur du FOND du code, pas du blanc
                # en dur : sur un code à fond coloré, un carré blanc au centre
                # se voit comme une pièce rapportée.
                backdrop = Image.new("RGB", (side + 12, side + 12), back_color)
                backdrop.paste(logo, (6, 6), logo if logo.mode == "RGBA" else None)
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
            return None, _("Aucun logo n'est disponible : le code sort sans marque.")
        octets = base64.b64decode(raw)
        entete = octets.lstrip()[:512]
        if entete[:5] == b"<?xml" or b"<svg" in entete:
            return None, _(
                "Le logo est un fichier SVG, que le générateur d'images ne "
                "sait pas incruster. Téléversez une version PNG dans « Logo "
                "du code QR » ci-contre ; le code sort sans marque en attendant."
            )
        try:
            return Image.open(io.BytesIO(octets)).convert("RGBA"), None
        except Exception as echec:  # noqa: BLE001
            _logger.warning("bf_linkpage: logo illisible (%s), QR sans marque", echec)
            return None, _(
                "Le logo n'a pas pu être lu comme une image. Le code sort "
                "sans marque."
            )
