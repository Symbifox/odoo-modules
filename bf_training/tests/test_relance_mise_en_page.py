"""La relance se range avec les autres courriels de la société.

Relevé sur une vraie relance reçue chez Blue Fox : Verdana, gris d'Odoo, petit
libellé « Votre Assignation de formation », bouton #729daf hors palette, pendant
que tous les autres courriels de la maison portent le bandeau anthracite,
l'accent de marque et Lexend. La cause : la mise en page légère d'Odoo et la
couleur native des boutons, choisies sans regarder ce que la société emploie.

Deux cas, parce que le module part aussi chez des organisations qui n'ont pas
`bluefox_branding` : les essais du cas maison se sautent sans lui, et le cas
Odoo se joue partout en retirant la mise en page maison de la liste.
"""
import re
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged

MAISON = "bluefox_branding.bf_mail_layout"
ODOO = "mail.mail_notification_light"


@tagged("post_install", "-at_install", "bf_training")
class TestRelanceMiseEnPage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.societe = cls.env.company
        cls.societe.write({"email": "registre@exemple.test", "name": "Société Essai Marque",
                           "email_secondary_color": "#729daf"})
        cls.categorie = cls.env["bf.training.category"].create(
            {"name": "Catégorie marque", "code": "QA_MARQUE"})
        cls.partenaire = cls.env["res.partner"].create(
            {"name": "Apprenant Marque", "email": "apprenant.marque@exemple.test"})
        cls.activite = cls.env["bf.training.activity"].create({
            "name": "Secourisme", "category_id": cls.categorie.id,
            "mode": "elearning", "duration_hours": 2.0})
        cls.maison_installee = bool(cls.env.ref(MAISON, raise_if_not_found=False))

    def _exiger_la_maison(self):
        if not self.maison_installee:
            self.skipTest("bluefox_branding n'est pas installé sur cette base")

    def _sans_la_maison(self):
        return patch.object(type(self.env["bf.training.assignment"]),
                            "_MISES_EN_PAGE_RELANCE", ("module_absent.mise_en_page", ODOO))

    def _relance(self):
        assignation = self.env["bf.training.assignment"].create({
            "partner_id": self.partenaire.id, "activity_id": self.activite.id,
            "due_date": fields.Date.context_today(self.partenaire) + timedelta(days=3)})
        dernier = self.env["mail.mail"].search([], order="id desc", limit=1).id or 0
        assignation._relancer()
        courriel = self.env["mail.mail"].search([("id", ">", dernier)], limit=1)
        self.assertTrue(courriel, "la relance doit produire un courriel")
        return courriel.body_html or ""

    @staticmethod
    def _fond_du_bouton(corps):
        trouve = re.search(r"background-color:\s*(#[0-9a-fA-F]{3,6});[^\"']*color:\s*#fff;",
                           corps)
        return trouve and trouve.group(1).lower()

    # -- la mise en page ------------------------------------------------------
    def test_la_premiere_mise_en_page_presente_est_retenue(self):
        with self._sans_la_maison():
            self.assertEqual(self.env["bf.training.assignment"]._mise_en_page_relance(), ODOO)

    def test_sans_la_maison_la_relance_part_dans_la_mise_en_page_d_odoo(self):
        with self._sans_la_maison():
            corps = self._relance()
        self.assertNotIn("#F8FAFC", corps, "le fond de la mise en page maison")
        self.assertIn("Société Essai Marque", corps)

    def test_avec_la_maison_la_relance_porte_le_bandeau_de_marque(self):
        """🔴 Le défaut relevé : la relance était le seul courriel sans bandeau."""
        self._exiger_la_maison()
        # Une teinte que seule la mise en page maison lit : le fond de son bandeau.
        # ⚠️ Pas l'URL du logo : elle change selon les modules installés
        # (`/brand/logo/…` ou `/web/image/res.company/…`), le bandeau non.
        self.societe.report_brand_dark = "#123A4B"
        corps = self._relance()
        self.assertIn("#123A4B", corps, "le bandeau de marque de la mise en page maison")
        self.assertNotIn("Votre Assignation", corps,
                         "le libellé de la mise en page d'Odoo ne doit plus apparaître")

    # -- le bouton ------------------------------------------------------------
    def test_sans_la_maison_le_bouton_prend_la_couleur_des_boutons_de_courriel(self):
        with self._sans_la_maison():
            self.assertEqual(self._fond_du_bouton(self._relance()), "#729daf")

    def test_avec_la_maison_le_bouton_prend_l_accent_de_marque_brut(self):
        """🔴 #729daf hors palette à côté de courriels à l'accent de marque.

        ⚠️ L'accent BRUT, pas sa variante assombrie : le blanc sur l'accent est un
        arbitrage du propriétaire de la marque, et le bouton de la mise en page
        maison fait pareil.
        """
        self._exiger_la_maison()
        self.societe.report_brand_primary = "#29ABE2"
        self.assertEqual(self._fond_du_bouton(self._relance()), "#29abe2")

    # -- la signature ---------------------------------------------------------
    def test_sans_signature_de_societe_la_relance_signe_du_nom(self):
        with self._sans_la_maison():
            corps = self._relance()
        self.assertRegex(corps, r"Merci,\s*<br\s*/?>\s*Société Essai Marque")

    def test_avec_la_signature_maison_la_relance_ne_signe_pas_deux_fois(self):
        """« Merci, Société Exemple » suivi du bloc de signature de la même société."""
        self._exiger_la_maison()
        self.societe.brand_email_signature_default = "<p>Signature maison QA-7788</p>"
        corps = self._relance()
        self.assertIn("Signature maison QA-7788", corps)
        self.assertRegex(corps, r"Merci,\s*</p>")
