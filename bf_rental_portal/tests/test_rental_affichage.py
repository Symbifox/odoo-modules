"""Ce que les pages AFFICHENT : relevé de la campagne de captures du 2026-10-10."""
import re

from odoo import fields
from odoo.tests import HttpCase, tagged

from .test_rental_portal import RentalPortalCommon, flat


def fil_ariane(html):
    """Le texte du fil d'Ariane du portail (`ol.o_portal_submenu`), balises retirées."""
    match = re.search(r'<ol[^>]*o_portal_submenu[^>]*>(.*?)</ol>', html, re.S)
    if not match:
        return ""
    return " ".join(re.sub(r"<[^>]+>", " ", match.group(1)).split())


@tagged("post_install", "-at_install")
class TestRentalPortalAffichage(HttpCase, RentalPortalCommon):

    def test_every_page_names_itself_in_the_breadcrumb(self):
        """🔴 Les trois pages placent le fil d'Ariane dans la barre de recherche :
        sans entrée pour elles, il n'y avait que l'icône d'accueil, et la page
        n'avait plus de titre du tout."""
        self.authenticate("rt_tenant", "rt_tenant_pwd")
        for url, nom in [
            ("/my/rental", "Mon bail"),
            ("/my/rental/notices", "Les avis reçus"),
            ("/my/rental/rent", "Mon loyer"),
        ]:
            response = self.url_open(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertEqual(fil_ariane(response.text), nom, url)

    def test_an_information_notice_says_so_with_a_capital(self):
        """« cet avis informe », en minuscule dans une colonne où les autres
        lignes disent « Acceptation » ou « Refus »."""
        today = fields.Date.context_today(self.env.user)
        self.env["bf.rental.notice"].create(
            {
                "lease_id": self.lease.id,
                "kind": "new_tenant_rent",
                "date_given": today,
                "date_received": today,
                "state": "given",
            }
        )
        self.authenticate("rt_tenant", "rt_tenant_pwd")
        text = flat(self.url_open("/my/rental/notices").text)
        self.assertIn("Rien : l'avis informe seulement", text)
        self.assertNotIn("cet avis informe", text)

    def test_the_upcoming_term_badge_is_readable_on_the_website_theme(self):
        """🔴 `text-bg-light` sur un badge du site : encre pâle sur fond pâle."""
        self.env["bf.rental.term"].create(
            {"lease_id": self.lease.id,
             "date_due": fields.Date.add(fields.Date.context_today(self.env.user), months=1),
             "amount_due": 1200.0}
        )
        self.authenticate("rt_tenant", "rt_tenant_pwd")
        html = self.url_open("/my/rental/rent").text
        badge = re.search(r'<span[^>]*class="([^"]*badge[^"]*)"[^>]*>\s*À venir\s*</span>', html)
        self.assertTrue(badge, "badge « À venir » absent")
        self.assertNotIn("text-bg-light", badge.group(1))

    def _en_anglais(self):
        """La locataire lit en anglais, catalogue du module chargé tel qu'il est sur disque."""
        lang = self.env["res.lang"]._activate_lang("en_CA")
        self.env["ir.module.module"]._load_module_terms(
            ["bf_rental_portal"], ["en_CA"], overwrite=True
        )
        # ⚠️ Le site ne sert que ses langues : sans en_CA parmi elles, la page
        # sortait en en_US (sans traduction, donc la source française). Les sites
        # des locataires l'ont ; celui d'une base neuve (la CI) n'a que en_US.
        if "website" in self.env:
            for site in self.env["website"].search([]):
                site.language_ids |= lang
        self.tenant_user.lang = "en_CA"

    def test_the_information_notice_reads_in_english(self):
        self._en_anglais()
        today = fields.Date.context_today(self.env.user)
        self.env["bf.rental.notice"].create(
            {"lease_id": self.lease.id, "kind": "new_tenant_rent",
             "date_given": today, "date_received": today, "state": "given"}
        )
        self.authenticate("rt_tenant", "rt_tenant_pwd")
        text = flat(self.url_open("/my/rental/notices").text)
        self.assertIn("Nothing: the notice only informs", text)

    def test_the_breadcrumb_reads_in_english(self):
        """🔴 Les entrées du fil d'Ariane vivent dans une vue neuve
        (`portal_breadcrumbs_rental`). Odoo range les traductions de vue par
        xmlid : « Mon bail », traduit sous les autres vues, restait en français
        dans celle-ci, et c'est elle qui tient lieu de titre à ces trois pages."""
        self._en_anglais()
        self.authenticate("rt_tenant", "rt_tenant_pwd")
        for url, nom in [
            ("/my/rental", "My lease"),
            ("/my/rental/notices", "Notices received"),
            ("/my/rental/rent", "My rent"),
        ]:
            response = self.url_open(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertEqual(fil_ariane(response.text), nom, url)
