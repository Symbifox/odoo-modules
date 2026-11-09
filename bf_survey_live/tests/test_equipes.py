# -*- coding: utf-8 -*-
import psycopg2

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.bf_survey_live.models.bf_live_team import COULEURS, MAX_EQUIPES
from odoo.addons.bf_live_base.models.color_utils import contrast_ratio

from .common import BfLiveCommon


@tagged("post_install", "-at_install")
class TestEquipes(BfLiveCommon):

    # ------------------------------------------------------------
    # Le tirage au hasard
    # ------------------------------------------------------------

    def test_tirage_equilibre_a_un_membre_pres(self):
        """Le tourniquet garantit l'équilibre : sans lui, 7 contre 3 arrive."""
        for index in range(7):
            self.participant("joueur%s" % index)
        self.sondage.bf_live_team_target = 3
        self.sondage.action_bf_draw_teams()
        comptes = sorted(
            len(self.sondage._bf_live_participations().filtered(
                lambda participation, equipe=equipe: participation.bf_team_id == equipe))
            for equipe in self.sondage.bf_live_team_ids
        )
        self.assertEqual(comptes, [2, 2, 3])

    def test_tirage_ne_laisse_personne_sans_equipe(self):
        for index in range(5):
            self.participant("joueur%s" % index)
        self.sondage.bf_live_team_target = 2
        self.sondage.action_bf_draw_teams()
        participations = self.sondage._bf_live_participations()
        self.assertTrue(all(participation.bf_team_id for participation in participations))

    def test_tirage_reutilise_ses_equipes(self):
        """Deux tirages ne font pas deux jeux d'équipes."""
        for index in range(4):
            self.participant("joueur%s" % index)
        self.sondage.bf_live_team_target = 2
        self.sondage.action_bf_draw_teams()
        premieres = self.sondage.bf_live_team_ids
        self.sondage.action_bf_draw_teams()
        self.assertEqual(premieres, self.sondage.bf_live_team_ids)

    def test_tirage_reprend_les_arrivants(self):
        for index in range(4):
            self.participant("joueur%s" % index)
        self.sondage.bf_live_team_target = 2
        self.sondage.action_bf_draw_teams()
        retardataire = self.participant("retard")
        self.assertFalse(retardataire.bf_team_id)
        self.sondage.action_bf_draw_teams()
        self.assertTrue(retardataire.bf_team_id)

    def test_tirage_refuse_hors_bornes(self):
        self.participant("Ana")
        self.sondage.bf_live_team_target = 1
        with self.assertRaises(UserError):
            self.sondage.action_bf_draw_teams()
        self.sondage.bf_live_team_target = MAX_EQUIPES + 1
        with self.assertRaises(UserError):
            self.sondage.action_bf_draw_teams()

    def test_tirage_refuse_une_salle_vide(self):
        self.sondage.bf_live_team_target = 2
        with self.assertRaises(UserError):
            self.sondage.action_bf_draw_teams()

    def test_tirage_refuse_si_une_question_choisit_lequipe(self):
        """Deux façons de faire les équipes en même temps, et la dernière
        réponse écraserait le tirage sans que personne le voie."""
        self.participant("Ana")
        self.question_choix.bf_save_as_team = True
        self.sondage.bf_live_team_target = 2
        with self.assertRaises(UserError):
            self.sondage.action_bf_draw_teams()

    def test_tirage_ignore_les_essais_de_lanimateur(self):
        self.participant("Ana")
        self.participant("essai", test_entry=True)
        self.sondage.bf_live_team_target = 2
        self.sondage.action_bf_draw_teams()
        essai = self.env["survey.user_input"].search([
            ("survey_id", "=", self.sondage.id), ("test_entry", "=", True)])
        self.assertFalse(essai.bf_team_id)

    # ------------------------------------------------------------
    # Les équipes d'une question
    # ------------------------------------------------------------

    def test_une_equipe_par_reponse_a_louverture(self):
        self.question_choix.bf_save_as_team = True
        self.sondage._bf_sync_teams()
        self.assertEqual(
            sorted(self.sondage.bf_live_team_ids.mapped("name")), ["Bleu", "Vert"])
        self.assertEqual(
            self.sondage.bf_live_team_ids.mapped("answer_id"),
            self.question_choix.suggested_answer_ids)

    def test_la_synchronisation_ne_duplique_pas(self):
        self.question_choix.bf_save_as_team = True
        self.sondage._bf_sync_teams()
        self.sondage._bf_sync_teams()
        self.assertEqual(len(self.sondage.bf_live_team_ids), 2)

    def test_louverture_de_la_seance_fige_les_equipes(self):
        """Le crochet est à l'ouverture, pas à la réponse : une équipe créée par
        deux participants en même temps ferait tomber la réponse de l'un."""
        self.question_choix.bf_save_as_team = True
        self.sondage.session_state = False
        self.sondage.action_start_session()
        self.assertEqual(len(self.sondage.bf_live_team_ids), 2)

    def test_la_reponse_fait_entrer_dans_lequipe(self):
        self.question_choix.bf_save_as_team = True
        self.sondage._bf_sync_teams()
        participation = self.participant("Ana")
        participation._save_lines(self.question_choix, str(self.reponse_bleu.id))
        equipe = self.sondage.bf_live_team_ids.filtered(
            lambda team: team.answer_id == self.reponse_bleu)
        self.assertEqual(participation.bf_team_id, equipe)

    def test_une_reponse_sans_equipe_ne_casse_pas_la_reponse(self):
        """Une réponse ajoutée en cours de séance n'a pas d'équipe : le
        participant reste sans équipe, et sa réponse passe quand même."""
        self.question_choix.bf_save_as_team = True
        self.sondage._bf_sync_teams()
        tardive = self.env["survey.question.answer"].create({
            "question_id": self.question_choix.id, "value": "Rouge"})
        participation = self.participant("Ana")
        participation._save_lines(self.question_choix, str(tardive.id))
        self.assertFalse(participation.bf_team_id)
        self.assertTrue(self.env["survey.user_input.line"].search([
            ("user_input_id", "=", participation.id),
            ("suggested_answer_id", "=", tardive.id)]))

    def test_une_seule_question_choisit_lequipe(self):
        self.question_choix.bf_save_as_team = True
        autre = self.env["survey.question"].create({
            "survey_id": self.sondage.id, "title": "Autre choix", "sequence": 4,
            "question_type": "simple_choice",
            "suggested_answer_ids": [(0, 0, {"value": "A"})],
        })
        with self.assertRaises(ValidationError):
            autre.bf_save_as_team = True

    def test_seul_un_choix_unique_choisit_lequipe(self):
        with self.assertRaises(ValidationError):
            self.question_texte.bf_save_as_team = True

    def test_une_reponse_ne_mene_qua_une_equipe(self):
        self.question_choix.bf_save_as_team = True
        self.sondage._bf_sync_teams()
        # ⚠️ Le point de reprise n'est pas décoratif : une violation de
        # contrainte casse la transaction, et la suite de l'essai partirait sur
        # un curseur mort.
        with mute_logger("odoo.sql_db"), self.assertRaises(psycopg2.IntegrityError):
            with self.cr.savepoint():
                self.env["bf.live.team"].create({
                    "name": "Doublon",
                    "survey_id": self.sondage.id,
                    "answer_id": self.reponse_bleu.id,
                })

    # ------------------------------------------------------------
    # Les couleurs
    # ------------------------------------------------------------

    def test_chaque_couleur_dequipe_porte_une_encre_lisible(self):
        """Une étiquette d'équipe se lit de loin ou ne sert à rien : 4,5:1."""
        for cle, _nom in COULEURS:
            equipe = self.env["bf.live.team"].create({
                "name": "Essai %s" % cle, "survey_id": self.sondage.id,
                "color_key": cle,
            })
            self.assertGreaterEqual(
                contrast_ratio(equipe.color, equipe.text_color), 4.5,
                "la couleur %s ne porte pas d'encre lisible" % cle)

    def test_les_equipes_du_tirage_ont_des_couleurs_distinctes(self):
        for index in range(6):
            self.participant("joueur%s" % index)
        self.sondage.bf_live_team_target = MAX_EQUIPES
        self.sondage.action_bf_draw_teams()
        couleurs = self.sondage.bf_live_team_ids.mapped("color")
        self.assertEqual(len(set(couleurs)), MAX_EQUIPES)

    def test_le_compte_des_membres_suit_les_participations(self):
        for index in range(4):
            self.participant("joueur%s" % index, points=10)
        self.sondage.bf_live_team_target = 2
        self.sondage.action_bf_draw_teams()
        for equipe in self.sondage.bf_live_team_ids:
            self.assertEqual(equipe.member_count, 2)
            self.assertEqual(equipe.score_total, 20)
