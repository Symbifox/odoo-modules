"""L'exécution SCORM : servir le paquet, et tenir le modèle de données.

Le contenu d'un paquet SCORM tourne dans une iframe et cherche son LMS en
remontant la chaîne des fenêtres parentes jusqu'à trouver un objet nommé
``API`` (SCORM 1.2) ou ``API_1484_11`` (SCORM 2004). Cet objet est du JavaScript
côté navigateur ; ce contrôleur est ce qu'il appelle pour persister.
"""
import json
import logging
import mimetypes

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

BASE = "/bf_training_scorm"


def _json(data, status=200):
    return request.make_response(
        json.dumps(data, default=str),
        headers=[("Content-Type", "application/json; charset=utf-8")],
        status=status,
    )


def _paquet_et_tentative(package_id):
    """Le paquet que l'appelant a le droit de jouer, et sa tentative.

    🔴 La garde n'est PAS sur le paquet : elle est sur le CONTENU qui le porte.
    `slide.slide` a ses propres règles — un cours privé, un cours payant, un
    membre ou non — et les rejouer ici à la main les ferait diverger au premier
    changement du natif. On lit donc la diapositive avec les droits de
    l'appelant : si elle est refusée, le paquet l'est aussi.
    """
    paquet = request.env["bf.scorm.package"].sudo().browse(int(package_id))
    if not paquet.exists() or not paquet.slide_id:
        return None, None
    try:
        paquet.slide_id.check_access("read")
    except Exception:  # noqa: BLE001
        return None, None
    partenaire = request.env.user.partner_id
    if not partenaire:
        return None, None
    tentative = request.env["bf.scorm.attempt"]._pour(paquet, partenaire)
    return paquet, tentative


class ScormRuntime(http.Controller):

    # ------------------------------------------------------------------
    # Servir les fichiers du paquet
    # ------------------------------------------------------------------
    @http.route(f"{BASE}/content/<int:package_id>/<path:chemin>",
                type="http", auth="user", methods=["GET"], csrf=False)
    def contenu(self, package_id, chemin, **kw):
        paquet, _tentative = _paquet_et_tentative(package_id)
        if not paquet:
            return request.not_found()
        donnees = paquet._lire_fichier(chemin)
        if donnees is None:
            return request.not_found()
        type_mime, _enc = mimetypes.guess_type(chemin)
        return request.make_response(donnees, headers=[
            ("Content-Type", type_mime or "application/octet-stream"),
            # ⚠️ Pas de cache : un paquet remplacé sous le même identifiant
            # servirait l'ancien contenu depuis le navigateur, et l'apprenant
            # referait une version qui n'existe plus.
            ("Cache-Control", "no-store"),
        ])

    @http.route(f"{BASE}/launch/<int:package_id>", type="http", auth="user",
                methods=["GET"], csrf=False)
    def lancement(self, package_id, **kw):
        """La page qui porte l'API et l'iframe du contenu."""
        paquet, tentative = _paquet_et_tentative(package_id)
        if not paquet:
            return request.not_found()
        return request.render("bf_training_scorm.scorm_player", {
            "paquet": paquet,
            "tentative": tentative,
            "url_contenu": f"{BASE}/content/{paquet.id}/{paquet.launch_href}",
        })

    # ------------------------------------------------------------------
    # Le modèle de données
    # ------------------------------------------------------------------
    @http.route(f"{BASE}/cmi/get", type="json", auth="user", methods=["POST"])
    def cmi_get(self, package_id, **kw):
        paquet, tentative = _paquet_et_tentative(package_id)
        if not paquet:
            return {"error": "forbidden"}
        return {"ok": True, "data": tentative._donnees(),
                "version": paquet.version or "1.2"}

    @http.route(f"{BASE}/cmi/set", type="json", auth="user", methods=["POST"])
    def cmi_set(self, package_id, values=None, **kw):
        paquet, tentative = _paquet_et_tentative(package_id)
        if not paquet:
            return {"error": "forbidden"}
        if not isinstance(values, dict):
            return {"error": "bad_request"}
        # ⚠️ On n'accepte que des clés CMI, et seulement des valeurs scalaires.
        # Un contenu hostile — ou simplement bogué — pousserait sinon des objets
        # imbriqués dans un champ qu'on relira en JSON.
        propres = {
            str(cle): ("" if valeur is None else str(valeur))
            for cle, valeur in values.items()
            if isinstance(cle, str) and cle.startswith("cmi.")
            and not isinstance(valeur, (dict, list))
        }
        tentative._ecrire(propres)
        return {"ok": True, "completed": tentative.completed}
