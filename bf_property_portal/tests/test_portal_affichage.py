"""Ce que les pages AFFICHENT : relevé de la campagne de captures du 2026-10-10.

Chaque essai part d'un défaut vu sur la démo. Les pages se rendaient, et les
essais d'avant passaient : ils cherchaient le bon contenu, pas la bonne forme.
"""
import html
import re
from datetime import timedelta

import pytz

from odoo import fields
from odoo.tests import HttpCase, tagged


def fil_ariane(html):
    """Le texte du fil d'Ariane du portail (`ol.o_portal_submenu`), balises retirées."""
    match = re.search(r'<ol[^>]*o_portal_submenu[^>]*>(.*?)</ol>', html, re.S)
    if not match:
        return ""
    return " ".join(re.sub(r"<[^>]+>", " ", match.group(1)).split())


@tagged("post_install", "-at_install")
class TestPortalAffichage(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat de l'affichage", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble de l'affichage", "organisation_id": cls.syndicat.id}
        )
        cls.unit = cls.env["bf.property.unit"].create(
            {"name": "501", "building_id": cls.building.id, "quote_part": 1000.0}
        )
        cls.partner = cls.env["res.partner"].create(
            {"name": "Affichage coproprio", "email": "affichage@example.invalid"}
        )
        cls.env["bf.property.ownership"].create(
            {"unit_id": cls.unit.id, "partner_id": cls.partner.id}
        )
        cls.user = cls.env["res.users"].create(
            {
                "name": "Affichage coproprio",
                "login": "affichage_owner",
                "password": "affichage_owner_pwd",
                "partner_id": cls.partner.id,
                "tz": "America/Montreal",
                "groups_id": [(6, 0, [cls.env.ref("base.group_portal").id])],
            }
        )
        cls.hall = cls.env["bf.property.common.area"].create(
            {"name": "Salle de l'affichage", "building_id": cls.building.id, "bookable": True}
        )
        voisin = cls.env["res.partner"].create({"name": "Voisin de l'affichage"})
        autre = cls.env["bf.property.unit"].create(
            {"name": "502", "building_id": cls.building.id, "quote_part": 0.0}
        )
        cls.env["bf.property.ownership"].create(
            {"unit_id": autre.id, "partner_id": voisin.id}
        )
        # 22 h UTC : 18 h ou 17 h à Montréal selon la saison, jamais 22 h.
        debut = (fields.Datetime.now() + timedelta(days=3)).replace(
            hour=22, minute=0, second=0, microsecond=0
        )
        cls.booking = cls.env["bf.property.booking"].create(
            {
                "common_area_id": cls.hall.id,
                "partner_id": voisin.id,
                "date_start": debut,
                "date_stop": debut + timedelta(hours=2),
            }
        )
        cls.announcement = cls.env["bf.property.announcement"].create(
            {
                "name": "Coupure d'eau mardi matin",
                "organisation_id": cls.syndicat.id,
                "audience": "all",
                "body": "<p>De 9 h à 11 h, colonne est.</p>",
                # La veille : la fenêtre se juge au jour de la LECTRICE (Montréal),
                # et le soir, le jour de l'UTC est déjà le lendemain.
                "date_start": fields.Date.context_today(cls.env.user) - timedelta(days=1),
            }
        )
        cls.announcement.action_publish()

    def _page(self, url):
        """Le HTML de la page, entités décodées : « Coupure d'eau » sort en
        `Coupure d&#39;eau`, et un essai qui chercherait le texte tel quel
        échouerait sur une page juste."""
        self.authenticate("affichage_owner", "affichage_owner_pwd")
        response = self.url_open(url)
        self.assertEqual(response.status_code, 200, url)
        return html.unescape(response.text)

    def test_busy_slots_are_shown_in_the_reader_time_zone(self):
        """🔴 « Ce qui est déjà pris » imprimait la date-heure UTC brute
        (« 2026-10-14 22:00:00 ») sous un tableau « Mes réservations » à l'heure
        de Montréal : deux heures différentes pour le même créneau."""
        texte = self._page("/my/property/bookings")
        brut = fields.Datetime.to_string(self.booking.date_start)
        self.assertNotIn(brut, texte, "créneau pris imprimé en UTC brut")
        local = pytz.utc.localize(self.booking.date_start).astimezone(
            pytz.timezone("America/Montreal")
        )
        self.assertIn(local.strftime("%H:%M"), texte, "créneau pris absent à l'heure locale")

    def test_every_page_names_itself_in_the_breadcrumb(self):
        """🔴 Le fil d'Ariane n'avait que l'icône d'accueil ; sur « Ma copropriété »,
        qui le place dans la barre de recherche, la page n'avait plus de titre."""
        attendus = {
            "/my/property": "Ma copropriété",
            "/my/property/announcements": "Ma copropriété Annonces",
            "/my/property/documents": "Ma copropriété Documents",
            "/my/property/requests": "Ma copropriété Mes demandes d'entretien",
            "/my/property/bookings": "Ma copropriété Espaces communs",
            "/my/property/deliveries": "Ma copropriété Colis et visiteurs",
        }
        for url, fil in attendus.items():
            self.assertEqual(fil_ariane(self._page(url)), fil, url)

    def test_the_home_page_shows_the_current_announcements(self):
        """Le contrôleur passait les annonces à l'accueil ; le gabarit ne les lisait pas."""
        texte = self._page("/my/property")
        self.assertIn("Annonces récentes", texte)
        self.assertIn("Coupure d'eau mardi matin", texte)
        self.assertIn("De 9 h à 11 h, colonne est.", texte)

    def test_a_refusal_is_not_shown_as_what_was_done(self):
        """🔴 Le motif d'un refus paraissait sous « Ce qui a été fait : »."""
        demande = self.env["bf.property.request"].create(
            {
                "organisation_id": self.syndicat.id,
                "building_id": self.building.id,
                "unit_id": self.unit.id,
                "requester_partner_id": self.partner.id,
                "category": "plumbing",
                "description": "Le robinet de ma cuisine fuit.",
                "portion_type": "private",
            }
        )
        demande.write({"resolution": "Partie privative : à la charge du copropriétaire."})
        demande.action_refuse()
        self.assertEqual(demande.state, "refused")
        texte = self._page("/my/property/requests")
        self.assertIn("Motif du refus", texte)
        self.assertNotIn("Ce qui a été fait", texte)

    def test_a_reopened_refusal_shows_no_stale_reason(self):
        """🔴 Rouverte, une demande refusée gardait son motif, et le portail le
        montrait sous « Ce qui a été fait : ». Le motif reste au bureau ; le
        portail ne montre la résolution que d'une demande réglée ou refusée."""
        demande = self.env["bf.property.request"].create(
            {
                "organisation_id": self.syndicat.id,
                "building_id": self.building.id,
                "unit_id": self.unit.id,
                "requester_partner_id": self.partner.id,
                "category": "plumbing",
                "description": "Le robinet de ma cuisine fuit.",
                "portion_type": "private",
            }
        )
        motif = "Partie privative : à la charge du copropriétaire."
        demande.write({"resolution": motif})
        demande.action_refuse()
        demande.action_reopen()
        self.assertEqual(demande.state, "submitted")
        self.assertEqual(demande.resolution, motif, "la réouverture vide l'historique")
        texte = self._page("/my/property/requests")
        self.assertIn(demande.name, texte, "la demande rouverte n'est pas sur la page")
        self.assertNotIn("Motif du refus", texte)
        self.assertNotIn("Ce qui a été fait", texte)
        self.assertNotIn(motif, texte)

    def test_the_register_badge_is_readable_on_the_website_theme(self):
        """🔴 `text-bg-light` sur un badge du site : le thème calcule une encre
        pâle contre un fond pâle, et « Registre » disparaissait."""
        piece = self.env["ir.attachment"].create(
            {"name": "declaration.pdf", "raw": b"%PDF-1.4 declaration",
             "mimetype": "application/pdf"}
        )
        document = self.env["bf.property.document"].create(
            {
                "name": "Déclaration de copropriété",
                "organisation_id": self.syndicat.id,
                "category": "declaration",
                "audience": "owners",
                "register_ack": True,
                "attachment_id": piece.id,
            }
        )
        document.action_publish()
        self.assertTrue(document.is_register_item)
        texte = self._page("/my/property/documents")
        badge = re.search(r'<span[^>]*class="([^"]*)"[^>]*>Registre</span>', texte)
        self.assertTrue(badge, "badge « Registre » absent")
        self.assertNotIn("text-bg-light", badge.group(1))

    def _en_anglais(self):
        """La lectrice lit en anglais, catalogue du module chargé tel qu'il est sur disque."""
        lang = self.env["res.lang"]._activate_lang("en_CA")
        self.env["ir.module.module"]._load_module_terms(
            ["bf_property_portal"], ["en_CA"], overwrite=True
        )
        # ⚠️ Le site ne sert que ses langues : sans en_CA parmi elles, la page
        # sortait en en_US (sans traduction, donc la source française). Les sites
        # des locataires l'ont ; celui d'une base neuve (la CI) n'a que en_US.
        if "website" in self.env:
            for site in self.env["website"].search([]):
                site.language_ids |= lang
        self.user.lang = "en_CA"

    def test_the_new_labels_read_in_english(self):
        """Les deux libellés neufs sont au catalogue : sans quoi la page anglaise
        retombe sur le français pour eux seuls."""
        self._en_anglais()
        demande = self.env["bf.property.request"].create(
            {
                "organisation_id": self.syndicat.id,
                "building_id": self.building.id,
                "unit_id": self.unit.id,
                "requester_partner_id": self.partner.id,
                "category": "plumbing",
                "description": "Kitchen tap leaking.",
                "portion_type": "private",
                "resolution": "Private portion.",
            }
        )
        demande.action_refuse()
        self.assertIn("Recent announcements", self._page("/my/property"))
        texte = self._page("/my/property/requests")
        self.assertIn("Reason for refusal", texte)
        self.assertNotIn("Motif du refus", texte)

    def test_the_breadcrumb_reads_in_english(self):
        """🔴 Les entrées du fil d'Ariane vivent dans une vue neuve
        (`portal_breadcrumbs_property`). Odoo range les traductions de vue par
        xmlid : un libellé traduit sous une autre vue ne s'y applique pas. Sans
        ses propres occurrences au catalogue, la page anglaise qui place le fil
        dans la barre de recherche perdait son titre au profit du fil français."""
        self._en_anglais()
        attendus = {
            "/my/property": "My co-ownership",
            "/my/property/announcements": "My co-ownership Announcements",
            "/my/property/documents": "My co-ownership Documents",
            "/my/property/requests": "My co-ownership My maintenance requests",
            "/my/property/bookings": "My co-ownership Common spaces",
            "/my/property/deliveries": "My co-ownership Parcels and visitors",
        }
        for url, fil in attendus.items():
            self.assertEqual(fil_ariane(self._page(url)), fil, url)

    def test_the_pinned_mark_reads_in_english(self):
        """L'infobulle de l'épingle, à l'accueil, est une occurrence de la vue
        `portal_property_home` : même règle que le fil d'Ariane."""
        self.announcement.pinned = True
        self._en_anglais()
        texte = self._page("/my/property")
        self.assertIn('title="Pinned"', texte)
        self.assertNotIn('title="Épinglée"', texte)
