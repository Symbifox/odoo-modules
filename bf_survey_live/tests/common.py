# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields
from odoo.tests import common


class BfLiveCommon(common.TransactionCase):
    """Un sondage en séance, déjà ouvert, avec une question notée et une ouverte.

    ⚠️ `session_start_time` est posé une heure en ARRIÈRE, et ce n'est pas un
    détail d'essai : `create_date` vaut l'heure de la TRANSACTION, donc une
    participation créée ici porterait l'heure d'ouverture du curseur, avant une
    séance ouverte « maintenant », et la salle paraîtrait vide.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sondage = cls.env["survey.survey"].create({
            "title": "Atelier du mardi",
            "survey_type": "live_session",
            "scoring_type": "scoring_without_answers",
            "session_state": "in_progress",
            "session_start_time": fields.Datetime.now() - timedelta(hours=1),
        })
        cls.question_choix = cls.env["survey.question"].create({
            "survey_id": cls.sondage.id,
            "title": "Quelle couleur",
            "sequence": 1,
            "question_type": "simple_choice",
            "suggested_answer_ids": [
                (0, 0, {"value": "Bleu", "answer_score": 10}),
                (0, 0, {"value": "Vert", "answer_score": 0}),
            ],
        })
        cls.reponse_bleu = cls.question_choix.suggested_answer_ids[0]
        cls.reponse_vert = cls.question_choix.suggested_answer_ids[1]
        cls.question_texte = cls.env["survey.question"].create({
            "survey_id": cls.sondage.id,
            "title": "Un mot sur la rencontre",
            "sequence": 2,
            "question_type": "text_box",
            "bf_live_screen": "word_cloud",
        })

    def participant(self, pseudonyme, points=0, test_entry=False):
        participation = self.env["survey.user_input"].create({
            "survey_id": self.sondage.id,
            "is_session_answer": True,
            "nickname": pseudonyme,
            "test_entry": test_entry,
        })
        if points:
            self.env["survey.user_input.line"].create({
                "user_input_id": participation.id,
                "question_id": self.question_choix.id,
                "answer_type": "suggestion",
                "suggested_answer_id": self.reponse_bleu.id,
                "answer_score": points,
            })
        return participation

    def reponse_texte(self, participation, texte):
        return self.env["survey.user_input.line"].create({
            "user_input_id": participation.id,
            "question_id": self.question_texte.id,
            "answer_type": "text_box",
            "value_text_box": texte,
        })
