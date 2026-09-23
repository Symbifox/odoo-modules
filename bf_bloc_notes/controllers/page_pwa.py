"""La page ``/notes`` : le bloc-notes au pouce, installable à l'écran d'accueil.

Au téléphone, une application de notes à part gagnait sur le bloc-notes, parce
qu'elle s'ouvrait sur un champ de saisie et Odoo sur une liste. Cette page renverse l'ordre : elle s'ouvre SUR le
champ, garde ce qu'on tape même hors ligne, et envoie quand le réseau revient.

Ce que la page fait, et comment :

* **Saisie d'abord.** Le champ a le focus à l'ouverture ; la liste vient dessous.
* **Hors ligne.** La coquille (cette page, sa feuille, son script) est gardée par
  l'agent de service. Une note tapée sans réseau entre dans une file locale
  avec un identifiant tiré par la page, et le serveur rend la même note à
  chaque renvoi (``client_uuid``) : la file rejoue sans jamais dédoubler.
* **Menu Partager.** Le manifeste déclare une cible de partage : un texte ou un
  lien partagé depuis une autre application ouvre la page avec la saisie
  pré-remplie.
* **Gestes rapides.** Épingler, rappel aujourd'hui ou demain, tâche, rattacher à
  une fiche, archiver (avec annulation). Ce sont les méthodes ``_mobile_*`` de
  ``bf.note`` : les mêmes que l'API à jeton de Symbifox Mobile.

🔴 La coquille ne porte AUCUNE donnée de l'usager : elle est gardée dans le
cache du navigateur, qui survit à la déconnexion. Les notes arrivent par les
routes JSON, et le cache local des notes est effacé quand la session change
d'usager (``uid`` porté par la réponse de la liste).

⚠️ Routes JSON en ``type="json"`` : la prévérification CORS qu'elles imposent
empêche de rejouer le témoin de session depuis un autre site.
"""

import json
import logging

from markupsafe import Markup, escape

from odoo import _, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

_logger = logging.getLogger(__name__)

STATIQUES = "/bf_bloc_notes/static/src/pwa"

#: Accent de repli, la couleur de la marque maison.
ACCENT_DEFAUT = "#29ABE2"

#: ⚠️ Les adresses d'icônes portent la version : Odoo sert ses statiques avec
#: `max-age=604800`, et une page installée garderait sinon l'ancienne icône une
#: semaine. `_manifeste()` pose la version à la requête.
ICONES = [
    ("icon-192.png", "192x192", "any"),
    ("icon-512.png", "512x512", "any"),
    ("icon-maskable-192.png", "192x192", "maskable"),
    ("icon-maskable-512.png", "512x512", "maskable"),
]

SERVICE_WORKER = """\
/* Agent de service du bloc-notes.

   Il garde la coquille, page comprise : c'est ce qui permet d'ouvrir le
   bloc-notes et d'y taper sans réseau. La page ne porte aucune donnée de
   l'usager, les notes passent par les routes JSON, jamais mises en cache ici.

   🔴 Le nom du cache porte la version du module : un nom fixe garderait
   l'ancienne feuille de style pour toujours sur une page déjà installée. */
const CACHE = 'bf-notes-%(version)s';
const SHELL = [
  '%(statiques)s/notes.css?v=%(version)s',
  '%(statiques)s/notes.js?v=%(version)s',
  '%(statiques)s/icon-192.png?v=%(version)s',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE)
    .then((c) => c.addAll(SHELL).then(() => c.add('/notes')))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith('bf-notes-') && k !== CACHE)
      .map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== self.location.origin) { return; }
  if (SHELL.includes(url.pathname + url.search)) {
    e.respondWith(caches.match(e.request).then((r) => r || fetch(e.request)));
    return;
  }
  if (url.pathname === '/notes') {
    /* Réseau d'abord : en ligne, la page fraîche (et sa copie gardée est
       remplacée). Hors ligne, la copie gardée, quels que soient les
       paramètres (un partage arrive avec ?texte=…). Une redirection vers
       l'écran de connexion n'est PAS gardée : elle remplacerait la page. */
    e.respondWith(fetch(e.request).then((r) => {
      if (r.ok && !r.redirected) {
        const copie = r.clone();
        caches.open(CACHE).then((c) => c.put('/notes', copie));
      }
      return r;
    }).catch(() => caches.match('/notes')));
  }
});
"""


