# -*- coding: utf-8 -*-
from odoo import fields, models


class SurveyUserInput(models.Model):
    _inherit = "survey.user_input"

    bf_team_id = fields.Many2one(
        "bf.live.team", string="Équipe", ondelete="set null", index=True)

    def _save_lines(self, question, answer, comment=None, overwrite_existing=True):
        """Rien à enregistrer sur une section, et une réponse d'équipe y entre.

        🔴 La première garde est celle de la trame. Le cœur finit son
        `_save_lines` par `raise AttributeError(question.question_type + …)`, et
        `question_type` vaut False sur une SECTION : c'est donc une TypeError
        (`bool + str`) qui remonte, en 500, sur le téléphone du participant. Le
        cœur n'y arrive jamais seul, parce qu'il ne met au mur que des sections
        DÉCRITES, dont l'écran du participant n'offre aucun bouton. La trame,
        elle, projette des diapositives sans description : le téléphone y
        tombait dans la branche « question » du cœur, avec son bouton
        « Soumettre » et sa touche Entrée armée. Mesuré au banc avant le
        correctif : `builtins.TypeError`.

        La garde reste ici même maintenant que le bouton a disparu : un écran
        resté ouvert sur l'écran d'avant, ou un appel direct à la route, poste
        encore ce que son HTML portait.

        ⚠️ L'équipe se CHERCHE, elle ne se crée pas ici : elle a été figée à
        l'ouverture de la séance. Si elle manque (une réponse ajoutée en cours de
        séance), le participant reste sans équipe et le tableau le compte à part,
        plutôt que de faire tomber sa réponse sur une contrainte d'unicité.
        """
        if question.is_page:
            return
        resultat = super()._save_lines(
            question, answer, comment=comment, overwrite_existing=overwrite_existing)
        if question.bf_save_as_team and question.question_type == "simple_choice":
            ligne = self.env["survey.user_input.line"].sudo().search([
                ("user_input_id", "=", self.id),
                ("question_id", "=", question.id),
            ], limit=1)
            equipe = self.env["bf.live.team"].sudo().search([
                ("survey_id", "=", question.survey_id.id),
                ("answer_id", "=", ligne.suggested_answer_id.id),
            ], limit=1) if ligne.suggested_answer_id else None
            if equipe:
                self.sudo().bf_team_id = equipe.id
        return resultat
