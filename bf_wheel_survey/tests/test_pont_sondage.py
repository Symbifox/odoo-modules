# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPontSondage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sondage = cls.env["survey.survey"].create({
            "title": "Quiz du midi",
            "survey_type": "live_session",
            "access_mode": "public",
            "questions_layout": "page_per_question",
            "question_and_page_ids": [(0, 0, {
                "title": "Quelle est ta boisson ?",
                "question_type": "simple_choice",
                "suggested_answer_ids": [
                    (0, 0, {"value": "Café"}),
                    (0, 0, {"value": "Thé"}),
                ],
            })],
        })
        cls.question = cls.sondage.question_and_page_ids[0]
        cls.roue = cls.env["bf.wheel"].create({
            "name": "Tirage du quiz",
            "source": "session",
            "survey_id": cls.sondage.id,
        })

    def _ouvrir_seance(self):
        # 🔴 `create_date` vaut l'heure de la TRANSACTION, pas celle de l'appel :
        # dans un essai, une participation créée après ce write porte quand même
        # l'heure d'ouverture du curseur, donc AVANT un `session_start_time` pris
        # à l'horloge. La séance s'ouvre donc une heure en arrière. En production,
        # chaque requête est sa propre transaction et la question ne se pose pas.
        self.sondage.write({
            "session_state": "in_progress",
            "session_start_time": fields.Datetime.now() - timedelta(hours=1),
        })

    def _participant(self, pseudo, **extra):
        valeurs = {
            "survey_id": self.sondage.id,
            "is_session_answer": True,
            "nickname": pseudo,
        }
        valeurs.update(extra)
        return self.env["survey.user_input"].create(valeurs)

    # ------------------------------------------------------------------
    # La séance en direct
    # ------------------------------------------------------------------
    def test_une_seance_pas_commencee_le_dit(self):
        with self.assertRaises(UserError):
            self.roue.action_spin()

    def test_la_roue_tire_parmi_les_participants(self):
        self._ouvrir_seance()
        for pseudo in ("Mimi", "Ous", "Lan"):
            self._participant(pseudo)
        resultat = self.roue.action_spin()
        self.assertEqual(
            sorted(c["label"] for c in resultat["candidates"]), ["Lan", "Mimi", "Ous"])
        self.assertIn(resultat["winner_label"], ["Lan", "Mimi", "Ous"])
        spin = self.env["bf.wheel.spin"].browse(resultat["spin_id"])
        self.assertEqual(spin.source, "session")
        self.assertEqual(spin.candidate_count, 3)
        self.assertFalse(spin.segment_id, "un participant n'est pas un segment")

    def test_chaque_participant_a_sa_couleur(self):
        """Sans segment, un candidat n'a pas de couleur : la roue serait unie."""
        self._ouvrir_seance()
        for pseudo in ("Mimi", "Ous", "Lan", "Gab"):
            self._participant(pseudo)
        couleurs = [c["color"] for c in self.roue.get_display_data()["candidates"]]
        self.assertEqual(len(set(couleurs)), 4, couleurs)
        self.assertTrue(all(c.startswith("#") for c in couleurs))

    def test_qui_rejoint_entre_deux_tours_entre_dans_le_second(self):
        self._ouvrir_seance()
        self._participant("Mimi")
        self.assertEqual(len(self.roue.action_spin()["candidates"]), 1)
        self._participant("Ous")
        self.assertEqual(len(self.roue.action_spin()["candidates"]), 2)

    def test_la_salle_d_hier_ne_compte_pas(self):
        """`session_start_time` borne la liste à la séance en cours."""
        self._ouvrir_seance()
        vieux = self._participant("Fantôme")
        vieux.flush_recordset()
        # Reculer la participation d'un jour, donc avant le début de la séance.
        self.env.cr.execute(
            "UPDATE survey_user_input SET create_date = create_date - interval '1 day' "
            "WHERE id = %s", (vieux.id,))
        vieux.invalidate_recordset()
        self._participant("Mimi")
        candidats = [c["label"] for c in self.roue.get_display_data()["candidates"]]
        self.assertEqual(candidats, ["Mimi"])

    def test_un_participant_sans_pseudonyme_n_a_pas_de_part(self):
        self._ouvrir_seance()
        self._participant(False)
        self._participant("Mimi")
        self.assertEqual(
            [c["label"] for c in self.roue.get_display_data()["candidates"]], ["Mimi"])

    def test_deux_fois_le_meme_pseudonyme_ne_vaut_pas_deux_chances(self):
        self._ouvrir_seance()
        self._participant("Mimi")
        self._participant("Mimi")
        self.assertEqual(len(self.roue.get_display_data()["candidates"]), 1)

    def test_une_entree_d_essai_ne_participe_pas(self):
        self._ouvrir_seance()
        self._participant("Sonde", test_entry=True)
        self._participant("Mimi")
        self.assertEqual(
            [c["label"] for c in self.roue.get_display_data()["candidates"]], ["Mimi"])

    # ------------------------------------------------------------------
    # Les réponses d'une question
    # ------------------------------------------------------------------
    def _repondre(self, valeur):
        participation = self._participant("p%s" % valeur)
        choix = self.question.suggested_answer_ids.filtered(lambda a: a.value == valeur)
        return self.env["survey.user_input.line"].create({
            "user_input_id": participation.id,
            "question_id": self.question.id,
            "answer_type": "suggestion",
            "suggested_answer_id": choix.id,
        })

    def test_la_roue_tire_parmi_les_reponses(self):
        self.roue.write({"source": "answers", "question_id": self.question.id})
        self._repondre("Café")
        self._repondre("Café")
        self._repondre("Thé")
        candidats = self.roue.get_display_data()["candidates"]
        self.assertEqual(sorted(c["label"] for c in candidats), ["Café", "Thé"])
        self.assertEqual({c["weight"] for c in candidats}, {1},
                         "sans pondération, une réponse vaut une part")

    def test_la_part_suit_le_nombre_quand_on_le_demande(self):
        self.roue.write({"source": "answers", "question_id": self.question.id,
                         "weight_by_count": True})
        self._repondre("Café")
        self._repondre("Café")
        self._repondre("Thé")
        poids = {c["label"]: c["weight"]
                 for c in self.roue.get_display_data()["candidates"]}
        self.assertEqual(poids, {"Café": 2, "Thé": 1})

    def test_sans_question_la_roue_le_dit(self):
        self.roue.write({"source": "answers", "question_id": False})
        with self.assertRaises(UserError):
            self.roue.action_spin()

    # ------------------------------------------------------------------
    # Le bouton du sondage
    # ------------------------------------------------------------------
    def test_le_bouton_du_sondage_reutilise_la_roue(self):
        action = self.sondage.action_bf_wheel_session()
        self.assertEqual(action["tag"], "bf_wheel.projector")
        self.assertEqual(action["params"]["wheel_id"], self.roue.id,
                         "le bouton ne crée pas une deuxième roue pour le même sondage")

    def test_le_bouton_cree_la_roue_quand_il_n_y_en_a_pas(self):
        autre = self.env["survey.survey"].create({
            "title": "Quiz du soir", "survey_type": "live_session"})
        action = autre.action_bf_wheel_session()
        roue = self.env["bf.wheel"].browse(action["params"]["wheel_id"])
        self.assertEqual(roue.survey_id, autre)
        self.assertEqual(roue.source, "session")
