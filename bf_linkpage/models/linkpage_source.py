"""Le registre des sources de liens.

C'est la pièce qui distingue ce module d'un service de pages de liens : un
lien n'est pas obligé d'être une chaîne recopiée à la main. Il peut nommer une
SOURCE, et l'URL est alors résolue à l'affichage depuis la base.

Conséquence recherchée : quand le slug de rendez-vous d'une personne change,
sa page suit sans que personne n'y touche, et le QR déjà imprimé dans sa
signature continue de pointer au bon endroit.

Deux règles gouvernent tout ce fichier.

1. AUCUN IMPORT VERS UN MODULE FOURNISSEUR. Les sources s'appuient sur
   `bf_appointment` et `bf_securetransfer`, mais ce module ne dépend d'aucun
   des deux et ne les importe jamais : il regarde si le modèle est au registre
   (`"modele" in self.env`) et se tait sinon. Un import, lui, ferait échouer
   l'installation là où le fournisseur est absent.

2. UNE SOURCE QUI NE RÉSOUT PAS N'AFFICHE RIEN. Elle ne rend pas une URL
   approximative et n'envoie personne vers une page d'accueil : le lien
   disparaît de la page publique. Un lien mort dans une page atteinte par QR
   coûte plus cher qu'un lien absent, parce que le QR, lui, est déjà imprimé.
"""

import logging

from odoo import api, models
from odoo.tools import LazyTranslate

# Les libellés du catalogue sont posés au chargement du module, sans langue :
# paresseux ici, traduits dans la langue de l'usager par `_selection`.
_lt = LazyTranslate(__name__)

_logger = logging.getLogger(__name__)


