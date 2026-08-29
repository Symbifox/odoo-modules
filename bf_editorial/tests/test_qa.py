# -*- coding: utf-8 -*-
"""Les contrôles déterministes doivent trouver ce qu'ils annoncent, et rien
de plus. Un contrôle qui crie au loup se fait désactiver."""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestEditorialQA(TransactionCase):

    def setUp(self):
        super().setUp()
        self.qa = self.env["bf.editorial.qa"]

    def test_emdash_detecte(self):
        findings = self.qa._check_content("<p>Un texte — coupé.</p>", "fr_CA")
        self.assertTrue(any("cadratin" in f for f in findings))

    def test_texte_propre_ne_signale_rien(self):
        html = (
            '<p>Un paragraphe correct.</p>'
            '<h2>Un titre</h2>'
            '<table><tr><th scope="col">Colonne</th></tr></table>'
            '<img src="/a.png" alt="Une description"/>'
        )
        self.assertEqual(self.qa._check_content(html, "fr_CA"), [])

    def test_cliche_francais(self):
        findings = self.qa._check_content(
            "<p>La bonne nouvelle, c'est que ça marche.</p>", "fr_CA",
        )
        self.assertTrue(any("bonne nouvelle" in f for f in findings))

    def test_cliche_anglais_pas_applique_au_francais(self):
        """Un texte français contenant « the good news » n'est pas contrôlé
        par le jeu anglais : les règles suivent la langue du créneau."""
        findings = self.qa._check_content(
            "<p>Le terme « the good news » est cité.</p>", "fr_CA",
        )
        self.assertFalse(any("good news" in f for f in findings))

    def test_cliche_anglais(self):
        findings = self.qa._check_content(
            "<p>Let's be honest about this.</p>", "en_CA",
        )
        self.assertTrue(any("let's be honest" in f for f in findings))

    def test_titre_vide(self):
        findings = self.qa._check_content("<h2><br></h2>", "fr_CA")
        self.assertTrue(any("H2 vide" in f for f in findings))

    def test_th_sans_scope(self):
        findings = self.qa._check_content("<table><tr><th>A</th></tr></table>", "fr_CA")
        self.assertTrue(any("scope" in f for f in findings))

    def test_th_avec_scope_passe(self):
        findings = self.qa._check_content(
            '<table><tr><th scope="col">A</th></tr></table>', "fr_CA",
        )
        self.assertFalse(any("scope" in f for f in findings))

    def test_thead_n_est_pas_un_th_sans_scope(self):
        """« th » est un préfixe littéral de « thead » : sans limite de mot,
        le motif prenait chaque <thead> pour un en-tête sans portée. Vécu
        sur le billet 239 : deux <thead>, deux faux constats, alors que les
        six <th> réels portaient tous scope="col"."""
        findings = self.qa._check_content(
            '<table><thead><tr>'
            '<th scope="col">A</th><th scope="col">B</th>'
            '</tr></thead></table>', "fr_CA",
        )
        self.assertFalse(any("scope" in f for f in findings))

    def test_image_sans_alt(self):
        findings = self.qa._check_content('<img src="/a.png"/>', "fr_CA")
        self.assertTrue(any("alternatif" in f for f in findings))

    def test_marqueur_oublie(self):
        findings = self.qa._check_content("<p>a</p><!-- IMAGE: capture -->", "fr_CA")
        self.assertTrue(any("Marqueur" in f for f in findings))

    def test_couleur_faible_contraste(self):
        findings = self.qa._check_content(
            '<p style="color:#29ABE2">texte</p>', "fr_CA",
        )
        self.assertTrue(any("contraste" in f for f in findings))

    def test_ponctuation_francaise_dans_creneau_anglais(self):
        findings = self.qa._check_content(
            '<p><a href="/x">Source</a> : description</p>', "en_CA",
        )
        self.assertTrue(any("française" in f for f in findings))

    def test_creneau_vide(self):
        self.assertTrue(any("vide" in f for f in self.qa._check_content("", "fr_CA")))
