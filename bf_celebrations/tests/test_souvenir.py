# -*- coding: utf-8 -*-
"""Ce qui reste quand la base n'a plus rien : le souvenir, la purge, et deux
petits défauts de la 1.0 qu'on ne veut pas revoir."""

import base64
from datetime import datetime, timedelta

from odoo.tests import HttpCase, TransactionCase, tagged

# Un GIF 1×1 valide, pour que Pillow l'ouvre vraiment.
GIF = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


@tagged("post_install", "-at_install", "bf_celebrations")
class TestSouvenir(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ⚠️ Les tests qui livrent rendent un PDF, et wkhtmltopdf va chercher
        # les feuilles de style à `report.url` ou `web.base.url`. Sur un banc
        # où cette adresse est le serveur de test lui-même, la requête attend
        # le curseur que le test tient : blocage mutuel, « Request timed out »,
        # et une passe qui ne finit jamais. Une adresse fermée fait échouer la
        # récupération tout de suite ; le PDF sort quand même, sans styles.
        cls.env["ir.config_parameter"].sudo().set_param(
            "report.url", "http://127.0.0.1:9")
        cls.usager = cls.env["res.users"].create({
            "name": "Rosalie Vachon", "login": "cel_sv@example.test",
            "email": "cel_sv@example.test",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.employe = cls.env["hr.employee"].create({
            "name": "Rosalie Vachon", "user_id": cls.usager.id,
            "work_email": "cel_sv@example.test",
        })
        cls.profil = cls.env["bf.celebration.profile"].sudo().create({
            "employee_id": cls.employe.id,
            "keepsake_email": "rosalie.perso@example.test",
        })

    def _tableau(self, **extra):
        valeurs = {
            "name": "Bon départ Rosalie",
            "recipient_employee_id": self.employe.id,
            "delivery_date": datetime.now() - timedelta(hours=1),
            "state": "open",
        }
        valeurs.update(extra)
        board = self.env["bf.celebration.board"].create(valeurs)
        self.env["bf.celebration.post"].sudo().create([
            {"board_id": board.id, "author_name": "Un collègue",
             "body": "<p>Merci pour tout.</p>"},
            {"board_id": board.id, "author_name": "Une autre",
             "body": "<p>À bientôt</p>", "style": "hand",
             "image": base64.b64encode(GIF)},
        ])
        return board

    def test_la_livraison_part_avec_ses_pieces(self):
        board = self._tableau()
        board._livrer()
        self.assertEqual(board.state, "delivered")
        pieces = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "bf.celebration.board"),
            ("res_id", "=", board.id)])
        noms = sorted(pieces.mapped("name"))
        self.assertEqual(noms, ["Carte - Rosalie Vachon.html",
                                "Carte - Rosalie Vachon.pdf"])
        courriel = self.env["mail.mail"].sudo().search(
            [("model", "=", "bf.celebration.board"),
             ("res_id", "=", board.id)], order="id desc", limit=1)
        self.assertTrue(courriel)
        self.assertEqual(set(courriel.attachment_ids.ids), set(pieces.ids))

    def test_l_adresse_personnelle_recoit_la_meme_livraison(self):
        board = self._tableau()
        board._livrer()
        courriel = self.env["mail.mail"].sudo().search(
            [("model", "=", "bf.celebration.board"),
             ("res_id", "=", board.id)], order="id desc", limit=1)
        self.assertIn("cel_sv@example.test", courriel.email_to)
        self.assertIn("rosalie.perso@example.test", courriel.email_to,
                      "L'adresse du profil doit recevoir la carte.")

    def test_la_page_souvenir_est_autonome(self):
        board = self._tableau()
        html = board._html_souvenir().decode("utf-8")
        self.assertTrue(html.lstrip().startswith("<!DOCTYPE html>"))
        self.assertNotIn("<script", html)
        self.assertIn("data:font/woff2;base64,", html,
                      "La police voyage dans le fichier.")
        self.assertIn("data:image/gif;base64,", html,
                      "L'image aussi, avec son vrai type.")
        self.assertNotIn("/celebration/", html,
                         "Aucun lien vers nous : la page vit sans le serveur.")
        self.assertIn("Merci pour tout.", html)
        self.assertIn("cel-manuscrit", html)
        # Les sélecteurs CSS ne sont pas échappés (le « > » survit).
        self.assertNotIn("&gt;", html.split("<style", 2)[-1].split("</style>")[0])

    def test_le_pdf_est_un_pdf(self):
        board = self._tableau()
        pdf = board._pdf_souvenir()
        self.assertTrue(pdf.startswith(b"%PDF"),
                        "Le rendu doit être forcé même en mode test.")

    def test_une_piece_trop_lourde_est_retenue_pas_le_courriel(self):
        from odoo.addons.bf_celebrations.models import celebration_board
        board = self._tableau()
        original = celebration_board.PIECES_JOINTES_MAX
        celebration_board.PIECES_JOINTES_MAX = 10
        try:
            board._livrer()
        finally:
            celebration_board.PIECES_JOINTES_MAX = original
        self.assertEqual(board.state, "delivered")
        pieces = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "bf.celebration.board"),
            ("res_id", "=", board.id)])
        self.assertFalse(pieces)
        self.assertTrue(any(
            "Trop lourd" in (m.body or "") for m in board.message_ids))

    def test_la_purge_est_eteinte_par_defaut(self):
        param = self.env["ir.config_parameter"].sudo()
        param.set_param("bf_celebrations.retention_months", "")
        board = self._tableau()
        board._livrer()
        board.sudo().delivered_date = datetime.now() - timedelta(days=900)
        self.assertEqual(
            self.env["bf.celebration.board"]._cron_purger_livres(), 0)
        self.assertTrue(board.exists())

    def test_la_purge_efface_la_carte_et_ses_pieces(self):
        param = self.env["ir.config_parameter"].sudo()
        param.set_param("bf_celebrations.retention_months", "6")
        vieille = self._tableau()
        vieille._livrer()
        vieille.sudo().delivered_date = datetime.now() - timedelta(days=200)
        recente = self._tableau(name="Bonne fête")
        recente._livrer()
        ouverte = self._tableau(name="Encore ouverte",
                                delivery_date=datetime.now() + timedelta(days=3))
        vieille_id = vieille.id
        nombre = self.env["bf.celebration.board"]._cron_purger_livres()
        self.assertEqual(nombre, 1)
        self.assertFalse(vieille.exists())
        self.assertTrue(recente.exists())
        self.assertTrue(ouverte.exists())
        self.assertFalse(self.env["bf.celebration.post"].sudo().search(
            [("board_id", "=", vieille_id)]))
        self.assertFalse(self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "bf.celebration.board"),
            ("res_id", "=", vieille_id)]))

    def test_le_code_qr_parait_sur_la_fiche(self):
        """La 1.0 annonçait le QR sur la fiche et ne l'affichait pas."""
        board = self._tableau()
        self.assertTrue(board.qr_image)
        self.assertTrue(base64.b64decode(board.qr_image).startswith(b"\x89PNG"))

    def test_un_tableau_vide_ne_previent_qu_une_fois(self):
        """Le cron passe toutes les heures : une activité par heure ensevelit."""
        board = self.env["bf.celebration.board"].create({
            "name": "Vide", "recipient_employee_id": self.employe.id,
            "delivery_date": datetime.now() - timedelta(hours=1),
            "state": "open",
        })
        Board = self.env["bf.celebration.board"]
        Board._cron_livrer()
        Board._cron_livrer()
        Board._cron_livrer()
        activites = self.env["mail.activity"].search([
            ("res_model", "=", "bf.celebration.board"),
            ("res_id", "=", board.id)])
        self.assertEqual(len(activites), 1)
        self.assertTrue(board.empty_notified)