def _version():
    """La version installée du module, cassure de cache des statiques."""
    module = request.env["ir.module.module"].sudo().search(
        [("name", "=", "bf_bloc_notes")], limit=1)
    return (module.installed_version or "0").replace(".", "-")


def _manifeste():
    version = _version()
    return {
        # ``id`` fixe l'identité de l'application : sans lui, le lanceur la
        # dérive de ``start_url`` et installerait une seconde icône le jour où
        # l'adresse change.
        "id": "/notes",
        "name": _("Notes"),
        "short_name": _("Notes"),
        "description": _("Write a note in one second, even offline."),
        "start_url": "/notes",
        "scope": "/notes",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#12161a",
        "theme_color": "#12161a",
        "lang": (request.env.lang or "fr_CA").replace("_", "-"),
        "icons": [
            {"src": "%s/%s?v=%s" % (STATIQUES, nom, version), "sizes": taille,
             "type": "image/png", "purpose": usage}
            for nom, taille, usage in ICONES
        ],
        # Le menu Partager d'Android : un texte ou un lien partagé depuis une
        # autre application ouvre la page avec la saisie pré-remplie.
        "share_target": {
            "action": "/notes",
            "method": "GET",
            "params": {"title": "titre", "text": "texte", "url": "lien"},
        },
        # Appui long sur l'icône : droit à une note neuve.
        "shortcuts": [{
            "name": _("New note"),
            "short_name": _("New"),
            "url": "/notes?nouvelle=1",
            "icons": [{"src": "%s/icon-192.png?v=%s" % (STATIQUES, version),
                       "sizes": "192x192", "type": "image/png"}],
        }],
    }


