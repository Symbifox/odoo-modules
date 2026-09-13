# -*- coding: utf-8 -*-
"""La route est publique au sens d'Odoo : elle doit donc se garder elle-même."""
from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install", "bf_org_chart")
class TestControleurOrganigramme(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        P = cls.env["res.partner"]
        cls.holding = P.create({"name": "Holding d'essai", "is_company": True})
        cls.filiale = P.create({"name": "Filiale d'essai", "is_company": True})
        cls.env["bf.ownership"].create({
            "owner_id": cls.holding.id, "owned_id": cls.filiale.id, "percent": 100})

    def test_page_rend_un_svg(self):
        self.authenticate("admin", "admin")
        reponse = self.url_open(
            "/bf/organigramme/res.partner/%s/detention" % self.filiale.id)
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("<svg", reponse.text)
        self.assertIn("Holding d&#39;essai", reponse.text.replace("'", "&#39;"))

    def test_pdf_est_servi_en_piece_jointe(self):
        self.authenticate("admin", "admin")
        reponse = self.url_open(
            "/bf/organigramme/res.partner/%s/detention/pdf" % self.filiale.id)
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.headers["Content-Type"], "application/pdf")
        self.assertTrue(reponse.content.startswith(b"%PDF-"))

    def test_modele_qui_n_a_pas_signe_le_contrat_est_refuse(self):
        """Un nom de modèle dans l'URL ne doit pas ouvrir la base."""
        self.authenticate("admin", "admin")
        utilisateur = self.env.ref("base.user_admin")
        reponse = self.url_open(
            "/bf/organigramme/res.users/%s/detention" % utilisateur.id)
        self.assertEqual(reponse.status_code, 404)

    def test_genre_inconnu_est_refuse(self):
        self.authenticate("admin", "admin")
        reponse = self.url_open(
            "/bf/organigramme/res.partner/%s/inexistant" % self.filiale.id)
        self.assertEqual(reponse.status_code, 404)

    def test_sans_session_on_est_renvoye_a_la_connexion(self):
        reponse = self.url_open(
            "/bf/organigramme/res.partner/%s/detention" % self.filiale.id,
            allow_redirects=False)
        self.assertIn(reponse.status_code, (302, 303))
        self.assertIn("/web/login", reponse.headers.get("Location", ""))
