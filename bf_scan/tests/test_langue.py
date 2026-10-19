"""La langue des pages Numériser, éprouvée par HTTP.

La source du module est anglaise et le français vit dans `i18n/fr_CA.po`. La
coquille des pages portait `<html lang="fr-CA">` en dur : une page servie en
anglais s'annonçait française (lecteur d'écran, correcteur, traduction du
navigateur). Le contrôleur passe maintenant la langue de la requête au gabarit
(`lang_html`), sur chacune des trois pages : l'accueil, la facture et le
document.

Chaque essai ouvre la page dans le rôle d'un usager dont la langue est
épinglée (en_CA ou fr_CA), par la vraie route : session, droits, rendu.
"""

import html

from odoo.tests import tagged
from odoo.tests.common import HttpCase


@tagged("post_install", "-at_install")
class EssaiLangue(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_scan"], ["fr_CA"], overwrite=True)
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        groupes = [
            cls.env.ref("base.group_user").id,
            cls.env.ref("account.group_account_invoice").id,
            cls.env.ref("bf_contact_enrichment.group_bf_contact_enrich").id,
            cls.env.ref("base.group_partner_manager").id,
        ]
        cls.anglo = Users.create({
            "name": "Ann Langue", "login": "ann_langue_essai",
            "password": "ann_langue_essai", "email": "ann@langue.invalid",
            "lang": "en_CA", "groups_id": [(6, 0, groupes)],
        })
        cls.franco = Users.create({
            "name": "Fanny Langue", "login": "fanny_langue_essai",
            "password": "fanny_langue_essai", "email": "fanny@langue.invalid",
            "lang": "fr_CA", "groups_id": [(6, 0, groupes)],
        })

    def _page(self, login, route):
        self.authenticate(login, login)
        reponse = self.url_open(route)
        self.assertEqual(reponse.status_code, 200, route)
        return html.unescape(reponse.text)

    def test_accueil_anglais(self):
        """L'accueil d'un usager en_CA s'annonce en-CA et parle anglais."""
        page = self._page("ann_langue_essai", "/scan")
        self.assertIn('<html lang="en-CA"', page)
        self.assertIn("Take the photo, the rest files itself.", page)
        self.assertIn("Business card", page)
        self.assertNotIn("Carte d'affaires", page)
        self.assertNotIn('lang="fr-CA"', page)

    def test_accueil_francais(self):
        """L'accueil d'un usager fr_CA s'annonce fr-CA et parle français."""
        page = self._page("fanny_langue_essai", "/scan")
        self.assertIn('<html lang="fr-CA"', page)
        self.assertIn("Prenez la photo, le reste se range", page)
        self.assertIn("Carte d'affaires", page)
        self.assertNotIn("Business card", page)

    def test_page_facture_suit_la_langue(self):
        """La page de facture porte la langue de l'usager, dans les deux sens."""
        page = self._page("ann_langue_essai", "/scan/facture")
        self.assertIn('<html lang="en-CA"', page)
        self.assertIn("Scan an invoice", page)
        self.assertNotIn("Numériser une facture", page)
        page = self._page("fanny_langue_essai", "/scan/facture")
        self.assertIn('<html lang="fr-CA"', page)
        self.assertIn("Numériser une facture", page)
        self.assertNotIn("Scan an invoice", page)

    def test_page_document_suit_la_langue(self):
        """La page de document porte la langue de l'usager, dans les deux sens."""
        page = self._page("ann_langue_essai", "/scan/document")
        self.assertIn('<html lang="en-CA"', page)
        self.assertIn("Scan a document", page)
        self.assertNotIn("Numériser un document", page)
        page = self._page("fanny_langue_essai", "/scan/document")
        self.assertIn('<html lang="fr-CA"', page)
        self.assertIn("Numériser un document", page)
        self.assertNotIn("Scan a document", page)
