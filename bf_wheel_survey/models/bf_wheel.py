# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BfWheel(models.Model):
    _inherit = "bf.wheel"

    source = fields.Selection(
        selection_add=[
            ("session", "Les participants de la séance en direct"),
            ("answers", "Les réponses d'une question"),
        ],
        ondelete={"session": "set default", "answers": "set default"},
    )
    survey_id = fields.Many2one("survey.survey", string="Sondage")
    question_id = fields.Many2one(
        "survey.question", string="Question",
        domain="[('survey_id', '=', survey_id), ('is_page', '=', False)]")
    weight_by_count = fields.Boolean(
        string="Une part d'autant plus large que la réponse revient", default=False,
        help="La roue devient la carte de ce que la salle a répondu : une réponse "
             "donnée dix fois occupe dix parts.")
    session_state = fields.Selection(related="survey_id.session_state", string="Séance")

    @api.onchange("survey_id")
    def _onchange_survey_id(self):
        if self.question_id.survey_id != self.survey_id:
            self.question_id = False

    def _candidates(self):
        self.ensure_one()
        if self.source == "session":
            return self._candidates_from_session()
        if self.source == "answers":
            return self._candidates_from_answers()
        return super()._candidates()

    def _candidates_from_session(self):
        """Les gens qui ont rejoint la séance en cours, un pseudonyme par part.

        ⚠️ Relu à CHAQUE tour : qui rejoint entre deux tirages entre dans le
        second. Et la séance d'avant ne compte pas : `session_start_time` borne
        la liste, sinon le tirage de ce midi pêcherait dans la salle d'hier.
        """
        sondage = self.survey_id
        if not sondage:
            raise UserError(_("La roue « %(name)s » ne dit pas de quel sondage "
                              "elle tire ses participants.", name=self.name))
        if not sondage.session_start_time:
            raise UserError(_(
                "La séance de « %(sondage)s » n'a pas commencé : personne n'a "
                "encore rejoint.", sondage=sondage.title))
        # Les pseudonymes d'une séance s'affichent déjà au classement devant toute
        # la salle ; les lire en sudo n'ouvre rien de plus, et évite d'exiger les
        # droits de Sondages de qui tient la roue.
        participations = self.env["survey.user_input"].sudo().search([
            ("survey_id", "=", sondage.id),
            ("is_session_answer", "=", True),
            ("create_date", ">=", sondage.session_start_time),
            ("test_entry", "=", False),
        ], order="create_date, id")
        candidats = []
        vus = set()
        for participation in participations:
            nom = (participation.nickname or "").strip() or (
                participation.partner_id.name or "").strip()
            if not nom or nom in vus:
                # Sans pseudonyme, personne à nommer à l'écran : la part serait
                # muette. Et deux fois le même nom ne vaut pas deux chances.
                continue
            vus.add(nom)
            candidats.append({"label": nom, "weight": 1, "color": False, "segment_id": False})
        return candidats

    def _candidates_from_answers(self):
        """Une part par réponse donnée à la question, dans l'ordre du sondage."""
        question = self.question_id
        if not question:
            raise UserError(_("La roue « %(name)s » ne dit pas de quelle question "
                              "elle tire ses réponses.", name=self.name))
        lignes = self.env["survey.user_input.line"].sudo().search([
            ("question_id", "=", question.id),
            ("skipped", "=", False),
        ])
        comptes = {}
        for ligne in lignes:
            etiquette = self._answer_label(ligne)
            if not etiquette:
                continue
            comptes[etiquette] = comptes.get(etiquette, 0) + 1
        return [{
            "label": etiquette,
            "weight": compte if self.weight_by_count else 1,
            "color": False,
            "segment_id": False,
        } for etiquette, compte in comptes.items()]

    @api.model
    def _answer_label(self, ligne):
        """Ce qu'une réponse écrit sur une part, quel que soit son type."""
        if ligne.suggested_answer_id:
            return (ligne.suggested_answer_id.value or "").strip()
        for champ in ("value_char_box", "value_text_box"):
            valeur = (ligne[champ] or "").strip() if ligne[champ] else ""
            if valeur:
                return valeur
        if ligne.answer_type == "numerical_box":
            return ("%g" % ligne.value_numerical_box)
        if ligne.answer_type == "scale":
            return str(ligne.value_scale)
        if ligne.answer_type == "date" and ligne.value_date:
            return fields.Date.to_string(ligne.value_date)
        if ligne.answer_type == "datetime" and ligne.value_datetime:
            return fields.Datetime.to_string(ligne.value_datetime)
        return ""