def _encre_sur(couleur):
    """Noir ou blanc, celui des deux qui se lit sur la couleur d'accent.

    🔴 L'encre ne peut pas être une constante : l'accent d'origine d'Odoo est un
    prune foncé (#714B67), sur lequel une encre bleu nuit tombe à 1,6:1.
    """
    couleur = (couleur or "").strip()
    if len(couleur) == 4:
        couleur = "#" + "".join(c * 2 for c in couleur[1:])
    try:
        canaux = [int(couleur[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
    except (ValueError, IndexError):
        return "#ffffff"

    def lineaire(canal):
        return canal / 12.92 if canal <= 0.04045 else ((canal + 0.055) / 1.055) ** 2.4

    luminance = (0.2126 * lineaire(canaux[0])
                 + 0.7152 * lineaire(canaux[1])
                 + 0.0722 * lineaire(canaux[2]))
    return "#0b0f13" if (luminance + 0.05) / 0.05 >= 1.05 / (luminance + 0.05) else "#ffffff"


def _accent():
    """L'accent de la marque du locataire, ou celui de la maison.

    ⚠️ Le champ vit dans un module de marque qui n'est pas une dépendance d'ici.
    """
    societe = request.env.company.sudo()
    accent = ""
    if "report_brand_primary" in societe._fields:
        accent = (societe.report_brand_primary or "").strip()
    if not (accent.startswith("#") and len(accent) in (4, 7)):
        accent = ACCENT_DEFAUT
    return accent


def _mots():
    """Les phrases du SCRIPT, traduites par le serveur.

    🔴 Écrites en dur dans le ``.js``, elles resteraient dans une seule langue
    alors que le reste de la page suit celle de l'usager (relevé sur
    ``/capture`` en préparant les captures de la vitrine).
    """
    return {
        "placeholder": _("Write a note…"),
        "title_placeholder": _("Title (optional)"),
        "save": _("Save"),
        "saved": _("Saved."),
        "saved_offline": _("Kept on this device. It will be sent when the network is back."),
        "pending": _("To send"),
        "search": _("Search notes"),
        "empty": _("No notes yet. The first one starts above."),
        "offline": _("Offline: your notes stay on this device."),
        "pin": _("Pin"),
        "unpin": _("Unpin"),
        "today": _("Reminder today"),
        "tomorrow": _("Reminder tomorrow"),
        "task": _("Make it a task"),
        "reroute": _("Link to a record"),
        "archive": _("Archive"),
        "undo": _("Undo"),
        "archived": _("Note archived."),
        "open": _("Open in Symbifox"),
        "read_only": _("This note has formatting that the phone cannot keep. "
                       "Read it here, edit it in Symbifox."),
        "close": _("Close"),
        "cancel": _("Cancel"),
        "choose_project": _("Choose the project"),
        "choose_record": _("Find the record (name, number, pasted URL)"),
        "no_result": _("No result."),
        "conflict": _("This note was changed elsewhere while you were editing it."),
        "keep_mine": _("Keep mine"),
        "take_theirs": _("Take the other version"),
        "session_expired": _("Your session has expired. Reload the page to sign in."),
        "needs_network": _("This gesture needs the network."),
        "error": _("The server refused:"),
        "actions": _("Actions"),
        "links": _("Linked to"),
    }


def _erreur(exc, defaut):
    """Une phrase lisible pour la page, jamais une trace."""
    return {"error": exc.args[0] if exc.args else defaut}


def _garde(fn):
    """Même contrat que l'API à jeton : erreur en phrase, écriture annulée.

    🔴 Une route JSON qui RENVOIE une erreur (au lieu de la lever) est commitée
    par Odoo : sans le retour en arrière, un geste refusé à mi-chemin laisserait
    sa première moitié en base.
    """
    try:
        return fn()
    except AccessError as exc:
        request.env.cr.rollback()
        return _erreur(exc, _("Access denied."))
    except UserError as exc:
        request.env.cr.rollback()
        return _erreur(exc, _("Refused."))
    except Exception:  # noqa: BLE001
        request.env.cr.rollback()
        _logger.exception("bf_bloc_notes : échec d'une route de la page /notes")
        return {"error": _("The server could not complete the request.")}


class BfNotePage(http.Controller):

    @http.route("/notes", type="http", auth="user", methods=["GET"],
                website=False, sitemap=False)
    def page(self, **kw):
        if request.env.user.share:
            # Un compte de portail n'a pas de bloc-notes.
            return request.redirect("/my")
        accent = _accent()
        html = Markup("""<!DOCTYPE html>
<html lang="%(langue)s">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content"/>
<meta name="theme-color" content="#12161a"/>
<title>%(titre)s</title>
<link rel="manifest" href="/notes/manifest.webmanifest"/>
<link rel="icon" href="%(statiques)s/icon-192.png?v=%(version)s"/>
<link rel="apple-touch-icon" href="%(statiques)s/icon-192.png?v=%(version)s"/>
<link rel="stylesheet" href="%(statiques)s/notes.css?v=%(version)s"/>
<style>:root { --brand-primary: %(accent)s; --brand-ink: %(encre)s; }</style>
</head>
<body>
<header>
  <h1>%(titre)s</h1>
  <span id="reseau" class="reseau hidden"></span>
</header>
<main>
  <form id="saisie" class="saisie" autocomplete="off">
    <input id="titre" type="text" maxlength="200"/>
    <textarea id="texte" rows="3" enterkeyhint="enter"></textarea>
    <div class="saisie-pied">
      <span id="etat" class="etat" aria-live="polite"></span>
      <button id="garder" class="principal" type="submit"></button>
    </div>
  </form>
  <input id="recherche" class="recherche" type="search"/>
  <ul id="liste" class="liste"></ul>
  <p id="vide" class="vide hidden"></p>
</main>
<div id="toast" class="toast hidden" role="status"></div>
<dialog id="fiche" class="fiche"></dialog>
<script type="application/json" id="i18n">%(mots)s</script>
<script src="%(statiques)s/notes.js?v=%(version)s"></script>
</body>
</html>""") % {
            "titre": escape(_("Notes")),
            "accent": escape(accent),
            "encre": escape(_encre_sur(accent)),
            "statiques": STATIQUES,
            "version": _version(),
            # ⚠️ `</` n'apparaît jamais dans du JSON produit par json.dumps sauf
            # dans une chaîne ; l'échapper empêche une traduction contenant
            # `</script>` de fermer le bloc.
            "mots": Markup(json.dumps(_mots(), ensure_ascii=False).replace("</", "<\\/")),
            "langue": (request.env.lang or "fr_CA").replace("_", "-"),
        }
        return request.make_response(html, headers=[
            ("Content-Type", "text/html; charset=utf-8"),
            # Hors des caches PARTAGÉS ; l'agent de service de la page, lui,
            # la garde pour l'ouverture hors ligne (elle ne porte aucune donnée).
            ("Cache-Control", "private, no-cache"),
        ])

    @http.route("/notes/manifest.webmanifest", type="http", auth="public",
                methods=["GET"], sitemap=False)
    def manifest(self, **kw):
        return request.make_response(json.dumps(_manifeste(), ensure_ascii=False), headers=[
            ("Content-Type", "application/manifest+json; charset=utf-8"),
            ("Cache-Control", "public, max-age=300"),
        ])

    @http.route("/notes/sw.js", type="http", auth="public", methods=["GET"],
                sitemap=False)
    def service_worker(self, **kw):
        # ⚠️ `Service-Worker-Allowed` : sans cet en-tête, l'agent servi depuis
        # `/notes/sw.js` ne pourrait pas revendiquer la portée `/notes`.
        return request.make_response(
            SERVICE_WORKER % {"statiques": STATIQUES, "version": _version()},
            headers=[
                ("Content-Type", "application/javascript; charset=utf-8"),
                ("Service-Worker-Allowed", "/notes"),
                ("Cache-Control", "no-cache"),
            ])

    # ── Routes JSON de la page ─────────────────────────────────────────

    @http.route("/notes/api/liste", type="json", auth="user", methods=["POST"])
    def api_liste(self, limit=50, offset=0, archived=False, q=None, since=None, **kw):
        def run():
            resultat = request.env["bf.note"]._mobile_list(
                limit=limit, offset=offset, archived=archived, query=q, since=since)
            resultat["uid"] = request.env.uid
            return resultat
        return _garde(run)

    @http.route("/notes/api/creer", type="json", auth="user", methods=["POST"])
    def api_creer(self, **kw):
        return _garde(lambda: request.env["bf.note"]._mobile_create(kw))

    @http.route("/notes/api/modifier", type="json", auth="user", methods=["POST"])
    def api_modifier(self, id=None, **kw):
        def run():
            note = request.env["bf.note"]._mobile_browse(id)
            if not note:
                return {"error": _("This note no longer exists, or you do not have access to it.")}
            return note._mobile_update(kw)
        return _garde(run)

    @http.route("/notes/api/geste", type="json", auth="user", methods=["POST"])
    def api_geste(self, id=None, action=None, **kw):
        def run():
            note = request.env["bf.note"]._mobile_browse(id)
            if not note:
                return {"error": _("This note no longer exists, or you do not have access to it.")}
            return note._mobile_action(action, kw)
        return _garde(run)

    @http.route("/notes/api/projets", type="json", auth="user", methods=["POST"])
    def api_projets(self, q=None, **kw):
        return _garde(lambda: {"projets": request.env["bf.note"]._mobile_projects(q)})

    @http.route("/notes/api/cibles", type="json", auth="user", methods=["POST"])
    def api_cibles(self, q=None, **kw):
        return _garde(lambda: {"groupes": request.env["bf.note"]._mobile_targets(q)})
