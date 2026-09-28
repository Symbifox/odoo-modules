"""L'exécution SCORM : servir le paquet, et tenir le modèle de données.

Le contenu d'un paquet SCORM tourne dans une iframe et cherche son LMS en
remontant la chaîne des fenêtres jusqu'à trouver un objet nommé ``API``
(SCORM 1.2) ou ``API_1484_11`` (SCORM 2004).

🔴 Le contenu d'un paquet est du HTML et du JavaScript ARBITRAIRES, déposés par
un gestionnaire de formation. Servi depuis le domaine d'Odoo sans cloison, il
tournait avec la session de qui l'ouvrait — administrateur compris — et pouvait
appeler ``/web/dataset/call_kw`` en son nom. Il est donc servi :

* avec l'en-tête ``Content-Security-Policy: sandbox allow-scripts allow-forms
  allow-popups`` : sans ``allow-same-origin``, le navigateur lui donne une
  origine OPAQUE, étrangère à Odoo — il ne lit ni les pages ni les réponses
  d'Odoo, et la cloison tient même si on ouvre l'adresse hors de l'iframe ;
* sous une adresse qui porte un jeton signé, lié à l'usager et daté, plutôt que
  sous la session : une origine opaque n'envoie pas (ou pas partout) le témoin
  de session, et le contenu n'en a de toute façon pas besoin.

Conséquence : le contenu ne peut plus toucher ``parent.API``. L'API est donc
posée DANS chaque page HTML du paquet (le script ``scorm_sco.js``, injecté au
service avec les données CMI de la tentative), et les écritures remontent à la
page du lecteur par ``postMessage`` ; c'est elle, avec la session, qui persiste.
"""
import json
import logging
import mimetypes
import re
import time

from odoo import http
from odoo.http import request
from odoo.tools import consteq, file_open
from odoo.tools.misc import hmac as odoo_hmac

_logger = logging.getLogger(__name__)

BASE = "/bf_training_scorm"

PORTEE_JETON = "bf_training_scorm.contenu"
# Une séance de formation tient dans la journée ; un jeton plus long ne sert
# qu'à qui l'aurait copié.
DUREE_JETON = 12 * 3600
CSP_CONTENU = "sandbox allow-scripts allow-forms allow-popups"
TYPES_HTML = ("text/html", "application/xhtml+xml")
_TETE = re.compile(rb"<head\b[^>]*>", re.IGNORECASE)


def _jeton(paquet, uid, expire=None):
    expire = int(expire or time.time() + DUREE_JETON)
    signature = odoo_hmac(request.env(su=True), PORTEE_JETON,
                          f"{paquet.id}:{uid}:{expire}")
    return f"{uid}.{expire}.{signature}"


def _lire_jeton(package_id, jeton):
    """L'usager que le jeton désigne pour ce paquet, ou None."""
    try:
        uid, expire, signature = jeton.split(".")
        uid, expire = int(uid), int(expire)
    except ValueError:
        return None
    if expire < time.time():
        return None
    attendue = odoo_hmac(request.env(su=True), PORTEE_JETON,
                         f"{package_id}:{uid}:{expire}")
    if not consteq(signature, attendue):
        return None
    usager = request.env["res.users"].sudo().browse(uid).exists()
    if not usager or not usager.active:
        return None
    return usager


def _cloison(type_mime):
    return [
        ("Content-Type", type_mime or "application/octet-stream"),
        ("Content-Security-Policy", CSP_CONTENU),
        ("X-Content-Type-Options", "nosniff"),
        # Le jeton est dans l'adresse : il ne part pas chez un tiers.
        ("Referrer-Policy", "no-referrer"),
        # ⚠️ Pas de cache : un paquet remplacé sous le même identifiant
        # servirait l'ancien contenu depuis le navigateur, et l'apprenant
        # referait une version qui n'existe plus.
        ("Cache-Control", "no-store"),
    ]


def _avec_api(donnees, tentative, paquet):
    """La page HTML du paquet, avec l'API SCORM posée avant tout son code."""
    etat = json.dumps({
        "packageId": paquet.id,
        "version": paquet.version or "1.2",
        "data": tentative._donnees(),
    }).replace("<", "\\u003c")
    with file_open("bf_training_scorm/static/src/js/scorm_sco.js") as f:
        pont = f.read()
    bloc = (f"<script>window.__bfScorm = {etat};</script>"
            f"<script>{pont}</script>").encode()
    trouve = _TETE.search(donnees)
    if trouve:
        return donnees[:trouve.end()] + bloc + donnees[trouve.end():]
    return bloc + donnees


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
    @http.route(f"{BASE}/sco/<int:package_id>/<string:jeton>/<path:chemin>",
                type="http", auth="public", methods=["GET"], csrf=False)
    def contenu(self, package_id, jeton, chemin, **kw):
        usager = _lire_jeton(package_id, jeton)
        if not usager:
            return request.not_found()
        paquet = request.env["bf.scorm.package"].sudo().browse(package_id).exists()
        if not paquet or not paquet.slide_id:
            return request.not_found()
        try:
            paquet.slide_id.with_user(usager).check_access("read")
        except Exception:  # noqa: BLE001
            return request.not_found()
        donnees = paquet._lire_fichier(chemin)
        if donnees is None:
            return request.not_found()
        type_mime, _enc = mimetypes.guess_type(chemin)
        if type_mime in TYPES_HTML:
            tentative = request.env["bf.scorm.attempt"]._pour(
                paquet, usager.partner_id)
            donnees = _avec_api(donnees, tentative, paquet)
        return request.make_response(donnees, headers=_cloison(type_mime))

    # ⚠️ `website=True` : `web.frontend_layout`, sous `website` (que l'eLearning
    # installe toujours), lit `website` dans le contexte de rendu. Sans lui, la
    # page du lecteur tombait en erreur 500 avant même d'afficher l'iframe.
    @http.route(f"{BASE}/launch/<int:package_id>", type="http", auth="user",
                methods=["GET"], csrf=False, website=True)
    def lancement(self, package_id, **kw):
        """La page qui porte l'iframe du contenu et reçoit ses écritures."""
        paquet, tentative = _paquet_et_tentative(package_id)
        if not paquet:
            return request.not_found()
        return request.render("bf_training_scorm.scorm_player", {
            "paquet": paquet,
            "tentative": tentative,
            "url_contenu": f"{BASE}/sco/{paquet.id}/"
                           f"{_jeton(paquet, request.env.uid)}/{paquet.launch_href}",
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
