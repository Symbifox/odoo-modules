# -*- coding: utf-8 -*-
import json

import werkzeug.exceptions

from odoo import http
from odoo.http import request

from odoo.addons.survey.controllers.survey_session_manage import UserInputSession


class BfLiveUserInputSession(UserInputSession):
    """Les trois écrans de plus, greffés sur la séance du cœur.

    Le nuage et le tableau tirent leurs données de leurs propres routes, et non
    de `/survey/session/results`, pour ne rien devoir à la forme de la charge
    utile du cœur : le rafraîchissement de la barre de progression reste le sien,
    le nuage est le nôtre.
    """

    def _prepare_manage_session_values(self, survey):
        """Le premier affichage porte déjà son nuage.

        ⚠️ La clé est posée MÊME sans question en cours : le gabarit la lit sur
        la balise racine, et QWeb tombe sur un nom inconnu, pas sur une valeur
        vide. Le cœur, lui, rend un dictionnaire vide quand la séance est close.
        """
        valeurs = super()._prepare_manage_session_values(survey)
        question = survey.session_question_id
        nuage = []
        if question and question._bf_shows_word_cloud():
            nuage = question._bf_word_cloud(self._bf_live_lines(survey, question))
        valeurs["bf_live_screen"] = question.bf_live_screen if question else "standard"
        valeurs["bf_shows_word_cloud"] = bool(
            question and question._bf_shows_word_cloud())
        valeurs["bf_word_cloud_json"] = json.dumps(nuage)
        valeurs["bf_shows_written_answers"] = bool(
            question and question._bf_shows_written_answers())
        valeurs["bf_live_scoreboard_mode"] = survey.bf_live_scoreboard
        valeurs["bf_live_podium"] = survey.bf_live_podium
        # La trame : la diapositive au mur, s'il y en a une, et le compteur
        # d'écrans. Les trois clés sont posées MÊME hors trame, pour la même
        # raison que celles du nuage : QWeb tombe sur un nom inconnu.
        valeurs["bf_slide"] = (
            question if question and question._bf_is_slide() else False)
        trame = survey._bf_frame_values()
        valeurs["bf_frame_position"] = trame["position"]
        valeurs["bf_frame_total"] = trame["total"]
        valeurs["bf_frame_counter"] = bool(
            survey.bf_live_frame_counter and trame["slides"] and trame["position"])
        return valeurs

    def _prepare_question_results_values(self, survey, user_input_lines):
        """Les réponses d'un texte long ou d'un nombre, dans la charge du cœur.

        Le cœur ne remplit `input_line_values` que pour `char_box`, `date` et
        `datetime` : pour les deux autres types, son cadre restait vide pendant
        que la salle répondait. On ne touche ni à la route, ni au cadre, ni au
        composant du navigateur : on remplit la MÊME clé, et son cadre affiche.

        ⚠️ Le filtre sur la question n'est pas de la décoration : la route du
        cœur cherche les lignes de la question en cours, mais cette méthode est
        aussi appelée au premier affichage avec un recordset vide, et un jour
        avec autre chose. Elle ne garde que les lignes de la question au mur.
        """
        valeurs = super()._prepare_question_results_values(survey, user_input_lines)
        question = survey.session_question_id
        if not question or not question._bf_shows_written_answers():
            return valeurs
        lignes = user_input_lines.filtered(
            lambda ligne: ligne.question_id == question)
        valeurs["input_line_values"] = question._bf_written_answers(lignes)
        return valeurs

    def _bf_live_lines(self, survey, question):
        """Les réponses à la question en cours, bornées à la séance en cours.

        🔴 Et aux vraies : le cœur pose `is_session_answer=True` sur un essai du
        sondage ouvert pendant la séance, donc un « Test » de l'animateur
        envoyait ses mots au nuage, au milieu de ceux de la salle. Le module le
        promet déjà ailleurs : `_bf_live_participations` et le podium écartent
        `test_entry`.
        """
        Ligne = request.env["survey.user_input.line"].sudo()
        if not survey.session_start_time:
            return Ligne.browse()
        return Ligne.search([
            ("survey_id", "=", survey.id),
            ("question_id", "=", question.id),
            ("skipped", "=", False),
            ("user_input_id.test_entry", "=", False),
            ("create_date", ">=", survey.session_start_time),
        ])

    @http.route("/bf_survey_live/word_cloud/<string:survey_token>",
                type="json", auth="user", website=True)
    def bf_live_word_cloud(self, survey_token, **kwargs):
        """Le nuage de la question en cours, recompté à chaque appel."""
        survey = self._fetch_from_token(survey_token)
        if not survey or survey.session_state != "in_progress":
            return False
        question = survey.session_question_id
        if not question or not question._bf_shows_word_cloud():
            return False
        return {
            "words": question._bf_word_cloud(self._bf_live_lines(survey, question)),
            "answer_count": survey.session_question_answer_count,
            "attendees_count": survey.session_answer_count,
        }

    @http.route("/bf_survey_live/scoreboard/<string:survey_token>",
                type="json", auth="user", website=True)
    def bf_live_scoreboard(self, survey_token, final=False, **kwargs):
        """Le tableau projeté, rendu au serveur comme le classement du cœur."""
        survey = self._fetch_from_token(survey_token)
        if not survey:
            return ""
        tableau = survey._bf_live_scoreboard(final=final)
        # Le podium est le dernier écran : il ne s'affiche que si le sondage le
        # demande, et seulement à la fin. Partout ailleurs, c'est le tableau.
        gabarit = ("bf_survey_live.scoreboard_podium"
                   if tableau["final"] and survey.bf_live_podium
                   else "bf_survey_live.scoreboard_board")
        return request.env["ir.qweb"]._render(gabarit, {"board": tableau})

    @http.route("/bf_survey_live/teams/<string:survey_token>",
                type="json", auth="user", website=True)
    def bf_live_teams(self, survey_token, **kwargs):
        """La bande des équipes de l'écran d'attente, rendue au serveur.

        Rafraîchie à la même cadence que le compte des participants du cœur, et
        pour la même raison : le tirage se fait au bureau, pendant que l'écran
        d'attente est DÉJÀ projeté. Une bande rendue au seul chargement de la
        page n'aurait montré les équipes à personne.
        """
        survey = self._fetch_from_token(survey_token)
        if not survey or not survey.session_state:
            return ""
        return request.env["ir.qweb"]._render(
            "bf_survey_live.team_strip", {"survey": survey})

    @http.route("/bf_survey_live/slide_image/<string:survey_token>/<int:section_id>",
                type="http", auth="public", website=True, sitemap=False)
    def bf_live_slide_image(self, survey_token, section_id):
        """L'image d'une diapositive, servie sous le jeton du sondage.

        La même porte que l'image de fond du cœur, et pour la même raison :
        l'écran projeté la demande en HTTP, et le jeton du sondage est ce qui
        ouvre la séance. Passer par `/web/image` demanderait à l'animateur des
        droits de lecture sur le modèle, qu'il n'a pas forcément sur le sondage
        de quelqu'un d'autre.
        """
        sondage = request.env["survey.survey"].sudo().search(
            [("access_token", "=", survey_token)], limit=1)
        section = sondage.page_ids.filtered(lambda page: page.id == section_id)
        if not section:
            # une section qui n'est pas de ce sondage
            raise werkzeug.exceptions.Forbidden()
        return request.env["ir.binary"]._get_image_stream_from(
            section, "bf_slide_image").get_response()
