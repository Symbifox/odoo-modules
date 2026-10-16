# -*- coding: utf-8 -*-
"""La page que le client ouvre sans compte : `/progression/<projet>/<jeton>`.

Trois gardes, et toutes répondent la même chose (404) : projet absent, jeton
faux, portail fermé. Distinguer les cas renseignerait un visiteur sur
l'existence d'un projet.

⚠️ Le jeton voyage dans le chemin : la page ne charge rien d'externe, et
l'en-tête `Referrer-Policy: no-referrer` empêche qu'un lien sortant le
transmette. `X-Robots-Tag` tient la page hors des moteurs, au cas où l'adresse
se retrouverait collée quelque part de public.
"""
import logging

from werkzeug.utils import redirect

from odoo import http
from odoo.http import request

from ..models.progression_document import DepotRefuse
from ..models.project_project import jetons_egaux

_logger = logging.getLogger(__name__)

# L'enveloppe multipart d'un dépôt (séparateurs, en-têtes, jeton CSRF) tient
# largement là-dedans.
MARGE_FORMULAIRE = 64 * 1024

EN_TETES = [
    ("Referrer-Policy", "no-referrer"),
    ("X-Robots-Tag", "noindex, nofollow"),
    ("Cache-Control", "no-store"),
]


class ProgressionPortail(http.Controller):

    @staticmethod
    def _progression_projet(project_id, token):
        """Le projet en sudo si les gardes passent, sinon None.

        Un projet archivé ne répond plus : le mandat est clos, son adresse aussi.
        """
        projet = request.env["project.project"].sudo().browse(project_id).exists()
        if (not projet or not projet.active or not token
                or not projet.progression_portal_published):
            return None
        if not jetons_egaux(projet.progression_portal_token or "", token):
            return None
        return projet

    @staticmethod
    def _progression_document_permis(projet, document_id):
        """La pièce, si elle est active et attendue à une étape faite ou en cours
        de ce projet ; sinon None. Le dépôt direct et le pont S3 passent par ici."""
        try:
            document_id = int(document_id)
        except (TypeError, ValueError):
            return None
        document = request.env["bf.progression.document"].sudo().browse(document_id).exists()
        if not document or not document.active:
            return None
        permises = [e for e in projet._progression_portal_steps()
                    if e["state"] in ("done", "current")]
        if not any(document.stage_id.id in e["stage_ids"] for e in permises):
            return None
        return document

    # Les messages qu'affiche la page après un dépôt. La clé voyage dans
    # l'adresse : jamais de texte libre venu du visiteur.
    MESSAGES = ("received", "missing", "type", "empty", "too_large", "rate")

    @classmethod
    def _progression_valeurs(cls, projet):
        """Ce que le gabarit reçoit. Un module pont étend ce dict, pas la garde."""
        etapes = projet._progression_portal_steps()
        for etape in etapes:
            etape["documents"] = projet._progression_portal_documents(etape)
        return {
            "project": projet,
            "partner": projet.partner_id,
            "company": projet.company_id or request.env.company,
            "steps": etapes,
            "token": projet.progression_portal_token,
            "deposit_mode": cls._progression_mode_de_depot(projet),
            "max_upload_mb": request.env["bf.progression.deposit"].sudo()._plafond_octets() // (1024 * 1024),
        }

    @classmethod
    def _progression_mode_de_depot(cls, projet):
        """« direct » : le fichier passe par Odoo. Un module pont (S3) le change."""
        return "direct"

    @staticmethod
    def _progression_langue(projet):
        """La langue du client du mandat, posée sur la REQUÊTE."""
        langue = projet.partner_id.lang
        actives = [code for code, _nom in request.env["res.lang"].get_installed()]
        if langue in actives and request.env.context.get("lang") != langue:
            request.update_context(lang=langue)
        return projet.with_context(lang=request.env.context.get("lang"))

    @http.route(
        ["/progression/<int:project_id>/<string:token>"],
        # website=True : là où le module `website` est installé (la démo), il étend
        # la mise en page publique et attend `website` dans le rendu ; sans lui, 500.
        # Sans `website`, le drapeau est ignoré.
        type="http", auth="public", methods=["GET"], website=True, sitemap=False)
    def progression_page(self, project_id, token, **kw):
        projet = self._progression_projet(project_id, token)
        if projet is None:
            return request.not_found()
        # La langue du client du mandat d'abord : c'est lui qui lit la page. Elle
        # passe dans le contexte de la REQUÊTE, sinon les libellés du gabarit
        # suivraient le navigateur et les données la fiche.
        projet = self._progression_langue(projet)
        try:
            valeurs = self._progression_valeurs(projet)
            message = kw.get("message")
            valeurs["message"] = message if message in self.MESSAGES else None
        except Exception:
            # ⚠️ Route PUBLIQUE : une donnée aberrante ne rend pas un 500 au
            # visiteur. On refuse comme le reste et on le journalise.
            _logger.exception("Portail Progression illisible pour le projet %s", project_id)
            return request.not_found()
        reponse = request.render(
            "bf_stepbystep_clients.progression_portal_page", valeurs,
            headers=EN_TETES)
        return reponse

    @http.route(
        ["/progression/<int:project_id>/<string:token>/deposit/<int:document_id>"],
        type="http", auth="public", methods=["POST"], sitemap=False)
    def progression_deposit(self, project_id, token, document_id, **kw):
        """Le client dépose une pièce. Même garde que la page, puis la pièce doit
        être attendue à une étape FAITE ou EN COURS de CE projet."""
        projet = self._progression_projet(project_id, token)
        if projet is None:
            return request.not_found()
        # La note au chatter suit la langue du client, comme la page.
        projet = self._progression_langue(projet)
        retour = "/progression/%s/%s" % (projet.id, token)
        document = self._progression_document_permis(projet, document_id)
        if document is None:
            return request.not_found()
        Depot = request.env["bf.progression.deposit"].sudo()
        plafond = Depot._plafond_octets()
        # Un corps plus gros que le plafond (plus l'enveloppe du formulaire) est
        # refusé avant d'en lire le fichier.
        longueur = request.httprequest.content_length
        if longueur and longueur > plafond + MARGE_FORMULAIRE:
            return redirect(retour + "?message=too_large#doc-%s" % document.id, code=303)
        fichier = request.httprequest.files.get("file")
        if not fichier or not fichier.filename:
            return redirect(retour + "?message=missing#doc-%s" % document.id, code=303)
        # On lit un octet de plus que le plafond : assez pour refuser sans tout
        # charger en mémoire.
        contenu = fichier.stream.read(plafond + 1)
        try:
            Depot._verser(projet, document, fichier.filename, contenu,
                          ip=request.httprequest.remote_addr)
        except DepotRefuse as refus:
            return redirect(retour + "?message=%s#doc-%s" % (refus.code, document.id), code=303)
        return redirect(retour + "?message=received#doc-%s" % document.id, code=303)
