# -*- coding: utf-8 -*-
from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.bf_survey_live.controllers import session as controleur
from odoo.addons.bf_survey_live.models import nuage

from .common import BfLiveCommon


@tagged("post_install", "-at_install")
class TestNuageDeMots(BfLiveCommon):

    # ------------------------------------------------------------
    # Les jetons
    # ------------------------------------------------------------

    def test_elision_retiree(self):
        """« l'équipe » et « équipe » sont le même mot."""
        self.assertEqual(nuage.jetons("L'équipe est là"), ["équipe", "est", "là"])

    def test_mot_a_apostrophe_garde_entier(self):
        """« aujourd'hui » n'est pas une élision : il reste entier."""
        self.assertEqual(nuage.jetons("Aujourd'hui, demain"), ["Aujourd'hui", "demain"])

    def test_apostrophe_typographique(self):
        """L'apostrophe courbe des claviers vaut l'apostrophe droite."""
        self.assertEqual(nuage.jetons("l’atelier"), ["atelier"])

    def test_trait_dunion_garde_le_mot_entier(self):
        self.assertIn("peut-être", nuage.jetons("peut-être"))

    def test_chiffres_ecartes(self):
        self.assertEqual(nuage.jetons("2026 ans"), ["ans"])

    def test_casse_gardee_par_les_jetons(self):
        """La casse survit au découpage : c'est l'appelant qui minuscule."""
        self.assertEqual(nuage.jetons("Odoo"), ["Odoo"])

    # ------------------------------------------------------------
    # Le nuage
    # ------------------------------------------------------------

    def test_mots_outils_ecartes(self):
        mots = [mot["text"] for mot in nuage.nuage(["le chat et le chien dans la maison"])]
        self.assertEqual(sorted(mots), ["chat", "chien", "maison"])

    def test_mot_dune_lettre_ecarte(self):
        self.assertEqual(nuage.nuage(["a b c"]), [])

    def test_un_mot_a_chiffres_se_compte_sous_sa_partie_lettres(self):
        """Mesuré, pas supposé : « odoo18 » et « odoo » sont le même mot ici,
        parce que le découpage laisse les chiffres de côté."""
        resultat = nuage.nuage(["odoo18 odoo"])
        self.assertEqual([(mot["text"], mot["count"]) for mot in resultat], [("odoo", 2)])

    def test_casse_regroupee_graphie_la_plus_frequente(self):
        """« Odoo » et « odoo » comptent pour un, et c'est la forme majoritaire
        qui monte à l'écran."""
        resultat = nuage.nuage(["Odoo", "Odoo", "odoo"])
        self.assertEqual(len(resultat), 1)
        self.assertEqual(resultat[0]["text"], "Odoo")
        self.assertEqual(resultat[0]["count"], 3)

    def test_le_plus_frequent_prend_la_taille_maximale(self):
        resultat = nuage.nuage(["chat chat chat chien"])
        self.assertEqual(resultat[0]["text"], "chat")
        self.assertEqual(resultat[0]["size"], nuage.TAILLE_MAX)

    def test_la_taille_croit_avec_le_compte(self):
        resultat = nuage.nuage(["chat chat chat chien chien souris"])
        tailles = [mot["size"] for mot in resultat]
        self.assertEqual(tailles, sorted(tailles, reverse=True))
        self.assertGreaterEqual(min(tailles), nuage.TAILLE_MIN)

    def test_ordre_deterministe_a_egalite(self):
        """Deux mots à égalité sortent dans le même ordre d'un appel à l'autre,
        sinon le nuage se réarrange sans qu'un mot ait bougé."""
        premier = [mot["text"] for mot in nuage.nuage(["zebre chat lion"])]
        second = [mot["text"] for mot in nuage.nuage(["lion zebre chat"])]
        self.assertEqual(premier, second)
        self.assertEqual(premier, ["chat", "lion", "zebre"])

    def test_limite_respectee(self):
        # ⚠️ Des mots SANS chiffre : « mot1 » et « mot2 » se comptent tous deux
        # sous « mot », puisque le découpage laisse les chiffres de côté.
        mots = ["mot%s%s" % (chr(97 + index // 26), chr(97 + index % 26))
                for index in range(60)]
        self.assertEqual(len(nuage.nuage([" ".join(mots)], limite=12)), 12)

    def test_mots_ecartes_en_plus(self):
        resultat = nuage.nuage(["odoo facile"], a_ecarter="Odoo")
        self.assertEqual([mot["text"] for mot in resultat], ["facile"])

    def test_rien_a_compter(self):
        self.assertEqual(nuage.nuage([]), [])
        self.assertEqual(nuage.nuage(["", None]), [])

    # ------------------------------------------------------------
    # Le nuage d'une question
    # ------------------------------------------------------------

    def test_nuage_dune_question_lit_le_texte_long(self):
        un = self.participant("Ana")
        deux = self.participant("Bo")
        self.reponse_texte(un, "collaboration et clarté")
        self.reponse_texte(deux, "clarté")
        lignes = self.env["survey.user_input.line"].search([
            ("question_id", "=", self.question_texte.id)])
        resultat = self.question_texte._bf_word_cloud(lignes)
        self.assertEqual(resultat[0]["text"], "clarté")
        self.assertEqual(resultat[0]["count"], 2)

    def test_nuage_dune_question_prend_ses_mots_a_ecarter(self):
        un = self.participant("Ana")
        self.reponse_texte(un, "odoo odoo clarté")
        self.question_texte.bf_word_cloud_ignore = "odoo"
        lignes = self.env["survey.user_input.line"].search([
            ("question_id", "=", self.question_texte.id)])
        self.assertEqual(
            [mot["text"] for mot in self.question_texte._bf_word_cloud(lignes)],
            ["clarté"])

    def test_le_nuage_ecarte_un_essai_du_sondage(self):
        """🔴 Les mots du nuage viennent de `_bf_live_lines`, qui ne filtrait
        pas `test_entry` : le cœur pose `is_session_answer=True` sur un essai
        ouvert pendant la séance, donc un « Test » de l'animateur envoyait ses
        mots au mur avec ceux de la salle. Le module l'écarte déjà des
        participations et du podium.

        ⚠️ Le vrai chemin, et non la méthode voisine : c'est la route du nuage
        qui choisit les lignes, et `request` est tout ce qu'elle emprunte à
        l'extérieur.
        """
        self.reponse_texte(self.participant("Ana"), "collaboration")
        self.reponse_texte(self.participant("essai", test_entry=True), "sabotage")
        with patch.object(controleur, "request", SimpleNamespace(env=self.env)):
            lignes = controleur.BfLiveUserInputSession()._bf_live_lines(
                self.sondage, self.question_texte)
        self.assertEqual(lignes.mapped("value_text_box"), ["collaboration"])
        mots = [mot["text"]
                for mot in self.question_texte._bf_word_cloud(lignes)]
        self.assertEqual(mots, ["collaboration"])

    def test_le_nuage_sallume_sur_texte_long_et_texte_court(self):
        self.assertTrue(self.question_texte._bf_shows_word_cloud())
        courte = self.env["survey.question"].create({
            "survey_id": self.sondage.id, "title": "Un mot", "sequence": 3,
            "question_type": "char_box", "bf_live_screen": "word_cloud",
        })
        self.assertTrue(courte._bf_shows_word_cloud())

    def test_le_nuage_reste_eteint_par_defaut(self):
        courte = self.env["survey.question"].create({
            "survey_id": self.sondage.id, "title": "Un mot", "sequence": 3,
            "question_type": "char_box",
        })
        self.assertFalse(courte._bf_shows_word_cloud())

    def test_pas_de_nuage_sur_un_choix(self):
        self.question_choix.bf_live_screen = "word_cloud"
        self.assertFalse(self.question_choix._bf_shows_word_cloud())

    def test_pas_de_nuage_sur_un_pseudonyme_ni_un_courriel(self):
        """Un nuage de courriels serait une fuite affichée au mur."""
        courte = self.env["survey.question"].create({
            "survey_id": self.sondage.id, "title": "Ton nom", "sequence": 3,
            "question_type": "char_box", "bf_live_screen": "word_cloud",
            "save_as_nickname": True,
        })
        self.assertFalse(courte._bf_shows_word_cloud())
        courriel = self.env["survey.question"].create({
            "survey_id": self.sondage.id, "title": "Ton courriel", "sequence": 4,
            "question_type": "char_box", "bf_live_screen": "word_cloud",
            "validation_email": True, "save_as_email": True,
        })
        self.assertFalse(courriel._bf_shows_word_cloud())
