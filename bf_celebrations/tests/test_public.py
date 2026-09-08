# -*- coding: utf-8 -*-
"""Les pages publiques : signer sans compte, et ce qui doit rester fermé."""

from datetime import datetime, timedelta

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install", "bf_celebrations")
class TestPagesPubliques(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employe = cls.env["hr.employee"].create({
            "name": "Solveig Marchetti",
            "work_email": "cel_public@example.test",
        })
        cls.tableau = cls.env["bf.celebration.board"].create({
            "name": "Bonne fête Solveig",
            "recipient_employee_id": cls.employe.id,
            "delivery_date": datetime.now() + timedelta(days=2),
            "state": "open",
        })
        cls.jeton = cls.tableau.sudo().access_token

    def test_la_page_s_ouvre_sans_compte(self):
        reponse = self.url_open("/celebration/%s" % self.jeton)
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("Bonne fête Solveig", reponse.text)
        # Une page de carte ne doit jamais se retrouver dans un index.
        self.assertEqual(
            reponse.headers.get("X-Robots-Tag"), "noindex, nofollow")
        self.assertIn("frame-ancestors 'none'",
                      reponse.headers.get("Content-Security-Policy", ""))

    def test_un_jeton_invente_ne_mene_nulle_part(self):
        reponse = self.url_open("/celebration/pasunjeton")
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("ne mène nulle part", reponse.text)

    def test_signer_sans_compte(self):
        reponse = self.url_open(
            "/celebration/%s/signer" % self.jeton,
            data={"author_name": "Une collègue",
                  "body": "Passe une belle journée."},
            timeout=30)
        self.assertEqual(reponse.status_code, 200)
        mots = self.tableau.sudo().post_ids
        self.assertEqual(len(mots), 1)
        self.assertEqual(mots.author_name, "Une collègue")
        self.assertEqual(mots.state, "published")
        # ⚠️ Le corps arrive du public : il ressort en texte mis en
        # paragraphes, jamais en HTML fourni par un anonyme.
        self.assertIn("<p>", mots.body)
        self.assertNotIn("<script", mots.body)

    def test_le_html_d_un_anonyme_ne_passe_pas(self):
        self.url_open(
            "/celebration/%s/signer" % self.jeton,
            data={"author_name": "Quelqu'un",
                  "body": "<script>alert(1)</script><b>gras</b>"},
            timeout=30)
        mot = self.tableau.sudo().post_ids.sorted("id")[-1]
        self.assertNotIn("<script", mot.body)
        self.assertNotIn("<b>", mot.body)
        self.assertIn("alert(1)", mot.body)  # échappé, donc inerte

    def test_un_mot_vide_est_refuse(self):
        avant = len(self.tableau.sudo().post_ids)
        self.url_open(
            "/celebration/%s/signer" % self.jeton,
            data={"author_name": "Personne", "body": "   "}, timeout=30)
        self.assertEqual(len(self.tableau.sudo().post_ids), avant)

    def test_la_moderation_retient_le_mot(self):
        self.tableau.sudo().moderation = True
        self.url_open(
            "/celebration/%s/signer" % self.jeton,
            data={"author_name": "Quelqu'un", "body": "Bonne fête"},
            timeout=30)
        mot = self.tableau.sudo().post_ids.sorted("id")[-1]
        self.assertEqual(mot.state, "pending")
        # Et il ne paraît pas sur la page tant qu'il n'est pas approuvé.
        page = self.url_open("/celebration/%s" % self.jeton)
        self.assertNotIn("Bonne fête</p>", page.text)

    def test_le_code_qr_se_sert(self):
        reponse = self.url_open("/celebration/%s/qr" % self.jeton)
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.headers.get("Content-Type"), "image/png")
        self.assertTrue(reponse.content.startswith(b"\x89PNG"))

    def test_un_tableau_annule_ferme_la_page(self):
        self.tableau.sudo().write({"state": "cancelled"})
        reponse = self.url_open("/celebration/%s" % self.jeton)
        self.assertIn("ne mène nulle part", reponse.text)
