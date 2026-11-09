# -*- coding: utf-8 -*-
from urllib.parse import quote_plus

from odoo.tests import tagged

from odoo.addons.bf_live_base.models.color_utils import (
    PALETTE, brand_ramp, contrast_ratio, text_color,
)

from .common import BfLiveCommon

# Un PNG d'un pixel, pour donner un logo à la société sans lire de fichier.
PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkAAIAAAoAAv/"
    "lxKUAAAAASUVORK5CYII=")


@tagged("post_install", "-at_install")
class TestMarqueEtCode(BfLiveCommon):
    """La carte pour rejoindre, et la marque de la maison à l'écran."""

    # ------------------------------------------------------------
    # LA CARTE POUR REJOINDRE
    # ------------------------------------------------------------

    def test_la_carte_porte_le_code_et_son_code_qr(self):
        carte = self.sondage._bf_join_badge()
        self.assertEqual(carte["code"], self.sondage.session_code)
        self.assertEqual(carte["link"], self.sondage.session_link)
        # Le code QR encode l'adresse de la séance, et c'est elle qu'on scanne.
        self.assertIn(quote_plus(self.sondage.session_link), carte["qr"])
        self.assertTrue(carte["qr"].startswith("/report/barcode/QR/"))

    def test_rien_hors_d_une_seance_en_cours(self):
        """L'écran d'accueil porte déjà son grand code QR, et une séance close
        n'a plus de code qui mène quelque part."""
        self.sondage.session_state = "ready"
        self.assertFalse(self.sondage._bf_join_badge())
        self.sondage.session_state = False
        self.assertFalse(self.sondage._bf_join_badge())

    def test_le_drapeau_coupe_la_carte(self):
        self.sondage.bf_live_join_badge = False
        self.assertFalse(self.sondage._bf_join_badge())

    # ------------------------------------------------------------
    # LA MARQUE DE LA MAISON
    # ------------------------------------------------------------

    def test_la_marque_prend_la_couleur_de_la_societe(self):
        self.env.company.primary_color = "#8b1f62"
        marque = self.sondage._bf_live_brand()
        self.assertEqual(marque["ramp"], brand_ramp("#8b1f62"))
        self.assertEqual(marque["color"], marque["ramp"][0])
        self.assertEqual(marque["text_color"], text_color(marque["color"]))
        self.assertGreaterEqual(
            contrast_ratio(marque["color"], marque["text_color"]), 4.5)

    def test_sans_couleur_la_marque_garde_le_bleu_mesure(self):
        self.env.company.primary_color = False
        self.assertEqual(self.sondage._bf_live_brand()["ramp"], brand_ramp(PALETTE[0]))

    def test_le_podium_suit_la_couleur_de_la_societe(self):
        self.env.company.primary_color = "#8b1f62"
        for rang in range(3):
            self.participant("joueur%s" % rang, points=10 * (rang + 1))
        tableau = self.sondage._bf_live_scoreboard(final=True)
        self.assertEqual([ligne["block_color"] for ligne in tableau["podium"]],
                         list(brand_ramp("#8b1f62")))

    def test_le_logo_n_est_pose_que_si_la_societe_en_a_un(self):
        """🔴 Sans ce contrôle, `/logo.png` sert son carré gris de remplacement,
        et c'est lui qu'on projette au mur sans le savoir."""
        self.env.company.logo = False
        self.assertFalse(self.sondage._bf_live_brand()["logo"])
        self.env.company.logo = PNG_1PX
        self.assertEqual(self.sondage._bf_live_brand()["logo"],
                         "/logo.png?company=%s" % self.env.company.id)


