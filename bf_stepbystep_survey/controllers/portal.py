# -*- coding: utf-8 -*-
"""La route du bouton « questionnaire » de la page de progression.

🔴 Même garde que la page, et elle n'est pas recopiée : `_progression_projet`
du cœur est appelée telle quelle (projet existant, actif, publié, jeton juste,
et 404 pour tous les refus sans distinguer les cas).

🔴 La route est en POST, avec le jeton CSRF du formulaire : le geste CRÉE la
réponse du mandat, et un préchargeur de liens, un antivirus de courriel ou un
robot qui suit une adresse ne doit rien créer.

Le nom de la classe est celui du cœur. Ce n'est pas obligatoire — Odoo combine
les feuilles de l'arbre des contrôleurs, quel que soit leur nom, et un autre pont
nomme la sienne autrement — mais il garde l'ordre de
MRO lisible quand deux ponts surchargent la même méthode.
"""
import logging

from psycopg2 import IntegrityError, OperationalError
from werkzeug.utils import redirect

from odoo import http
from odoo.exceptions import UserError
from odoo.http import request

from odoo.addons.bf_stepbystep_clients.controllers.portal import ProgressionPortail

_logger = logging.getLogger(__name__)


class ProgressionPortail(ProgressionPortail):

    @classmethod
    def _progression_valeurs(cls, projet):
        valeurs = super()._progression_valeurs(projet)
        # Un seul calcul d'état par rendu : trois recherches sur les réponses
        # pour une page, c'était deux de trop.
        etat = projet._progression_survey_etat()
        valeurs["survey_done"] = etat == "fini"
        valeurs["survey_url"] = projet._progression_survey_adresse() if etat == "ouvert" else ""
        # L'étape qui demande le questionnaire ; 0 = sous le chapeau de la page.
        valeurs["survey_stage_id"] = projet.sudo().progression_survey_stage_id.id
        return valeurs

    @classmethod
    def _progression_questionnaire_a_l_etape(cls, projet, etapes):
        """L'étape qui demande le questionnaire doit être VISIBLE au client et
        faite ou en cours.

        Sans étape nommée, le bouton est au chapeau de la page : il n'y a rien à
        vérifier. Avec une étape, c'est la règle du dépôt de pièces du cœur, pour
        que les deux chemins publics du module se gardent pareil — le client
        n'ouvre pas d'avance ce qu'on lui demandera plus tard.
        """
        etape = projet.sudo().progression_survey_stage_id
        if not etape:
            return True
        return any(etape.id in e["stage_ids"] for e in etapes
                   if e["state"] in ("done", "current"))

    @http.route(
        ["/progression/<int:project_id>/<string:token>/questionnaire"],
        type="http", auth="public", methods=["POST"], website=True, sitemap=False)
    def progression_questionnaire(self, project_id, token, **kw):
        """Mène le client à LA réponse de son mandat, créée au premier geste.

        ⚠️ Une redirection, et non le questionnaire rendu ici : le rendu, la
        reprise à la bonne page et l'enregistrement des réponses sont au module
        `survey`, et les refaire les ferait diverger de lui.
        """
        projet = self._progression_projet(project_id, token)
        if projet is None:
            return request.not_found()
        try:
            # La langue du client d'abord, comme la page : la bascule vit sur la
            # REQUÊTE, et le questionnaire la suit.
            projet = self._progression_langue(projet)
            valeurs = self._progression_valeurs(projet)
            if not self._progression_questionnaire_a_l_etape(projet, valeurs["steps"]):
                return request.not_found()
            # Le partenaire de la réponse est celui que la PAGE nomme : le
            # bénéficiaire quand un pont le pose, le donneur d'ordre sinon.
            reponse = projet._progression_survey_ouvrir(
                partenaire=valeurs.get("portal_client"))
        except UserError:
            # État de configuration ordinaire (questionnaire fermé ou archivé
            # entre deux gestes) : un refus, pas une trace de 500 par clic.
            return request.not_found()
        except (IntegrityError, OperationalError):
            # 🔴 AVANT le filet à tout faire, et c'est TOUTE la protection contre
            # deux gestes simultanés : `retrying()` d'Odoo ne rejoue que ce qui
            # LÈVE. Avalée ici, une `SerializationFailure` rendait 404 au client
            # sur un double-clic légitime, et le verrou du modèle ne corrigeait
            # rien. Mesuré par `test_un_geste_concurrent_est_rejoue`.
            raise
        except Exception:
            # ⚠️ Route PUBLIQUE, comme la page : une donnée aberrante ne rend
            # pas un 500 au visiteur.
            _logger.exception(
                "Questionnaire de progression illisible pour le projet %s", project_id)
            return request.not_found()
        if not reponse:
            # Fini, que ce soit tout à l'heure dans un autre onglet ou par le
            # lien du courriel que la page vient de reprendre : la page le dit.
            # Sans jeton de réponse dans l'adresse, seulement celui de la page.
            if projet._progression_survey_etat() == "fini":
                return redirect("/progression/%s/%s" % (projet.id, token), code=303)
            return request.not_found()
        return redirect("/survey/%s/%s" % (
            reponse.survey_id.sudo().access_token, reponse.access_token), code=303)
