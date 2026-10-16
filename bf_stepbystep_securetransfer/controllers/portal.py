# -*- coding: utf-8 -*-
"""Les routes du dépôt par S3, branchées sur la page du portail Progression.

Chaque route repasse par la garde du portail (projet, jeton, publication), puis
par celle de l'envoi : il appartient à ce projet, il est en cours, et son jeton
à lui est juste. Le jeton du brouillon de transfert ne sort jamais.
"""
import logging

import psycopg2

from odoo import _, http
from odoo.exceptions import UserError
from odoo.http import request

from odoo.addons.bf_stepbystep_clients.controllers.portal import ProgressionPortail
from odoo.addons.bf_stepbystep_clients.models.progression_document import DepotRefuse

from ..models.progression_upload import EnvoiRefuse, s3_pret

_logger = logging.getLogger(__name__)


def _erreur(code, message=None):
    return {"error": code, "message": message or ""}


class ProgressionPortailS3(ProgressionPortail):

    @classmethod
    def _progression_mode_de_depot(cls, projet):
        return "s3" if s3_pret(request.env) else super()._progression_mode_de_depot(projet)

    def _progression_envoi(self, project_id, token, upload_id, upload_token):
        projet = self._progression_projet(project_id, token)
        if projet is None:
            return None, None
        # La langue du client AVANT de lire l'envoi : la note du versement la suit.
        projet = self._progression_langue(projet)
        envoi = request.env["bf.progression.upload"].sudo().browse(upload_id).exists()
        if (not envoi or envoi.project_id != projet or envoi.state != "uploading"
                or not envoi._verifier_jeton(upload_token)):
            return projet, None
        return projet, envoi

    @staticmethod
    def _progression_appel(fonction):
        """Les refus connus sortent avec leur code ; le reste, sans trace.

        Chaque opération tourne dans un point de sauvegarde : une erreur annule ce
        qu'elle avait écrit (brouillon, envoi, dépôt), au lieu de le laisser à
        moitié fait dans une transaction que la route valide quand même.

        Le visiteur ne lit que les textes écrits pour lui (refus du dépôt, envoi
        incomplet) : ceux du socle parlent de point d'accès, de seau ou de clés.

        🔴 Les erreurs de concurrence de PostgreSQL (accès sérialisé, verrou non
        obtenu) REMONTENT : Odoo relance alors toute la requête sur un instantané
        neuf, qui compte juste. Les avaler rendait un refus au client (et, pour
        `complete`, fermait son envoi) au lieu de rejouer.
        """
        try:
            with request.env.cr.savepoint():
                return fonction()
        except psycopg2.OperationalError:
            raise
        except DepotRefuse as refus:
            return _erreur(refus.code, str(refus))
        except EnvoiRefuse as refus:
            return _erreur("storage", str(refus))
        except UserError:
            _logger.warning("Portail Progression : dépôt S3 refusé par le stockage", exc_info=True)
            return _erreur("storage", _("The file could not be stored. Try again later."))
        except Exception:
            _logger.exception("Portail Progression : échec du dépôt S3")
            return _erreur("server", _("An error occurred. Try again later."))

    @http.route("/progression/<int:project_id>/<string:token>/s3/start",
                type="json", auth="public", methods=["POST"])
    def progression_s3_start(self, project_id, token, document_id=None,
                             filename=None, size=None, **kw):
        projet = self._progression_projet(project_id, token)
        if projet is None:
            return _erreur("not_found")
        projet = self._progression_langue(projet)
        document = self._progression_document_permis(projet, document_id)
        if document is None:
            return _erreur("not_found")
        try:
            taille = int(size)
        except (TypeError, ValueError):
            taille = 0

        def demarrer():
            envoi, plan = request.env["bf.progression.upload"].sudo()._demarrer(
                projet, document, filename, taille, ip=request.httprequest.remote_addr)
            reponse = {"upload_id": envoi.id, "upload_token": envoi.token, "mode": plan["mode"]}
            if plan["mode"] == "simple":
                reponse.update(url=plan["url"], headers=plan.get("headers") or {})
            else:
                reponse.update(part_size=plan["part_size"], parts_total=plan["parts_total"])
            return reponse
        return self._progression_appel(demarrer)

    @http.route("/progression/<int:project_id>/<string:token>/s3/<int:upload_id>/sign",
                type="json", auth="public", methods=["POST"])
    def progression_s3_sign(self, project_id, token, upload_id, upload_token=None,
                            parts=None, **kw):
        projet, envoi = self._progression_envoi(project_id, token, upload_id, upload_token)
        if envoi is None:
            return _erreur("not_found")
        if not isinstance(parts, list):
            return _erreur("storage", _("Invalid part numbers."))
        return self._progression_appel(
            lambda: {"urls": envoi.file_id.sudo().mpu_sign(parts)})

    @http.route("/progression/<int:project_id>/<string:token>/s3/<int:upload_id>/complete",
                type="json", auth="public", methods=["POST"])
    def progression_s3_complete(self, project_id, token, upload_id, upload_token=None, **kw):
        projet, envoi = self._progression_envoi(project_id, token, upload_id, upload_token)
        if envoi is None:
            return _erreur("not_found")

        def terminer():
            envoi._terminer(ip=request.httprequest.remote_addr)
            return {"ok": True}
        reponse = self._progression_appel(terminer)
        if "error" in reponse:
            # Le point de sauvegarde a tout annulé ; l'envoi, lui, est clos.
            envoi._marquer_en_echec()
        return reponse
