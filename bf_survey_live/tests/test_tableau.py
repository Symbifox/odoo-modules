# -*- coding: utf-8 -*-
from odoo.tests import tagged

from odoo.addons.bf_live_base.models.color_utils import brand_ramp, contrast_ratio

from .common import BfLiveCommon


@tagged("post_install", "-at_install")
class TestTableau(BfLiveCommon):

    # ------------------------------------------------------------
    # Par personne
    # ------------------------------------------------------------

    def test_classement_par_points_decroissants(self):
        self.participant("Ana", points=10)
        self.participant("Bo", points=30)
        self.participant("Cam", points=20)
        tableau = self.sondage._bf_live_scoreboard()
        self.assertEqual(tableau["mode"], "individual")
        self.assertEqual([ligne["label"] for ligne in tableau["rows"]], ["Bo", "Cam", "Ana"])
        self.assertEqual([ligne["rank"] for ligne in tableau["rows"]], [1, 2, 3])

    def test_un_pseudonyme_vide_devient_anonyme(self):
        self.participant("")
        tableau = self.sondage._bf_live_scoreboard()
        self.assertEqual(tableau["rows"][0]["label"], "Anonyme")

    def test_chaque_rang_porte_une_encre_lisible(self):
        for index in range(14):
            self.participant("joueur%s" % index, points=index)
        for ligne in self.sondage._bf_live_scoreboard()["rows"]:
            self.assertGreaterEqual(
                contrast_ratio(ligne["color"], ligne["text_color"]), 4.5)

    def test_les_essais_de_lanimateur_ne_jouent_pas(self):
        """Le classement du cœur, lui, compte les essais parmi les joueurs."""
        self.participant("Ana", points=10)
        self.participant("essai", points=99, test_entry=True)
        tableau = self.sondage._bf_live_scoreboard()
        self.assertEqual([ligne["label"] for ligne in tableau["rows"]], ["Ana"])
        self.assertEqual(tableau["attendees"], 1)

    def test_une_seance_jamais_ouverte_na_personne(self):
        self.sondage.session_start_time = False
        self.participant("Ana")
        tableau = self.sondage._bf_live_scoreboard()
        self.assertEqual(tableau["rows"], [])
        self.assertEqual(tableau["attendees"], 0)

    # ------------------------------------------------------------
    # Par équipe
    # ------------------------------------------------------------

    def test_le_classement_dequipe_se_fait_a_la_moyenne(self):
        """Le cas qui sépare les deux mesures : la grosse équipe a plus de
        points, la petite a une meilleure moyenne. C'est la moyenne qui classe,
        sinon « quel service ? » ferait gagner le plus gros service."""
        self.sondage.bf_live_scoreboard = "team"
        grosse = self.env["bf.live.team"].create({
            "name": "Grosse", "survey_id": self.sondage.id, "color_key": "bleue"})
        petite = self.env["bf.live.team"].create({
            "name": "Petite", "survey_id": self.sondage.id, "color_key": "orange"})
        for index in range(4):
            self.participant("gros%s" % index, points=10).bf_team_id = grosse
        self.participant("petit", points=30).bf_team_id = petite
        tableau = self.sondage._bf_live_scoreboard()
        self.assertEqual(tableau["mode"], "team")
        self.assertEqual([ligne["label"] for ligne in tableau["rows"]], ["Petite", "Grosse"])
        premiere, seconde = tableau["rows"]
        self.assertEqual((premiere["score"], premiere["members"], premiere["average"]), (30, 1, 30))
        self.assertEqual((seconde["score"], seconde["members"], seconde["average"]), (40, 4, 10))

    def test_une_equipe_sans_personne_ne_prend_pas_de_bloc(self):
        self.sondage.bf_live_scoreboard = "team"
        pleine = self.env["bf.live.team"].create({
            "name": "Pleine", "survey_id": self.sondage.id, "color_key": "bleue"})
        self.env["bf.live.team"].create({
            "name": "Vide", "survey_id": self.sondage.id, "color_key": "orange"})
        self.participant("Ana", points=10).bf_team_id = pleine
        tableau = self.sondage._bf_live_scoreboard()
        self.assertEqual([ligne["label"] for ligne in tableau["rows"]], ["Pleine"])

    def test_les_sans_equipe_se_comptent_a_part(self):
        """L'animateur doit VOIR les retardataires, pas les perdre."""
        self.sondage.bf_live_scoreboard = "team"
        equipe = self.env["bf.live.team"].create({
            "name": "Bleue", "survey_id": self.sondage.id, "color_key": "bleue"})
        self.participant("Ana").bf_team_id = equipe
        self.participant("retard")
        tableau = self.sondage._bf_live_scoreboard()
        self.assertEqual(tableau["teamless"], 1)
        self.assertEqual(tableau["attendees"], 2)

    def test_lequipe_porte_sa_couleur_sur_le_tableau(self):
        self.sondage.bf_live_scoreboard = "team"
        equipe = self.env["bf.live.team"].create({
            "name": "Verte", "survey_id": self.sondage.id, "color_key": "verte"})
        self.participant("Ana").bf_team_id = equipe
        ligne = self.sondage._bf_live_scoreboard()["rows"][0]
        self.assertEqual(ligne["color"], equipe.color)
        self.assertGreaterEqual(contrast_ratio(ligne["color"], ligne["text_color"]), 4.5)

    # ------------------------------------------------------------
    # Le podium
    # ------------------------------------------------------------

    def test_le_podium_prend_les_trois_premiers(self):
        for index, points in enumerate((5, 40, 30, 20, 10)):
            self.participant("joueur%s" % index, points=points)
        tableau = self.sondage._bf_live_scoreboard(final=True)
        self.assertEqual([ligne["rank"] for ligne in tableau["podium"]], [1, 2, 3])
        self.assertEqual([ligne["score"] for ligne in tableau["podium"]], [40, 30, 20])
        self.assertEqual([ligne["rank"] for ligne in tableau["others"]], [4, 5])

    def test_le_podium_sarrange_dune_salle_de_deux(self):
        self.participant("Ana", points=10)
        self.participant("Bo", points=20)
        tableau = self.sondage._bf_live_scoreboard(final=True)
        self.assertEqual(len(tableau["podium"]), 2)
        self.assertEqual(tableau["others"], [])

    def test_le_podium_dune_salle_vide_ne_tombe_pas(self):
        tableau = self.sondage._bf_live_scoreboard(final=True)
        self.assertEqual(tableau["podium"], [])
        self.assertEqual(tableau["rows"], [])

    def test_les_trois_blocs_du_podium_portent_une_encre_lisible(self):
        """Les blocs sont à la marque de la MAISON du sondage, et leur encre est
        CALCULÉE : le bleu de la maison prend l'encre foncée, le blanc n'y tient
        pas."""
        for index in range(3):
            self.participant("joueur%s" % index, points=10 * (index + 1))
        tableau = self.sondage._bf_live_scoreboard(final=True)
        attendu = list(brand_ramp(self.env.company.primary_color))
        self.assertEqual([ligne["block_color"] for ligne in tableau["podium"]], attendu)
        for ligne in tableau["podium"]:
            self.assertGreaterEqual(
                contrast_ratio(ligne["block_color"], ligne["block_text_color"]), 4.5,
                "le bloc %s ne porte pas d'encre lisible" % ligne["block_color"])

    def test_le_podium_marche_aussi_par_equipe(self):
        self.sondage.bf_live_scoreboard = "team"
        for index, cle in enumerate(("bleue", "orange", "verte")):
            equipe = self.env["bf.live.team"].create({
                "name": "Équipe %s" % cle, "survey_id": self.sondage.id,
                "color_key": cle})
            self.participant("joueur%s" % index, points=10 * (index + 1)).bf_team_id = equipe
        tableau = self.sondage._bf_live_scoreboard(final=True)
        self.assertEqual(tableau["mode"], "team")
        self.assertEqual([ligne["label"] for ligne in tableau["podium"]],
                         ["Équipe verte", "Équipe orange", "Équipe bleue"])

    # ------------------------------------------------------------
    # Les gabarits
    # ------------------------------------------------------------

    def test_le_gabarit_du_tableau_se_rend(self):
        self.participant("Ana", points=10)
        rendu = self.env["ir.qweb"]._render(
            "bf_survey_live.scoreboard_board",
            {"board": self.sondage._bf_live_scoreboard()})
        self.assertIn("Ana", rendu)
        self.assertIn("o_bf_live_chip", rendu)

    def test_le_gabarit_du_podium_se_rend(self):
        self.participant("Ana", points=10)
        rendu = self.env["ir.qweb"]._render(
            "bf_survey_live.scoreboard_podium",
            {"board": self.sondage._bf_live_scoreboard(final=True)})
        self.assertIn("Ana", rendu)
        self.assertIn("o_bf_live_podium_block", rendu)

    def test_le_gabarit_du_podium_se_rend_sur_une_salle_vide(self):
        rendu = self.env["ir.qweb"]._render(
            "bf_survey_live.scoreboard_podium",
            {"board": self.sondage._bf_live_scoreboard(final=True)})
        self.assertIn("pas de podium", rendu)