@tagged("post_install", "-at_install", "bf_celebrations")
class TestSouvenirPublic(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ⚠️ Les tests qui livrent rendent un PDF, et wkhtmltopdf va chercher
        # les feuilles de style à `report.url` ou `web.base.url`. Sur un banc
        # où cette adresse est le serveur de test lui-même, la requête attend
        # le curseur que le test tient : blocage mutuel, « Request timed out »,
        # et une passe qui ne finit jamais. Une adresse fermée fait échouer la
        # récupération tout de suite ; le PDF sort quand même, sans styles.
        cls.env["ir.config_parameter"].sudo().set_param(
            "report.url", "http://127.0.0.1:9")
        cls.employe = cls.env["hr.employee"].create({
            "name": "Rosalie Vachon", "work_email": "cel_svp@example.test",
        })
        cls.board = cls.env["bf.celebration.board"].create({
            "name": "Bon départ Rosalie",
            "recipient_employee_id": cls.employe.id,
            "delivery_date": datetime.now() + timedelta(days=2),
            "state": "open",
        })
        cls.env["bf.celebration.post"].sudo().create({
            "board_id": cls.board.id, "author_name": "Un collègue",
            "body": "<p>Merci pour tout.</p>"})
        cls.jeton = cls.board.sudo().access_token

    def test_le_souvenir_attend_la_livraison(self):
        for route in ("pdf", "souvenir"):
            page = self.url_open("/celebration/%s/%s" % (self.jeton, route))
            self.assertIn("pas encore livrée", page.text, route)
        tableau = self.url_open("/celebration/%s/tableau" % self.jeton).text
        self.assertNotIn("cel-ouverture", tableau,
                         "Pas d'enveloppe avant la livraison.")
        self.assertNotIn("Garder cette carte", tableau)

    def test_apres_la_livraison_tout_se_garde(self):
        self.board.sudo().write({"state": "delivered",
                                 "delivered_date": datetime.now()})
        pdf = self.url_open("/celebration/%s/pdf" % self.jeton)
        self.assertEqual(pdf.headers.get("Content-Type"), "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertIn("attachment", pdf.headers.get("Content-Disposition", ""))
        page = self.url_open("/celebration/%s/souvenir" % self.jeton)
        self.assertIn("text/html", page.headers.get("Content-Type", ""))
        self.assertIn("attachment", page.headers.get("Content-Disposition", ""))
        self.assertIn("Merci pour tout.", page.text)
        tableau = self.url_open("/celebration/%s/tableau" % self.jeton).text
        self.assertIn('id="cel-ouverture"', tableau)
        self.assertIn("livraison.js", tableau)
        self.assertIn("Garder cette carte", tableau)
        self.assertIn("Rejouer l'ouverture", tableau)

    def test_un_gif_est_servi_comme_un_gif(self):
        mot = self.env["bf.celebration.post"].sudo().create({
            "board_id": self.board.id, "author_name": "Une autre",
            "image": base64.b64encode(GIF)})
        reponse = self.url_open(
            "/celebration/%s/image/%s" % (self.jeton, mot.id))
        self.assertEqual(reponse.headers.get("Content-Type"), "image/gif",
                         "Sous nosniff, un GIF annoncé PNG est un mensonge.")
