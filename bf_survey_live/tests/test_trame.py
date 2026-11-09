# -*- coding: utf-8 -*-
import base64

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import BfLiveCommon

# Un GIF d'un pixel : une image valide, et rien de plus.
PIXEL = base64.b64encode(base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"))


@tagged("post_install", "-at_install")
class TestTrame(BfLiveCommon):
    """La trame : des diapositives entre les questions.

    Le sondage de `common` porte déjà une question à choix et une question
    ouverte. On y ajoute trois diapositives : une à l'ouverture, une au milieu,
    une à la fin. Aucune ne porte de description : c'est le cas que le cœur
    écarte, et c'est donc le seul qui prouve quelque chose.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Des rangs explicites : `common` pose ses deux questions à 1 et 2, et
        # une diapositive au même rang qu'une question se rangerait derrière
        # elle par son `id`. L'ordre voulu doit se lire dans le `sequence`.
        cls.question_choix.sequence = 10
        cls.question_texte.sequence = 30
        Element = cls.env["survey.question"]
        cls.ouverture = Element.create({
            "survey_id": cls.sondage.id,
            "title": "Bienvenue",
            "sequence": 0,
            "is_page": True,
            "bf_slide_layout": "title",
            "bf_slide_subtitle": "Atelier du mardi",
        })
        cls.milieu = Element.create({
            "survey_id": cls.sondage.id,
            "title": "Une pause",
            "sequence": 20,
            "is_page": True,
            "bf_slide_layout": "image",
            "bf_slide_image": PIXEL,
        })
        cls.fin = Element.create({
            "survey_id": cls.sondage.id,
            "title": "Merci",
            "sequence": 40,
            "is_page": True,
            "bf_slide_layout": "title",
        })
        # L'ordre de la trame : Bienvenue (0), Quelle couleur (10), Une pause
        # (20), Un mot (30), Merci (40).

    def salle(self):
        """La « salle » du cœur : un `survey.user_input` qui n'existe pas.

        🔴 Refait à chaque essai, et jamais gardé sur la classe : les valeurs
        d'un enregistrement `new()` vivent dans le CACHE, que chaque essai vide.
        Gardé dans `setUpClass`, son `survey_id` revenait vide, le cœur rendait
        une liste d'écrans VIDE sans rien dire, et le retour levait une
        ValueError dix lignes plus loin.
        """
        return self.sondage._get_session_most_voted_answers()

    def ecrans(self):
        return self.sondage._get_pages_and_questions_to_show()

    def suivant(self, courant, go_back=False):
        return self.sondage._get_next_page_or_question(
            self.salle(), courant.id, go_back=go_back)

    # ------------------------------------------------------------
    # La liste des écrans
    # ------------------------------------------------------------

    def test_une_diapositive_sans_description_reste_un_ecran(self):
        self.assertFalse(self.milieu.description)
        self.assertIn(self.milieu, self.ecrans())
        self.assertIn(self.ouverture, self.ecrans())
        self.assertIn(self.fin, self.ecrans())

    def test_le_coeur_seul_perdrait_les_diapositives(self):
        """La preuve que c'est la greffe qui les porte, et non le cœur."""
        self.sondage.question_and_page_ids.write({"bf_slide_layout": "standard"})
        restants = self.ecrans()
        self.assertNotIn(self.ouverture, restants)
        self.assertNotIn(self.milieu, restants)
        self.assertNotIn(self.fin, restants)

    def test_lecran_au_mur_reste_dans_la_liste_meme_redevenu_section(self):
        """🔴 Changer une mise en page pendant la séance ferait tomber la page de
        l'animateur : l'écran courant sortirait de la liste, et le cœur y cherche
        son index. Mesuré au banc : 500 sur la page de séance."""
        self.sondage.session_question_id = self.milieu
        self.milieu.bf_slide_layout = "standard"
        self.assertIn(self.milieu, self.ecrans())
        self.assertEqual(
            self.sondage._is_last_page_or_question(self.salle(), self.milieu), False)

    def test_hors_seance_la_liste_reste_celle_du_coeur(self):
        """Une diapositive est un écran de séance, pas une page de formulaire."""
        self.sondage.session_state = False
        self.assertNotIn(self.milieu, self.ecrans())

    def test_les_ecrans_sont_dans_lordre_du_document(self):
        attendus = [self.ouverture, self.question_choix, self.milieu,
                    self.question_texte, self.fin]
        self.assertEqual(list(self.ecrans()), attendus)

    def test_une_section_vide_nest_pas_un_ecran(self):
        vide = self.env["survey.question"].create({
            "survey_id": self.sondage.id, "title": "Section creuse",
            "sequence": 25, "is_page": True,
        })
        self.assertNotIn(vide, self.ecrans())

    # ------------------------------------------------------------
    # La marche d'un écran à l'autre
    # ------------------------------------------------------------

    def test_la_marche_passe_par_la_diapositive(self):
        self.assertEqual(self.suivant(self.ouverture), self.question_choix)
        self.assertEqual(self.suivant(self.question_choix), self.milieu)
        self.assertEqual(self.suivant(self.milieu), self.question_texte)

    def test_le_retour_repasse_par_la_diapositive(self):
        self.assertEqual(self.suivant(self.question_texte, go_back=True), self.milieu)
        self.assertEqual(self.suivant(self.milieu, go_back=True), self.question_choix)
        self.assertEqual(self.suivant(self.question_choix, go_back=True), self.ouverture)

    def test_la_diapositive_de_fin_vient_apres_la_derniere_question(self):
        """Le cœur, lui, rend un ensemble vide : pour lui, le sondage est fini."""
        self.assertEqual(self.suivant(self.question_texte), self.fin)
        self.assertFalse(self.suivant(self.fin))

    def test_deux_diapositives_de_suite_se_suivent(self):
        seconde = self.env["survey.question"].create({
            "survey_id": self.sondage.id, "title": "Et encore",
            "sequence": 21, "is_page": True, "bf_slide_layout": "title",
        })
        salle = self.sondage._get_session_most_voted_answers()
        self.assertEqual(
            self.sondage._get_next_page_or_question(salle, self.question_choix.id),
            self.milieu)
        self.assertEqual(
            self.sondage._get_next_page_or_question(salle, self.milieu.id), seconde)
        self.assertEqual(
            self.sondage._get_next_page_or_question(salle, seconde.id),
            self.question_texte)

    def test_la_diapositive_douverture_est_le_premier_ecran(self):
        """🔴 Sinon la flèche de retour reste offerte, et un retour depuis le
        premier écran FERME la séance."""
        self.assertTrue(self.sondage._is_first_page_or_question(self.ouverture))
        self.assertFalse(self.sondage._is_first_page_or_question(self.question_choix))

    def test_sans_trame_la_regle_du_premier_ecran_ne_change_pas(self):
        self.sondage.question_and_page_ids.write({"bf_slide_layout": "standard"})
        self.assertTrue(
            self.sondage._is_first_page_or_question(self.question_choix))

    def test_la_derniere_question_nest_plus_la_derniere(self):
        salle = self.salle()
        self.assertFalse(
            self.sondage._is_last_page_or_question(salle, self.question_texte))
        self.assertTrue(
            self.sondage._is_last_page_or_question(salle, self.fin))

    # ------------------------------------------------------------
    # Le compteur d'écrans
    # ------------------------------------------------------------

    def test_le_compteur_dit_ou_on_en_est(self):
        for position, ecran in enumerate(self.ecrans(), start=1):
            self.sondage.session_question_id = ecran
            trame = self.sondage._bf_frame_values()
            self.assertEqual(trame["position"], position)
            self.assertEqual(trame["total"], 5)
        self.assertEqual(self.sondage._bf_frame_values()["slides"], 3)

    def test_le_compteur_ne_compte_pas_une_section_vide(self):
        self.env["survey.question"].create({
            "survey_id": self.sondage.id, "title": "Section creuse",
            "sequence": 25, "is_page": True,
        })
        self.assertEqual(self.sondage._bf_frame_values()["total"], 5)

    def test_une_seance_jamais_ouverte_ne_compte_rien(self):
        self.sondage.session_start_time = False
        self.assertEqual(self.sondage._bf_frame_values(),
                         {"total": 0, "position": 0, "slides": 0})

    def test_une_seance_close_ne_compte_plus(self):
        """⚠️ Sinon le compteur mentirait : la séance close, les diapositives
        sortent de la liste des écrans, et sept écrans en afficheraient quatre."""
        self.sondage.session_question_id = self.question_texte
        self.assertEqual(self.sondage._bf_frame_values()["total"], 5)
        self.sondage.session_state = False
        self.assertEqual(self.sondage._bf_frame_values(),
                         {"total": 0, "position": 0, "slides": 0})

    # ------------------------------------------------------------
    # Ce qu'une diapositive accepte
    # ------------------------------------------------------------

    def test_une_question_ne_devient_pas_une_diapositive(self):
        with self.assertRaises(ValidationError):
            self.question_texte.bf_slide_layout = "title"

    def test_une_diapositive_image_sans_image_est_refusee(self):
        with self.assertRaises(ValidationError):
            self.ouverture.bf_slide_layout = "image"
        with self.assertRaises(ValidationError):
            self.ouverture.bf_slide_layout = "split"

    def test_le_texte_dune_diapositive_se_lit_sans_outil_de_contexte(self):
        """`is_html_empty` n'est dans QWeb que si le cœur l'y pose, et il ne l'y
        pose pas quand la séance n'a pas de question en cours."""
        self.assertFalse(self.ouverture.bf_slide_has_text)
        self.ouverture.description = "<p><br></p>"
        self.assertFalse(self.ouverture.bf_slide_has_text)
        self.ouverture.description = "<p>Trois règles, et on commence.</p>"
        self.assertTrue(self.ouverture.bf_slide_has_text)

    # ------------------------------------------------------------
    # Le gabarit projeté
    # ------------------------------------------------------------

    def rendu(self, diapo):
        return self.env["ir.qweb"]._render(
            "bf_survey_live.diapositive",
            {"bf_slide": diapo, "survey": self.sondage})

    def test_le_gabarit_ecrit_le_titre_le_sous_titre_et_le_texte(self):
        self.ouverture.description = "<p>Trois règles, et on commence.</p>"
        html = self.rendu(self.ouverture)
        self.assertIn("Bienvenue", html)
        self.assertIn("Atelier du mardi", html)
        self.assertIn("Trois règles, et on commence.", html)
        self.assertIn("o_bf_slide_layout_title", html)

    def test_chaque_mise_en_page_porte_sa_classe_et_rien_ne_tombe(self):
        """Le même contenu, six fois : aucune mise en page ne l'avale."""
        diapo = self.milieu
        diapo.write({"bf_slide_subtitle": "La légende",
                     "description": "<p>Le texte du bas.</p>"})
        for mise in ("title", "text", "image", "split", "quote"):
            diapo.bf_slide_layout = mise
            html = self.rendu(diapo)
            self.assertIn("o_bf_slide_layout_%s" % mise, html)
            self.assertIn("Une pause", html)
            self.assertIn("La légende", html)
            self.assertIn("Le texte du bas.", html)

    def test_limage_passe_par_le_jeton_du_sondage(self):
        html = self.rendu(self.milieu)
        self.assertIn("/bf_survey_live/slide_image/%s/%s"
                      % (self.sondage.access_token, self.milieu.id), html)

    def test_une_mise_en_page_sans_image_nen_demande_pas_une(self):
        self.assertNotIn("slide_image", self.rendu(self.ouverture))
