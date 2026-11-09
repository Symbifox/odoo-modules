# -*- coding: utf-8 -*-
from odoo.tests import TransactionCase, tagged

from ..models.color_utils import (
    DARK_TEXT, LIGHT_TEXT, PALETTE, RAMP_PLANCHER, brand_ramp, contrast_ratio, darken,
    lighten, normalize_hex, palette_color, relative_luminance, text_color,
)

# Minimum de la WCAG 2.1 pour un texte normal.
CONTRASTE_MINIMAL = 4.5


@tagged("post_install", "-at_install")
class TestCouleurs(TransactionCase):
    """La palette et le calcul de contraste, sans aucun modèle.

    Ces essais sont venus de `bf_wheel` avec le fichier qu'ils gardent : ce qui
    s'y mesure ne tient à aucune roue, à aucune équipe et à aucune diapositive.
    Les trois essais qui parlaient d'un segment sont restés chez la roue.
    """

    def test_chaque_couleur_de_la_palette_porte_une_etiquette_lisible(self):
        for couleur in PALETTE:
            encre = text_color(couleur)
            self.assertIn(encre, (DARK_TEXT, LIGHT_TEXT))
            self.assertGreaterEqual(
                contrast_ratio(couleur, encre), CONTRASTE_MINIMAL,
                "l'étiquette posée sur %s se lit sous le minimum" % couleur)

    def test_l_encre_choisie_est_la_meilleure_des_deux(self):
        for couleur in PALETTE:
            encre = text_color(couleur)
            autre = DARK_TEXT if encre == LIGHT_TEXT else LIGHT_TEXT
            self.assertGreaterEqual(contrast_ratio(couleur, encre), contrast_ratio(couleur, autre))

    def test_le_seuil_qui_circule_se_tromperait(self):
        """Le bleu de la maison : luminance 0,35, donc « clair » à un seuil de 0,5."""
        self.assertEqual(text_color("#29abe2"), DARK_TEXT)
        self.assertLess(contrast_ratio("#29abe2", LIGHT_TEXT), CONTRASTE_MINIMAL)

    def test_normalisation(self):
        self.assertEqual(normalize_hex("#ABC"), "#aabbcc")
        self.assertEqual(normalize_hex("  #29ABE2 "), "#29abe2")
        self.assertFalse(normalize_hex("bleu"))
        self.assertFalse(normalize_hex("#12345"))
        self.assertFalse(normalize_hex(""))
        self.assertFalse(normalize_hex(False))

    def test_la_palette_tourne(self):
        self.assertEqual(palette_color(0), PALETTE[0])
        self.assertEqual(palette_color(len(PALETTE)), PALETTE[0])
        self.assertEqual(palette_color(len(PALETTE) + 2), PALETTE[2])

    # ------------------------------------------------------------
    # LA RAMPE DE LA MARQUE
    # ------------------------------------------------------------

    def test_la_rampe_retrouve_le_podium_pose_a_la_main(self):
        """Les trois blocs du podium posés à la main, calculés à une unité près."""
        self.assertEqual(brand_ramp("#29abe2"), ("#29abe2", "#1b6f93", "#124860"))

    def test_chaque_marche_porte_une_encre_lisible(self):
        """Ce n'est PAS acquis d'une marche calculée : quatre teintes choisies
        d'instinct étaient tombées entre les deux encres, à 3,5:1."""
        for couleur in list(PALETTE) + ["#1f84af", "#000000", "#ffffff", "#3d5a80"]:
            for marche in brand_ramp(couleur):
                self.assertGreaterEqual(
                    contrast_ratio(marche, text_color(marche)), CONTRASTE_MINIMAL,
                    "la marche %s de la rampe de %s ne porte aucune encre lisible"
                    % (marche, couleur))

    def test_les_trois_marches_se_distinguent(self):
        for couleur in list(PALETTE) + ["#1f84af", "#000000", "#ffffff"]:
            rampe = brand_ramp(couleur)
            self.assertEqual(len(rampe), 3)
            self.assertEqual(len(set(rampe)), 3, "deux marches identiques pour %s" % couleur)
            for rang in range(2):
                self.assertGreater(
                    contrast_ratio(rampe[rang], rampe[rang + 1]), 1.25,
                    "les marches %s et %s de %s ne se distinguent pas"
                    % (rampe[rang], rampe[rang + 1], couleur))

    def test_une_teinte_presque_noire_fait_monter_la_rampe(self):
        """On ne distingue pas trois noirs : sous le plancher, la rampe monte."""
        self.assertLess(relative_luminance("#000000"), RAMP_PLANCHER)
        rampe = brand_ramp("#000000")
        self.assertEqual(rampe[0], "#000000")
        self.assertGreater(relative_luminance(rampe[1]), relative_luminance(rampe[0]))
        self.assertGreater(relative_luminance(rampe[2]), relative_luminance(rampe[1]))

    def test_une_teinte_claire_fait_descendre_la_rampe(self):
        rampe = brand_ramp("#f4a72c")
        for rang in range(2):
            self.assertLess(
                relative_luminance(rampe[rang + 1]), relative_luminance(rampe[rang]))

    def test_la_premiere_marche_est_poussee_si_elle_n_est_pas_lisible(self):
        """Le bleu de la société de la maison se lit à 4,46:1 : sous le minimum."""
        self.assertLess(contrast_ratio("#1f84af", text_color("#1f84af")), CONTRASTE_MINIMAL)
        rampe = brand_ramp("#1f84af")
        self.assertNotEqual(rampe[0], "#1f84af")
        self.assertGreaterEqual(
            contrast_ratio(rampe[0], text_color(rampe[0])), CONTRASTE_MINIMAL)

    def test_ce_qui_n_est_pas_une_couleur_rend_la_rampe_du_bleu_mesure(self):
        """Le repli est décidé ICI, une fois : les écrans n'ont pas à le refaire."""
        for faux in (False, "", "bleu", "#12345", None):
            self.assertEqual(brand_ramp(faux), brand_ramp(PALETTE[0]))

    def test_assombrir_et_eclaircir_vont_dans_le_bon_sens(self):
        self.assertEqual(darken("#ffffff", 0.5), "#808080")
        self.assertEqual(lighten("#000000", 0.5), "#808080")
        self.assertEqual(darken("#000000", 0.5), "#000000")
        self.assertEqual(lighten("#ffffff", 0.5), "#ffffff")