class BfLinkpageSource(models.AbstractModel):
    _name = "bf.linkpage.source"
    _description = "Dynamic link source registry"

    # ── Le catalogue ─────────────────────────────────────────────────────────

    @api.model
    def _sources(self):
        """Le catalogue des sources, dans l'ordre d'affichage.

        `provider` nomme le modèle qui doit exister pour que la source soit
        utilisable. `False` = la source ne dépend de rien d'installé.

        Un module satellite étend le catalogue en surchargeant cette méthode
        et en ajoutant son descripteur, puis en définissant la méthode
        `_resolve_<code>` correspondante.
        """
        return [
            {
                "code": "manual",
                "label": _lt("Manual address"),
                "provider": False,
                "help": "The URL is typed by hand in the link.",
            },
            {
                "code": "appointment",
                "label": _lt("Appointment booking"),
                "provider": "resource.booking.type",
                "help": "The person's public appointment page. Follows "
                        "their slug: if the slug changes, the link "
                        "changes with it.",
            },
            {
                "code": "securetransfer",
                "label": _lt("Secure upload"),
                "provider": "secure.transfer.brand",
                "help": "The /to/<slug> file upload page, so that someone "
                        "can send documents without an attachment.",
            },
            {
                "code": "advisor_booking",
                "label": _lt("Appointment with the advisor"),
                "provider": "resource.booking.type",
                "help": "The appointment page of the advisor set on the "
                        "organization page. Unrelated to the "
                        "\"Appointment booking\" source, which reads the "
                        "person the page is ABOUT.",
            },
            {
                "code": "advisor_email",
                "label": _lt("Email the advisor"),
                "provider": False,
                "help": "mailto: of the advisor set on the organization page.",
            },
            {
                "code": "advisor_transfer",
                "label": _lt("Advisor's secure upload"),
                "provider": "secure.transfer.brand",
                "help": "The advisor's /to/<slug> upload page, so that "
                        "the organization can send documents without an "
                        "attachment.",
            },
            {
                "code": "org_portal",
                "label": _lt("Organization portal"),
                "provider": False,
                "help": "The client portal. ⚠️ Resolves ONLY if at least "
                        "one person in the organization has an account: "
                        "without an account, the link would only open a "
                        "login screen.",
            },
            {
                "code": "org_invoices",
                "label": _lt("Invoices on the portal"),
                "provider": "account.move",
                "help": "The organization's invoices on the portal. Same "
                        "conditions as the portal, plus at least one "
                        "posted invoice.",
            },
            {
                "code": "org_projects",
                "label": _lt("Projects on the portal"),
                "provider": "project.project",
                "help": "The organization's projects on the portal. Same "
                        "conditions as the portal, plus at least one "
                        "active project whose visibility is \"portal\".",
            },
            {
                "code": "guide",
                "label": _lt("User guide"),
                "provider": False,
                "help": "The user guide, in the page's language. The "
                        "address comes from the settings; without a "
                        "setting, the link is not shown rather than "
                        "pointing to a guessed address.",
            },
            {
                "code": "partner_email",
                "label": _lt("Contact email"),
                "provider": False,
                "help": "mailto: taken from the contact record.",
            },
            {
                "code": "partner_phone",
                "label": _lt("Contact phone (mobile first)"),
                "provider": False,
                "help": "The mobile number from the contact record, "
                        "otherwise its phone. Kept for existing pages; "
                        "prefer one of the sources below, which name the "
                        "number they serve instead of guessing it.",
            },
            {
                "code": "phone_work",
                "label": _lt("Office phone"),
                "provider": False,
                "help": "The work phone from the employee record, "
                        "otherwise the company's. It is the number people "
                        "put in a signature.",
            },
            {
                "code": "phone_mobile",
                "label": _lt("Mobile"),
                "provider": False,
                "help": "The mobile number from the contact record, "
                        "otherwise the one from the employee record.",
            },
            {
                "code": "phone_direct",
                "label": _lt("Direct line"),
                "provider": False,
                "help": "The \"phone\" field of the contact record, the "
                        "one kept for a personal line.",
            },
            {
                "code": "phone_tollfree",
                "label": _lt("Toll-free"),
                "provider": False,
                "help": "The company's toll-free number. ⚠️ It is stored "
                        "in the \"mobile\" field of the company record, "
                        "for lack of a dedicated field in Odoo.",
            },
            {
                "code": "meet",
                "label": _lt("Instant meeting"),
                "help": "The permanent room entered on the page.",
                "provider": False,
            },
            {
                "code": "social_linkedin",
                "label": _lt("LinkedIn"),
                "provider": False,
                "social": True,
                "icon": "fa-brands fa-linkedin",
                "help": "The person's profile if entered on their record, "
                        "otherwise the company page.",
            },
            {
                "code": "social_github",
                "label": _lt("GitHub"),
                "provider": False,
                "social": True,
                "icon": "fa-brands fa-github",
                "help": "The company's GitHub account.",
            },
            {
                "code": "social_instagram",
                "label": _lt("Instagram"),
                "provider": False,
                "social": True,
                "icon": "fa-brands fa-instagram",
                "help": "The company's Instagram account.",
            },
            {
                "code": "social_facebook",
                "label": _lt("Facebook"),
                "provider": False,
                "social": True,
                "icon": "fa-brands fa-facebook",
                "help": "The company's Facebook page.",
            },
            {
                "code": "social_youtube",
                "label": _lt("YouTube"),
                "provider": False,
                "social": True,
                "icon": "fa-brands fa-youtube",
                "help": "The company's YouTube channel.",
            },
            {
                "code": "social_twitter",
                "label": _lt("X"),
                "provider": False,
                "social": True,
                "icon": "fa-brands fa-x-twitter",
                "help": "The company's X account. The Odoo field is still "
                        "called `social_twitter`.",
            },
            {
                "code": "partner_website",
                "label": _lt("Contact website"),
                "provider": False,
                "help": "The \"website\" field of the contact record.",
            },
        ]

    @api.model
    def _selection(self):
        """La sélection offerte au champ `source_code` d'un lien.

        Le catalogue entier est offert, fournisseur installé ou non. Retirer
        une option ferait DISPARAÎTRE la valeur des liens déjà enregistrés le
        jour où un module est désinstallé, et Odoo afficherait une case vide
        sans dire pourquoi. On préfère offrir l'option et la marquer
        indisponible à l'écran.
        """
        # Le libellé est lu HORS de l'appel : l'extracteur d'Odoo prend toute chaîne
        # écrite entre les parenthèses de `_()`, et `_(s["label"])` mettait « label »
        # au catalogue.
        sources = [(s["code"], s["label"]) for s in self._sources()]
        return [(code, self.env._(libelle)) for code, libelle in sources]

    @api.model
    def _social_codes(self):
        """Les codes qui s'affichent en rangée d'icônes plutôt qu'en carte."""
        return {s["code"] for s in self._sources() if s.get("social")}

    @api.model
    def _default_icon(self, code):
        """L'icône attachée au code, quand le lien n'en porte pas."""
        for source in self._sources():
            if source["code"] == code:
                return source.get("icon") or False
        return False

    @api.model
    def _available_codes(self):
        """Les codes dont le fournisseur est effectivement au registre."""
        return {
            s["code"]
            for s in self._sources()
            if not s["provider"] or s["provider"] in self.env
        }

    # ── La résolution ────────────────────────────────────────────────────────

    @api.model
    def _resolve(self, code, link):
        """Rendre l'URL d'un lien, ou False si elle ne se résout pas.

        Le nom commence par un tiret bas : une méthode publique sur un modèle
        est appelable par RPC, et rien ici n'a à l'être.
        """
        if not code:
            return False
        if code not in self._available_codes():
            return False
        if code in self._SOCIAL_COMPANY_FIELDS:
            return self._resolve_social(code, link)
        handler = getattr(self, "_resolve_%s" % code, None)
        if handler is None:
            return False
        # Le point de reprise est posé AVANT l'appel, pas dans le rattrapage.
        # Un résolveur qui meurt au milieu d'une requête laisse le curseur en
        # erreur : sans point de reprise antérieur, tout ce qui suit sur la
        # page publique échoue aussi. Poser un SAVEPOINT une fois l'exception
        # levée arriverait trop tard et sur une transaction déjà morte.
        try:
            with self.env.cr.savepoint():
                return handler(link) or False
        except Exception:  # noqa: BLE001
            _logger.warning(
                "bf_linkpage: la source %s n'a pas résolu le lien %s",
                code, link.id, exc_info=True,
            )
            return False

    # -- les résolveurs, un par code -----------------------------------------

    @api.model
    def _resolve_manual(self, link):
        return link.url

    @api.model
    def _resolve_appointment(self, link):
        """La page de rendez-vous publique de la personne de la page.

        Le chemin passe par la ressource : un type de rendez-vous n'a pas de
        propriétaire, il a des combinaisons de ressources, et c'est la
        ressource qui porte l'utilisateur. On ne rend qu'un type PUBLIÉ avec un
        slug : un type interne n'a rien à faire sur une page publique.
        """
        page = link.page_id
        booking_type = link._source_record("resource.booking.type")
        if not booking_type and page.booking_slug:
            # Le choix explicite de la personne passe avant toute recherche.
            booking_type = self.env["resource.booking.type"].sudo().search(
                [("slug", "=", page.booking_slug)], limit=1
            )
        if not booking_type:
            user = page.user_id
            if not user:
                return False
            resources = self.env["resource.resource"].sudo().search(
                [("user_id", "=", user.id)]
            )
            if not resources:
                return False
            booking_type = self.env["resource.booking.type"].sudo().search(
                [
                    ("is_public", "=", True),
                    ("slug", "!=", False),
                    ("combination_rel_ids.combination_id.resource_ids", "in", resources.ids),
                ],
                order="sequence, id",
                limit=1,
            )
        if not booking_type or not booking_type.is_public or not booking_type.slug:
            return False
        return "%s/appointment/%s" % (page._base_url(), booking_type.slug)

    @api.model
    def _resolve_securetransfer(self, link):
        """La page de dépôt /to/<slug>.

        Trois chemins, du plus sûr au plus fragile :

        1. La référence explicite posée sur le lien.
        2. Le PROPRIÉTAIRE : `secure.transfer.brand.owner_user_id` désigne la
           personne, c'est un fait de la base et non une convention.
        3. La marque dont le slug égale celui de la page.

        Le 3 était seul à l'origine, et il a échoué en silence dès la première
        page créée automatiquement : une marque en service porte souvent le
        seul prénom, tandis que la page générée depuis la fiche employé porte
        « prénom-nom ». Deux chaînes qui ne s'égalent pas, un lien qui
        disparaît, et rien à lire pour comprendre. Un rapprochement par propriétaire ne se casse pas en
        renommant une page.
        """
        page = link.page_id
        Brand = self.env["secure.transfer.brand"].sudo()
        brand = link._source_record("secure.transfer.brand")
        if not brand and page.user_id and "owner_user_id" in Brand._fields:
            brand = Brand.search([("owner_user_id", "=", page.user_id.id)], limit=1)
        if not brand:
            brand = Brand.search([("slug", "=", page.slug)], limit=1)
        if not brand or not brand.slug:
            return False
        return "%s/to/%s" % (page._base_url(), brand.slug)

    # -- ce qui tient à l'organisation ---------------------------------------

    @api.model
    def _org_partner_ids(self, page):
        """La fiche de l'organisation ET ses contacts.

        Un compte portail, une facture et un projet ne se rattachent pas tous
        au même endroit : le compte pend au contact, la facture parfois à la
        société, parfois au contact qui commande. Chercher sur la seule fiche
        mère fait disparaître des liens qui existent pourtant.
        """
        partner = page.partner_id
        if not partner:
            return []
        return [partner.id] + partner.sudo().child_ids.ids

    @api.model
    def _org_has_portal(self, page):
        """Vrai si quelqu'un de l'organisation peut ENTRER au portail.

        🔴 C'est la condition qui évite le pire lien d'un accueil client : un
        bouton « Votre portail » qui n'ouvre qu'un écran de connexion. Mesuré
        sur une base réelle : les projets rendus visibles au portail se
        comptent par centaines, et une organisation cliente sur cinq a un
        compte pour y entrer. Sans cette garde, la grande majorité des pages
        auraient affiché une porte sans clé.
        """
        cibles = self._org_partner_ids(page)
        if not cibles:
            return False
        return bool(self.env["res.users"].sudo().search_count([
            ("share", "=", True),
            ("active", "=", True),
            ("partner_id", "in", cibles),
        ]))

    @api.model
    def _resolve_org_portal(self, link):
        page = link.page_id
        if not self._org_has_portal(page):
            return False
        return "%s/my/home" % page._base_url()

    @api.model
    def _resolve_org_invoices(self, link):
        page = link.page_id
        if not self._org_has_portal(page):
            return False
        factures = self.env["account.move"].sudo().search_count([
            ("move_type", "in", ("out_invoice", "out_refund")),
            ("state", "=", "posted"),
            ("partner_id", "in", self._org_partner_ids(page)),
        ])
        return "%s/my/invoices" % page._base_url() if factures else False

    @api.model
    def _resolve_org_projects(self, link):
        page = link.page_id
        if not self._org_has_portal(page):
            return False
        projets = self.env["project.project"].sudo().search_count([
            ("active", "=", True),
            ("privacy_visibility", "=", "portal"),
            ("partner_id", "in", self._org_partner_ids(page)),
        ])
        return "%s/my/projects" % page._base_url() if projets else False

    # -- ce qui tient au conseiller ------------------------------------------

    @api.model
    def _advisor(self, link):
        """Le conseiller de la page, ou un ensemble vide."""
        return link.page_id.advisor_user_id

    @api.model
    def _resolve_advisor_booking(self, link):
        """La page de rendez-vous publique du conseiller.

        Même chemin que `_resolve_appointment` (le type ne connaît pas de
        propriétaire, c'est la ressource qui porte l'utilisateur), mais appuyé
        sur le conseiller de la page plutôt que sur la personne dont la page
        parle. Sur une page d'organisation, cette personne n'existe pas.

        🔴 **Et ici, on ne devine pas.** `_resolve_appointment` prend le premier
        type public par séquence quand rien ne le désigne ; sur une page
        personnelle c'est acceptable, parce que la personne relit sa propre
        page. Sur une page d'organisation, ce repli a offert une rencontre
        exploratoire à un client EXISTANT, vu en jouant une page de
        démonstration sur une base réelle : une instance en service porte
        souvent des dizaines de types publics, et la séquence 1 est la prise de
        contact. Le type se nomme donc sur la page, ou le lien disparaît. Un
        lien absent se voit au back-office ; une invitation à se présenter
        envoyée à un client de deux ans ne se rattrape pas.
        """
        page = link.page_id
        booking_type = link._source_record("resource.booking.type")
        if not booking_type and page.booking_slug:
            booking_type = self.env["resource.booking.type"].sudo().search(
                [("slug", "=", page.booking_slug)], limit=1
            )
        if not booking_type:
            return False
        if not self._advisor(link):
            return False
        if not booking_type.is_public or not booking_type.slug:
            return False
        return "%s/appointment/%s" % (page._base_url(), booking_type.slug)

    @api.model
    def _resolve_advisor_email(self, link):
        user = self._advisor(link)
        courriel = user.sudo().partner_id.email if user else False
        return "mailto:%s" % courriel if courriel else False

    @api.model
    def _resolve_advisor_transfer(self, link):
        page = link.page_id
        user = self._advisor(link)
        if not user:
            return False
        Brand = self.env["secure.transfer.brand"].sudo()
        brand = link._source_record("secure.transfer.brand")
        if not brand and "owner_user_id" in Brand._fields:
            brand = Brand.search([("owner_user_id", "=", user.id)], limit=1)
        if not brand or not brand.slug:
            return False
        return "%s/to/%s" % (page._base_url(), brand.slug)

    # -- le guide ------------------------------------------------------------

    @api.model
    def _resolve_guide(self, link):
        """L'adresse du guide, dans la langue de la page.

        Elle se lit dans les réglages, jamais dans une constante : un lien
        déduit enverrait le client d'un autre locataire vers NOTRE guide, et
        `bf_guide`, quand il est là, porte déjà l'adresse de l'instance. Pas
        de réglage, pas de lien : c'est mieux qu'une adresse devinée, qui
        finirait sur un écran de connexion sans que personne le voie.
        """
        get = self.env["ir.config_parameter"].sudo().get_param
        langue = (self.env.context.get("lang") or "fr_CA")[:2].lower()
        code = "en" if langue == "en" else "fr"
        for cle in ("bf_linkpage.guide_url_%s" % code, "bf_guide.url_%s" % code):
            valeur = get(cle)
            if valeur:
                return valeur.strip()
        return False

    @api.model
    def _resolve_partner_email(self, link):
        partner = link.page_id.partner_id
        return "mailto:%s" % partner.email if partner and partner.email else False

    @api.model
    def _resolve_partner_phone(self, link):
        partner = link.page_id.partner_id
        if not partner:
            return False
        number = partner.mobile or partner.phone
        if not number:
            return False
        # tel: n'accepte ni espace ni ponctuation de présentation.
        cleaned = "".join(c for c in number if c.isdigit() or c == "+")
        return "tel:%s" % cleaned if cleaned else False

    # Le champ de `res.company` derrière chaque code. Un seul résolveur les
    # sert tous : six méthodes identiques à un nom de champ près finissent
    # toujours par diverger sur cinq d'entre elles.
    _SOCIAL_COMPANY_FIELDS = {
        "social_linkedin": "social_linkedin",
        "social_github": "social_github",
        "social_instagram": "social_instagram",
        "social_facebook": "social_facebook",
        "social_youtube": "social_youtube",
        "social_twitter": "social_twitter",
    }

    @api.model
    def _resolve_social(self, code, link):
        """L'adresse d'un réseau, de la plus personnelle à la plus générique.

        LinkedIn d'abord sur la fiche de la personne (`x_linkedin_url`), parce
        qu'un profil personnel vaut mieux qu'une page d'entreprise sur une page
        qui porte un nom et une photo. Les autres n'existent qu'au niveau de
        l'entreprise dans Odoo.

        Rien n'est saisi sur le lien : l'adresse est LUE à l'affichage. C'est ce
        qui rend ces lignes posables par un gabarit, alors qu'une adresse tapée
        à la main serait effacée au prochain rafraîchissement.
        """
        page = link.page_id
        if code == "social_linkedin":
            partner = page.partner_id
            if partner and "x_linkedin_url" in partner._fields and partner.x_linkedin_url:
                return partner.x_linkedin_url
        company = page.sudo()._company()
        field = self._SOCIAL_COMPANY_FIELDS.get(code)
        if not company or not field or field not in company._fields:
            return False
        return company[field] or False

    @staticmethod
    def _tel(numero):
        """Un numéro en lien `tel:`, ou False.

        `tel:` n'accepte ni espace ni ponctuation de présentation. On garde le
        `+` initial, qui porte l'indicatif international.
        """
        if not numero:
            return False
        propre = "".join(c for c in numero if c.isdigit() or c == "+")
        return "tel:%s" % propre if propre else False

    @api.model
    def _resolve_phone_work(self, link):
        page = link.page_id
        employe = page._employee()
        if employe and employe.work_phone:
            return self._tel(employe.work_phone)
        return self._tel(page.sudo()._company().phone)

    @api.model
    def _resolve_phone_mobile(self, link):
        page = link.page_id
        if page.partner_id.mobile:
            return self._tel(page.partner_id.mobile)
        employe = page._employee()
        return self._tel(employe.mobile_phone) if employe else False

    @api.model
    def _resolve_phone_direct(self, link):
        return self._tel(link.page_id.partner_id.phone)

    @api.model
    def _resolve_phone_tollfree(self, link):
        return self._tel(link.page_id.sudo()._company().mobile)

    @api.model
    def _resolve_meet(self, link):
        """La salle permanente, telle que saisie sur la page."""
        return link.page_id.meet_url or False

    @api.model
    def _resolve_partner_website(self, link):
        partner = link.page_id.partner_id
        return partner.website if partner and partner.website else False
