# -*- coding: utf-8 -*-
"""Le questionnaire rempli depuis la page avise le responsable du mandat."""
from odoo import _, models


class SurveyUserInput(models.Model):
    _inherit = "survey.user_input"

    def _mark_done(self):
        # Seulement les réponses qui FINISSENT ici : une réponse déjà finie
        # repassée par `_mark_done` n'est pas un geste neuf du client.
        a_finir = self.filtered(lambda reponse: reponse.state != "done")
        resultat = super()._mark_done()
        Projet = self.env["project.project"].sudo()
        # ⚠️ Un cœur antérieur à 2.8.0 n'a pas l'avis : `depends` ne porte pas de
        # plancher de version, et un AttributeError ici rendait un 500 au client
        # qui soumet son questionnaire.
        if a_finir and hasattr(Projet, "_progression_aviser"):
            for projet in Projet.search(
                    [("progression_survey_answer_id", "in", a_finir.ids)]):
                reponse = projet._progression_survey_reponse()
                # La réponse que le mandat SERT : une réponse détachée parce que
                # le questionnaire du mandat a changé n'est plus la sienne.
                if reponse and reponse in a_finir:
                    projet._progression_aviser(
                        "survey", reponse_id=reponse.id,
                        delai=bool(reponse.survey_time_limit_reached))
        return resultat


class ProjectProject(models.Model):
    _inherit = "project.project"

    def _progression_avis_textes(self, evenement, valeurs):
        if evenement != "survey":
            return super()._progression_avis_textes(evenement, valeurs)
        projet = self.sudo()
        # Dans la langue du responsable (le contexte de l'appel), et non dans
        # celle du client qui a répondu.
        reponse = self.env["survey.user_input"].sudo().with_context(
            lang=self.env.context.get("lang")).browse(valeurs.get("reponse_id")).exists()
        questionnaire = reponse.survey_id.title if reponse else _("the questionnaire")
        if valeurs.get("delai"):
            # La limite de temps a fermé la réponse : le client ne l'a pas remise.
            return {
                "sujet": _("Questionnaire closed by its time limit: %(survey)s",
                           survey=questionnaire),
                "ligne": _("The time limit of the questionnaire %(survey)s was "
                           "reached; the client's answer is closed.",
                           survey=questionnaire),
                "lignes": [_(
                    "The time limit of the questionnaire %(survey)s was reached "
                    "on the progress page of the mandate %(project)s; the "
                    "client's answer is closed. It is in Odoo.",
                    survey=questionnaire, project=projet.display_name)],
            }
        return {
            "sujet": _("Questionnaire completed: %(survey)s", survey=questionnaire),
            "ligne": _("The client completed the questionnaire %(survey)s from the "
                       "progress page.", survey=questionnaire),
            "lignes": [_(
                "The client completed the questionnaire %(survey)s from the "
                "progress page of the mandate %(project)s. The answers are in Odoo.",
                survey=questionnaire, project=projet.display_name)],
        }
