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

_logger = logging.getLogger(__name__)


class BfLinkpageSource(models.AbstractModel):
    _name = "bf.linkpage.source"
    _description = "Registre des sources de liens dynamiques"

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
                "label": "Adresse saisie",
                "provider": False,
                "help": "L'URL est écrite à la main dans le lien.",
            },
            {
                "code": "appointment",
                "label": "Prise de rendez-vous",
                "provider": "resource.booking.type",
                "help": "La page publique de rendez-vous de la personne. Suit "
                        "son slug : si le slug change, le lien change avec lui.",
            },
            {
                "code": "securetransfer",
                "label": "Dépôt sécurisé",
                "provider": "secure.transfer.brand",
                "help": "La page de dépôt de fichiers /to/<slug>, pour que "
                        "quelqu'un envoie des documents sans pièce jointe.",
            },
            {
                "code": "partner_email",
                "label": "Courriel du contact",
                "provider": False,
                "help": "mailto: tiré de la fiche du contact.",
            },
            {
                "code": "partner_phone",
                "label": "Téléphone du contact",
                "provider": False,
                "help": "tel: tiré de la fiche du contact (mobile en premier).",
            },
            {
                "code": "meet",
                "label": "Rencontre instantanée",
                "help": "La salle permanente saisie sur la page.",
                "provider": False,
            },
            {
                "code": "social_linkedin",
                "label": "LinkedIn",
                "provider": False,
                "social": True,
                "icon": "fa-brands fa-linkedin",
                "help": "Le profil de la personne s'il est saisi sur sa fiche, "
                        "sinon la page de l'entreprise.",
            },
            {
                "code": "social_github",
                "label": "GitHub",
                "provider": False,
                "social": True,
                "icon": "fa-brands fa-github",
                "help": "Le compte GitHub de l'entreprise.",
            },
            {
                "code": "social_instagram",
                "label": "Instagram",
                "provider": False,
                "social": True,
                "icon": "fa-brands fa-instagram",
                "help": "Le compte Instagram de l'entreprise.",
            },
            {
                "code": "social_facebook",
                "label": "Facebook",
                "provider": False,
                "social": True,
                "icon": "fa-brands fa-facebook",
                "help": "La page Facebook de l'entreprise.",
            },
            {
                "code": "social_youtube",
                "label": "YouTube",
                "provider": False,
                "social": True,
                "icon": "fa-brands fa-youtube",
                "help": "La chaîne YouTube de l'entreprise.",
            },
            {
                "code": "social_twitter",
                "label": "X",
                "provider": False,
                "social": True,
                "icon": "fa-brands fa-x-twitter",
                "help": "Le compte X de l'entreprise. Le champ Odoo s'appelle "
                        "encore `social_twitter`.",
            },
            {
                "code": "partner_website",
                "label": "Site web du contact",
                "provider": False,
                "help": "Le champ « site web » de la fiche du contact.",
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
        return [(s["code"], s["label"]) for s in self._sources()]

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

    @api.model
    def _resolve_meet(self, link):
        """La salle permanente, telle que saisie sur la page."""
        return link.page_id.meet_url or False

    @api.model
    def _resolve_partner_website(self, link):
        partner = link.page_id.partner_id
        return partner.website if partner and partner.website else False
