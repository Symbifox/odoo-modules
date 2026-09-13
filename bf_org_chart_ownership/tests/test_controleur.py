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
        # Un utilisateur d'une AUTRE société, et une fiche qu'il ne peut pas lire.
        cls.voisine = cls.env["res.company"].create({"name": "Voisine contrôleur"})
        cls.voisin = cls.env["res.users"].create({
            "name": "Voisin contrôleur", "login": "voisin_orgchart_ctrl",
            "password": "voisin_orgchart_2026!",
            "company_id": cls.voisine.id, "company_ids": [(6, 0, [cls.voisine.id])],
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])]})
        cls.fiche_fermee = P.create({
            "name": "Fiche d'une autre société", "is_company": True,
            "company_id": cls.env.company.id})

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

    def test_une_fiche_hors_perimetre_rend_404(self):
        """🔴 Le contrôle d'accès du contrôleur pouvait être retiré sans qu'un
        seul essai tombe : tous jouaient en administrateur. C'est l'essai qui
        tient `check_access`."""
        self.authenticate("voisin_orgchart_ctrl", "voisin_orgchart_2026!")
        reponse = self.url_open(
            "/bf/organigramme/res.partner/%s/detention" % self.fiche_fermee.id)
        self.assertEqual(reponse.status_code, 404)
        reponse = self.url_open(
            "/bf/organigramme/res.partner/%s/detention/pdf" % self.fiche_fermee.id)
        self.assertEqual(reponse.status_code, 404)

    def test_le_refus_arrive_AVANT_le_moindre_dessin(self):
        """🔴 Retirer `check_access` du contrôleur ne faisait tomber aucun
        essai : la lecture profonde refusait de toute façon, plus loin. Sauf
        que « plus loin », c'est après la recherche, la matérialisation et la
        boucle. La garde vaut par ce qu'elle ÉVITE, pas seulement par sa
        réponse : cet essai mesure donc qu'aucun dessin n'est construit."""
        from unittest.mock import patch
        self.authenticate("voisin_orgchart_ctrl", "voisin_orgchart_2026!")
        Source = type(self.env["res.partner"])
        with patch.object(Source, "_org_chart_plan", autospec=True,
                          side_effect=AssertionError("dessin construit malgré le refus")):
            reponse = self.url_open(
                "/bf/organigramme/res.partner/%s/detention" % self.fiche_fermee.id)
        self.assertEqual(reponse.status_code, 404,
                         "le refus doit sortir sans avoir rien dessiné")

    def test_une_fiche_inexistante_rend_404(self):
        self.authenticate("admin", "admin")
        reponse = self.url_open("/bf/organigramme/res.partner/99999999/detention")
        self.assertEqual(reponse.status_code, 404)
