# -*- coding: utf-8 -*-
import base64
from datetime import timedelta

from odoo.tests import tagged
from odoo.tools import is_html_empty

from .common import BfLiveCommon

# Un GIF d'un pixel : une image valide, et rien de plus.
PIXEL = base64.b64encode(base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"))


@tagged("post_install", "-at_install")
class TestTelephone(BfLiveCommon):
    """Le téléphone du participant pendant une diapositive, et son équipe.

    🔴 Ce qui s'y joue n'est pas cosmétique : avant ce lot, une diapositive sans
    description mettait le téléphone dans la branche « question » du cœur, avec
    son bouton « Soumettre » et sa touche Entrée armée. Les deux menaient à
    `_save_lines` sur une SECTION, donc à une TypeError en 500 sur le téléphone
    du participant (mesuré au banc : `builtins.TypeError`).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.question_choix.sequence = 20
        cls.question_texte.sequence = 40
        Element = cls.env["survey.question"]
        # Une diapositive NUE : le seul écran que le cœur ne met jamais au mur.
        cls.diapo_nue = Element.create({
            "survey_id": cls.sondage.id,
            "title": "Bienvenue",
            "sequence": 10,
            "is_page": True,
            "bf_slide_layout": "title",
            "bf_slide_subtitle": "Atelier du mardi",
        })
        # Une diapositive qui porte tout : sous-titre, image, texte.
        cls.diapo_pleine = Element.create({
            "survey_id": cls.sondage.id,
            "title": "Le plan de la salle",
            "sequence": 30,
            "is_page": True,
            "bf_slide_layout": "split",
            "bf_slide_image": PIXEL,
            "bf_slide_subtitle": "Les sorties sont à gauche",
            "description": "<p>On se place par équipe.</p>",
        })
        # Une section DÉCRITE qui n'est pas une diapositive : l'écran du cœur
        # doit y rester exactement ce qu'il était.
        cls.section = Element.create({
            "survey_id": cls.sondage.id,
            "title": "Une pause",
            "sequence": 50,
            "is_page": True,
            "description": "<p>Dix minutes.</p>",
        })

    def rendu(self, question, participation, has_answered=False):
        """L'écran du téléphone, rendu comme la route du cœur le rend.

        ⚠️ Les trois dernières clés ne servent qu'à des attributs `data-…`, mais
        elles doivent EXISTER : QWeb tombe sur un nom inconnu, pas sur une valeur
        vide. Et `is_html_empty` n'est dans le contexte que parce que le
        contrôleur du cœur l'y pose.
        """
        return self.env["ir.qweb"]._render(
            "survey.survey_fill_form_in_progress",
            {
                "survey": self.sondage,
                "answer": participation,
                "question": question,
                "has_answered": has_answered,
                "is_html_empty": is_html_empty,
                "server_time": False,
                "timer_start": False,
                "time_limit_minutes": False,
            })

    def boutons(self, html):
        """Les boutons de soumission du formulaire.

        La flèche du cadre de la page n'est pas dans ce gabarit : tout ce qui
        porte `type="submit"` ici vient bien du formulaire.
        """
        return html.count('type="submit"')

    # ------------------------------------------------------------
    # 🔴 La garde du serveur
    # ------------------------------------------------------------

    def test_une_soumission_sur_une_section_nenregistre_rien_et_ne_tombe_pas(self):
        """Le cœur finirait sur `question_type + ": …"`, donc sur une TypeError."""
        ana = self.participant("Ana")
        self.assertIsNone(ana._save_lines(self.diapo_nue, None))
        self.assertFalse(ana.user_input_line_ids)
        # Et la même chose sur une section décrite, et sur une section qui n'est
        # pas une diapositive du tout : c'est le fait d'être une SECTION qui
        # décide, pas la mise en page.
        self.assertIsNone(ana._save_lines(self.diapo_pleine, None))
        self.assertIsNone(ana._save_lines(self.section, "n'importe quoi"))
        self.assertFalse(ana.user_input_line_ids)

    def test_une_reponse_a_une_vraie_question_senregistre_toujours(self):
        """La garde ne doit pas avaler les réponses : le témoin de l'autre sens."""
        ana = self.participant("Ana")
        ana._save_lines(self.question_texte, "un mot")
        self.assertEqual(ana.user_input_line_ids.value_text_box, "un mot")

    # ------------------------------------------------------------
    # Ce que le téléphone offre
    # ------------------------------------------------------------

    def test_une_diapositive_nue_noffre_aucun_bouton(self):
        ana = self.participant("Ana")
        html = self.rendu(self.diapo_nue, ana)
        self.assertEqual(self.boutons(html), 0)
        self.assertIn("o_bf_phone_slide", html)

    def test_le_drapeau_du_coeur_dit_vrai_sur_une_diapositive(self):
        """C'est ce drapeau, et lui seul, qui désarme la touche Entrée."""
        ana = self.participant("Ana")
        self.assertIn('data-is-page-description="True"', self.rendu(self.diapo_nue, ana))
        self.assertIn('data-is-page-description="True"',
                      self.rendu(self.diapo_pleine, ana))
        # Une vraie question garde le drapeau du cœur : faux, donc absent.
        self.assertNotIn("data-is-page-description",
                         self.rendu(self.question_choix, ana))

    def test_une_question_garde_son_bouton_et_son_champ(self):
        ana = self.participant("Ana")
        html = self.rendu(self.question_choix, ana)
        self.assertEqual(self.boutons(html), 1)
        self.assertIn("Quelle couleur", html)
        self.assertNotIn("o_bf_phone_slide", html)

    def test_une_section_decrite_garde_lecran_du_coeur(self):
        """Pas une diapositive : le cœur l'avait déjà, et rien ne change."""
        ana = self.participant("Ana")
        html = self.rendu(self.section, ana)
        self.assertEqual(self.boutons(html), 0)
        self.assertNotIn("o_bf_phone_slide", html)

    # ------------------------------------------------------------
    # Ce que le téléphone montre
    # ------------------------------------------------------------

    def test_une_diapositive_porte_ses_trois_champs_et_son_image(self):
        ana = self.participant("Ana")
        html = self.rendu(self.diapo_pleine, ana)
        self.assertIn("Le plan de la salle", html)
        self.assertIn("Les sorties sont à gauche", html)
        self.assertIn("On se place par équipe.", html)
        self.assertIn("/bf_survey_live/slide_image/%s/%s"
                      % (self.sondage.access_token, self.diapo_pleine.id), html)

    def test_une_diapositive_sans_image_nen_demande_pas_une(self):
        ana = self.participant("Ana")
        self.assertNotIn("slide_image", self.rendu(self.diapo_nue, ana))

    def test_aucune_mise_en_page_navale_un_champ_au_telephone(self):
        """Le même contenu, cinq mises en page : rien ne tombe en silence."""
        ana = self.participant("Ana")
        for mise in ("title", "text", "image", "split", "quote"):
            self.diapo_pleine.bf_slide_layout = mise
            html = self.rendu(self.diapo_pleine, ana)
            self.assertIn("Le plan de la salle", html)
            self.assertIn("Les sorties sont à gauche", html)
            self.assertIn("On se place par équipe.", html)

    def test_la_mise_en_page_projetee_ne_traverse_pas(self):
        """Un téléphone n'a qu'une colonne : les classes du mur n'y vont pas."""
        ana = self.participant("Ana")
        for mise in ("title", "text", "image", "split", "quote"):
            self.diapo_pleine.bf_slide_layout = mise
            html = self.rendu(self.diapo_pleine, ana)
            self.assertNotIn("o_bf_slide_layout_", html)
            self.assertNotIn("o_bf_slide_split", html)

    # ------------------------------------------------------------
    # Son équipe, sur son propre téléphone
    # ------------------------------------------------------------

    def equipe(self, nom, cle, rang=10):
        return self.env["bf.live.team"].create({
            "name": nom, "survey_id": self.sondage.id,
            "color_key": cle, "sequence": rang})

    def test_le_badge_porte_le_nom_et_les_couleurs_de_lequipe(self):
        bleue = self.equipe("Équipe bleue", "bleue")
        ana = self.participant("Ana")
        ana.bf_team_id = bleue.id
        html = self.rendu(self.question_choix, ana)
        self.assertIn("o_bf_phone_team", html)
        self.assertIn("Équipe bleue", html)
        self.assertIn(bleue.color, html)
        self.assertIn(bleue.text_color, html)

    def test_le_badge_suit_le_participant_sur_tous_les_ecrans(self):
        bleue = self.equipe("Équipe bleue", "bleue")
        ana = self.participant("Ana")
        ana.bf_team_id = bleue.id
        for ecran in (self.diapo_nue, self.diapo_pleine, self.section,
                      self.question_choix, self.question_texte):
            self.assertIn("o_bf_phone_team", self.rendu(ecran, ana))

    def test_sans_equipe_aucun_badge(self):
        ana = self.participant("Ana")
        self.assertNotIn("o_bf_phone_team", self.rendu(self.question_choix, ana))

    def test_un_repondant_hors_seance_ne_porte_pas_de_badge(self):
        """Le badge est une affaire de séance : un formulaire ordinaire l'ignore."""
        bleue = self.equipe("Équipe bleue", "bleue")
        repondant = self.env["survey.user_input"].create({
            "survey_id": self.sondage.id, "is_session_answer": False})
        repondant.bf_team_id = bleue.id
        self.assertNotIn("o_bf_phone_team", self.rendu(self.question_choix, repondant))

    # ------------------------------------------------------------
    # L'écran d'attente : la bande des équipes
    # ------------------------------------------------------------

    def test_la_bande_porte_chaque_equipe_et_son_effectif(self):
        bleue = self.equipe("Équipe bleue", "bleue", 10)
        orange = self.equipe("Équipe orange", "orange", 20)
        self.participant("Ana").bf_team_id = bleue.id
        self.participant("Bo").bf_team_id = bleue.id
        self.participant("Cid").bf_team_id = orange.id
        bande = self.sondage._bf_team_strip()
        self.assertEqual([ligne["label"] for ligne in bande["teams"]],
                         ["Équipe bleue", "Équipe orange"])
        self.assertEqual([ligne["members"] for ligne in bande["teams"]], [2, 1])

    def test_la_bande_compte_les_sans_equipe_une_fois_le_tirage_fait(self):
        """⚠️ Et pas avant : « 4 personnes sans équipe » avant le tirage est vrai,
        inévitable et sans intérêt."""
        bleue = self.equipe("Équipe bleue", "bleue")
        self.participant("Ana")
        self.participant("Bo")
        self.assertEqual(self.sondage._bf_team_strip()["teamless"], 0)
        self.participant("Cid").bf_team_id = bleue.id
        self.assertEqual(self.sondage._bf_team_strip()["teamless"], 2)

    def test_la_bande_ne_compte_que_la_seance_en_cours(self):
        """Une équipe survit à sa séance ; la bande parle de celle d'aujourd'hui."""
        bleue = self.equipe("Équipe bleue", "bleue")
        ana = self.participant("Ana")
        ana.bf_team_id = bleue.id
        self.assertEqual(self.sondage._bf_team_strip()["teams"][0]["members"], 1)
        # La même équipe, une séance plus tard : plus personne dedans.
        self.sondage.session_start_time = ana.create_date + timedelta(hours=1)
        self.assertEqual(self.sondage._bf_team_strip()["teams"][0]["members"], 0)

    def test_la_bande_se_rend_en_qweb_avec_ses_couleurs(self):
        bleue = self.equipe("Équipe bleue", "bleue")
        self.participant("Ana").bf_team_id = bleue.id
        self.participant("Bo")
        html = self.env["ir.qweb"]._render(
            "bf_survey_live.team_strip", {"survey": self.sondage})
        self.assertIn("Équipe bleue", html)
        self.assertIn("1 membre", html)
        self.assertIn(bleue.color, html)
        self.assertIn("personne sans équipe", html)

    def test_une_bande_sans_equipe_ne_rend_rien(self):
        """Le cadre est posé même vide : il ne doit rien y mettre."""
        html = self.env["ir.qweb"]._render(
            "bf_survey_live.team_strip", {"survey": self.sondage})
        self.assertNotIn("o_bf_live_strip_chip", html)