@tagged("post_install", "-at_install")
class TestReponsesEcrites(BfLiveCommon):
    """Les réponses que la séance du cœur n'affichait pas : texte long et nombre."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.question_libre = cls.env["survey.question"].create({
            "survey_id": cls.sondage.id,
            "title": "Qu'est-ce qu'on garde",
            "sequence": 3,
            "question_type": "text_box",
        })
        cls.question_nombre = cls.env["survey.question"].create({
            "survey_id": cls.sondage.id,
            "title": "Combien de personnes",
            "sequence": 4,
            "question_type": "numerical_box",
        })

    def ligne_libre(self, texte, question=None):
        return self.env["survey.user_input.line"].create({
            "user_input_id": self.participant("Ana").id,
            "question_id": (question or self.question_libre).id,
            "answer_type": "text_box",
            "value_text_box": texte,
        })

    def ligne_nombre(self, valeur, skipped=False):
        return self.env["survey.user_input.line"].create({
            "user_input_id": self.participant("Bo").id,
            "question_id": self.question_nombre.id,
            "answer_type": False if skipped else "numerical_box",
            "value_numerical_box": valeur,
            "skipped": skipped,
        })

    # ------------------------------------------------------------
    # QUI SE PROJETTE, ET QUI NON
    # ------------------------------------------------------------

    def test_un_texte_long_sans_nuage_s_ecrit_au_mur(self):
        self.assertTrue(self.question_libre._bf_shows_written_answers())

    def test_un_nombre_s_ecrit_au_mur(self):
        self.assertTrue(self.question_nombre._bf_shows_written_answers())

    def test_un_texte_long_avec_nuage_garde_son_nuage(self):
        """Le nuage EST son écran : les deux ne se superposent pas."""
        self.assertTrue(self.question_texte._bf_shows_word_cloud())
        self.assertFalse(self.question_texte._bf_shows_written_answers())

    def test_une_question_qui_recolte_un_courriel_ne_se_projette_jamais(self):
        self.question_libre.save_as_email = True
        self.assertFalse(self.question_libre._bf_shows_written_answers())

    def test_une_question_qui_recolte_un_pseudonyme_ne_se_projette_jamais(self):
        self.question_libre.save_as_nickname = True
        self.assertFalse(self.question_libre._bf_shows_written_answers())

    def test_les_types_que_le_coeur_affiche_deja_restent_a_lui(self):
        """Une date et un texte court ont déjà leur liste : on ne la double pas."""
        for type_de_question in ("char_box", "date", "datetime", "simple_choice"):
            self.question_libre.question_type = type_de_question
            self.assertFalse(self.question_libre._bf_shows_written_answers())

    # ------------------------------------------------------------
    # CE QUI PART AU MUR
    # ------------------------------------------------------------

    def test_la_forme_est_celle_de_la_charge_utile_du_coeur(self):
        ligne = self.ligne_libre("On garde le tableau blanc")
        reponses = self.question_libre._bf_written_answers(ligne)
        self.assertEqual(reponses, [{"id": ligne.id, "value": "On garde le tableau blanc"}])

    def test_une_reponse_longue_est_coupee_a_la_largeur_du_mur(self):
        ligne = self.ligne_libre("a" * 400)
        valeur = self.question_libre._bf_written_answers(ligne)[0]["value"]
        self.assertEqual(len(valeur), 70)
        self.assertTrue(valeur.endswith("…"))

    def test_les_retours_a_la_ligne_se_resserrent(self):
        """Le cadre du cœur écrit sur une ligne : un texte en trois paragraphes
        y arriverait avec ses blancs, et sa largeur calculée serait fausse."""
        ligne = self.ligne_libre("Un mot\n\n  puis\tun autre  ")
        self.assertEqual(self.question_libre._bf_written_answers(ligne)[0]["value"],
                         "Un mot puis un autre")

    def test_une_reponse_vide_ne_prend_pas_d_etiquette(self):
        ligne = self.ligne_libre("   ")
        self.assertEqual(self.question_libre._bf_written_answers(ligne), [])

    def test_un_nombre_entier_s_ecrit_sans_decimales(self):
        ligne = self.ligne_nombre(42)
        self.assertEqual(self.question_nombre._bf_written_answers(ligne)[0]["value"], "42")

    def test_un_nombre_a_virgule_garde_ses_decimales(self):
        ligne = self.ligne_nombre(3.5)
        valeur = self.question_nombre._bf_written_answers(ligne)[0]["value"]
        # La virgule ou le point viennent de la langue, jamais de « %s ».
        self.assertIn(valeur, ("3,50", "3.50"))

    def test_une_question_numerique_sautee_ne_met_pas_de_zero_au_mur(self):
        """🔴 Le cœur laisse passer les lignes sautées : une réponse texte sautée
        porte False et le navigateur l'écarte, mais un NOMBRE sauté porte 0.0, et
        le mur aurait affiché « 0 » pour chaque personne qui a passé la question.
        """
        saute = self.ligne_nombre(0, skipped=True)
        repondu = self.ligne_nombre(7)
        reponses = self.question_nombre._bf_written_answers(saute | repondu)
        self.assertEqual(reponses, [{"id": repondu.id, "value": "7"}])

    def test_un_zero_repondu_reste_au_mur(self):
        """Et l'inverse : « 0 » est une réponse, quand personne ne l'a sautée."""
        ligne = self.ligne_nombre(0)
        self.assertEqual(self.question_nombre._bf_written_answers(ligne)[0]["value"], "0")

    def test_un_essai_du_sondage_ne_s_affiche_pas_parmi_les_reponses_de_la_salle(self):
        """🔴 Le cœur pose `is_session_answer=True` sur un essai ouvert pendant
        la séance : un « Test » de l'animateur écrivait sa réponse au mur, au
        milieu de celles de la salle. Le module promet le contraire ailleurs —
        `_bf_live_participations` et le podium écartent déjà `test_entry`.
        """
        salle = self.ligne_libre("On garde le tableau blanc")
        essai = self.env["survey.user_input.line"].create({
            "user_input_id": self.participant("essai", test_entry=True).id,
            "question_id": self.question_libre.id,
            "answer_type": "text_box",
            "value_text_box": "Essai de l'animateur",
        })
        self.assertEqual(self.question_libre._bf_written_answers(salle | essai),
                         [{"id": salle.id, "value": "On garde le tableau blanc"}])

    def test_le_mur_ne_prend_que_les_cent_premieres(self):
        lignes = self.env["survey.user_input.line"]
        for rang in range(5):
            lignes |= self.ligne_libre("reponse %s" % rang)
        self.assertEqual(len(self.question_libre._bf_written_answers(lignes, limite=3)), 3)

    def test_les_lignes_jetees_sortent_avant_la_tranche_pas_apres(self):
        """🔴 Trancher d'abord compte les lignes jetées dans le quota :
        150 participants dont 60 ont passé la question donnaient 40 réponses au
        mur sur 90. Même forme ici, en plus petit : dix sautées devant, dix
        réponses derrière, un quota de dix — le mur restait vide.
        """
        lignes = self.env["survey.user_input.line"]
        for _ in range(10):
            lignes |= self.ligne_nombre(0, skipped=True)
        for rang in range(10):
            lignes |= self.ligne_nombre(rang + 1)
        self.assertEqual(
            len(self.question_nombre._bf_written_answers(lignes, limite=10)), 10)
