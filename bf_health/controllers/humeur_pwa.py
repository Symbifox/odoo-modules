"""La page ``/healthy-fox/humeur`` : l'humeur du jour au pouce.

Patron : ``/notes`` de ``bf_bloc_notes`` (page autonome sans paquet d'actifs,
manifeste et agent de service servis ici, icônes versionnées, sombre par
dessein, accent de la société et encre calculée).

* **Saisie d'abord.** Cinq visages, les activités en pastilles, une note
  facultative, Enregistrer. Quelques secondes.
* **Hors ligne.** La coquille est gardée par l'agent de service ; une saisie
  faite sans réseau attend dans une file locale (par usager) et part au retour
  du réseau. ``client_uuid`` : le serveur rend la même saisie à chaque renvoi.
* **Aucune série de jours.** La page montre la semaine, jamais un compteur.

🔴 La coquille ne porte AUCUNE donnée de la personne : elle survit à la
déconnexion dans le cache du navigateur. L'humeur passe par les routes JSON.

🔴 Les routes JSON tournent sous la session web de la personne : c'est le canal
« écran » du verrou de Gen. Un appel par l'API (XML-RPC, jeton) ne lit rien.

⚠️ Routes JSON en ``type="json"`` : la prévérification CORS qu'elles imposent
empêche de rejouer le témoin de session depuis un autre site.
"""
import csv
import io
import json
import logging
import re
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import _, fields, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import content_disposition, request

from ..models.health_mood import NIVEAUX, VISAGES

_logger = logging.getLogger(__name__)

RACINE = "/healthy-fox/humeur"
STATIQUES = "/bf_health/static/src/humeur"
ACCENT_DEFAUT = "#29ABE2"
FOND = "#12161a"

ICONES = [
    ("icon-192.png", "192x192", "any"),
    ("icon-512.png", "512x512", "any"),
    ("icon-maskable-192.png", "192x192", "maskable"),
    ("icon-maskable-512.png", "512x512", "maskable"),
]

SERVICE_WORKER = """\
/* Agent de service de la page Humeur de Healthy Fox.

   Il garde la coquille, page comprise : elle s'ouvre et accepte une saisie
   sans réseau. La page ne porte aucune donnée de la personne ; l'humeur passe
   par les routes JSON, jamais mises en cache ici.

   Le nom du cache porte la version du module, et l'activation ne supprime
   QUE les caches de cette page (le stockage est par origine : /notes et
   /capture ont les leurs). */
const PREFIXE = 'bf-healthy-fox-humeur-';
const CACHE = PREFIXE + '%(version)s';
const PAGE = '%(racine)s';
const SHELL = [
  '%(statiques)s/humeur.css?v=%(version)s',
  '%(statiques)s/humeur.js?v=%(version)s',
  '%(statiques)s/icon-192.png?v=%(version)s',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE)
    .then((c) => c.addAll(SHELL).then(() => c.add(PAGE)))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith(PREFIXE) && k !== CACHE)
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
  if (url.pathname === PAGE) {
    /* Réseau d'abord ; hors ligne, la copie gardée. Une redirection vers
       l'écran de connexion n'est PAS gardée : elle remplacerait la page. */
    e.respondWith(fetch(e.request).then((r) => {
      if (r.ok && !r.redirected) {
        const copie = r.clone();
        caches.open(CACHE).then((c) => c.put(PAGE, copie));
      }
      return r;
    }).catch(() => caches.match(PAGE)));
  }
});
"""


def _version():
    module = request.env["ir.module.module"].sudo().search([("name", "=", "bf_health")], limit=1)
    return (module.latest_version or module.installed_version or "0").replace(".", "-")


def _manifeste():
    version = _version()
    return {
        "id": RACINE,
        "name": _("Healthy Fox : humeur"),
        "short_name": _("Humeur"),
        "description": _("Votre humeur du jour en quelques secondes, même hors ligne."),
        "start_url": RACINE,
        "scope": RACINE,
        "display": "standalone",
        "orientation": "portrait",
        "background_color": FOND,
        "theme_color": FOND,
        "lang": (request.env.lang or "fr_CA").replace("_", "-"),
        "icons": [
            {"src": "%s/%s?v=%s" % (STATIQUES, nom, version), "sizes": taille,
             "type": "image/png", "purpose": usage}
            for nom, taille, usage in ICONES
        ],
    }


def _encre_sur(couleur):
    """Noir ou blanc, celui des deux qui se lit sur l'accent (motif /notes)."""
    couleur = (couleur or "").strip()
    if len(couleur) == 4:
        couleur = "#" + "".join(c * 2 for c in couleur[1:])
    try:
        canaux = [int(couleur[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
    except (ValueError, IndexError):
        return "#ffffff"

    def lineaire(canal):
        return canal / 12.92 if canal <= 0.04045 else ((canal + 0.055) / 1.055) ** 2.4

    luminance = (0.2126 * lineaire(canaux[0]) + 0.7152 * lineaire(canaux[1])
                 + 0.0722 * lineaire(canaux[2]))
    return "#0b0f13" if (luminance + 0.05) / 0.05 >= 1.05 / (luminance + 0.05) else "#ffffff"


def _accent():
    """L'accent de la marque du locataire, ou celui de la maison. Le champ vit
    dans un module de marque qui n'est pas une dépendance d'ici."""
    societe = request.env.company.sudo()
    accent = ""
    if "report_brand_primary" in societe._fields:
        accent = (societe.report_brand_primary or "").strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{3}([0-9A-Fa-f]{3})?", accent or ""):
        accent = ACCENT_DEFAUT
    return accent


def _mots():
    """Les phrases du script, traduites par le serveur."""
    libelles = dict(request.env["health.mood.entry"]._fields["level"]._description_selection(request.env))
    return {
        "question": _("Comment ça va ?"),
        "niveaux": {code: libelles.get(code, nom) for code, nom in NIVEAUX},
        "visages": VISAGES,
        "activities": _("Qu'avez-vous fait ?"),
        "note": _("Une note (facultatif)"),
        "add_note": _("Ajouter une note"),
        "save": _("Enregistrer"),
        "saved": _("Enregistré. Merci !"),
        "saved_offline": _("Gardé sur cet appareil. L'envoi se fera au retour du réseau."),
        "choose": _("Choisissez d'abord un visage."),
        "week": _("Cette semaine"),
        "nothing": _("Rien de saisi"),
        "offline": _("Hors ligne : vos saisies restent sur cet appareil."),
        "pending": _("À envoyer"),
        "session_expired": _("Votre session a expiré. Rechargez la page pour vous reconnecter."),
        "error": _("Le serveur a refusé :"),
        "journal": _("Mon journal et corrélations"),
        "export": _("Exporter (CSV)"),
        "reminder": _("Rappel quotidien à %s"),
        "reminder_off": _("Rappel quotidien désactivé"),
        "private": _("Privé : personne d'autre ne voit ce journal, administrateurs compris."),
        "today": _("Aujourd'hui"),
    }


def _garde(fn):
    """Erreur en phrase, écriture annulée (une route JSON qui RENVOIE une
    erreur est commitée par Odoo)."""
    try:
        return fn()
    except (AccessError, UserError, ValidationError) as exc:
        request.env.cr.rollback()
        return {"error": exc.args[0] if exc.args else _("Refusé.")}
    except Exception:  # noqa: BLE001
        request.env.cr.rollback()
        _logger.exception("bf_health : échec d'une route de la page Humeur")
        return {"error": _("Le serveur n'a pas pu terminer la demande.")}


def _heure(valeur):
    heures = int(valeur or 0)
    minutes = int(round(((valeur or 0) - heures) * 60))
    if minutes == 60:
        heures, minutes = heures + 1, 0
    return "%02d:%02d" % (heures % 24, minutes)


def _etat():
    """Ce que la page affiche : activités, semaine, rappel. Sous les droits de
    la personne (rien en sudo)."""
    env = request.env
    reglages = env["health.mood.settings"]._bf_mes_reglages()
    aujourd_hui = fields.Date.context_today(reglages)
    Activity = env["health.mood.activity"]
    activites = Activity.search([("create_uid", "=", env.uid)])
    saisies = env["health.mood.entry"].search(
        [("create_uid", "=", env.uid), ("date", ">=", aujourd_hui - timedelta(days=6))],
        order="date desc, id desc")
    jours = []
    for i in range(7):
        jour = aujourd_hui - timedelta(days=i)
        du_jour = saisies.filtered(lambda e, j=jour: e.date == j)
        jours.append({
            "date": fields.Date.to_string(jour),
            "label": _("Aujourd'hui") if i == 0 else jour.strftime("%d/%m"),
            "levels": du_jour.mapped("level"),
        })
    return {
        "uid": env.uid,
        "today": fields.Date.to_string(aujourd_hui),
        "activities": [{"id": a.id, "name": a.name, "icon": a.icon or ""} for a in activites],
        "week": jours,
        "reminder": {"enabled": reglages.reminder_enabled, "time": _heure(reglages.reminder_time)},
    }


def _saisir(level=None, activity_ids=None, note=None, client_uuid=None, date=None):
    env = request.env
    Entry = env["health.mood.entry"]
    if str(level) not in dict(NIVEAUX):
        raise UserError(_("Choisissez un des cinq visages."))
    # Une saisie faite hors ligne garde SON jour, si elle a moins d'une semaine.
    aujourd_hui = fields.Date.context_today(Entry)
    try:
        jour = fields.Date.to_date(date) if date else aujourd_hui
    except (ValueError, TypeError):
        jour = aujourd_hui
    if not jour or jour > aujourd_hui or jour < aujourd_hui - timedelta(days=7):
        jour = aujourd_hui
    uuid = str(client_uuid or "")[:64] or False
    if uuid:
        deja = Entry.search([("create_uid", "=", env.uid), ("client_uuid", "=", uuid)], limit=1)
        if deja:
            return {"ok": True, "id": deja.id, "replayed": True, "state": _etat()}
    ids = [int(i) for i in (activity_ids or []) if str(i).isdigit()][:30]
    entree = Entry.create({
        "date": jour,
        "level": str(level),
        "activity_ids": [(6, 0, ids)],
        "note": (note or "").strip()[:4000] or False,
        "client_uuid": uuid,
    })
    return {"ok": True, "id": entree.id, "state": _etat()}


class HealthyFoxHumeur(http.Controller):

    @http.route(RACINE, type="http", auth="user", methods=["GET"], website=False, sitemap=False)
    def page(self, **kw):
        if request.env.user.share:
            return request.redirect("/my")
        if not request.env.user.has_group("bf_health.group_health_user"):
            return request.redirect("/odoo")
        accent = _accent()
        html = Markup("""<!DOCTYPE html>
<html lang="%(langue)s">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content"/>
<meta name="theme-color" content="%(fond)s"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"/>
<title>%(titre)s</title>
<link rel="manifest" href="%(racine)s/manifest.webmanifest"/>
<link rel="icon" href="%(statiques)s/icon-192.png?v=%(version)s"/>
<link rel="apple-touch-icon" href="%(statiques)s/icon-192.png?v=%(version)s"/>
<link rel="stylesheet" href="%(statiques)s/humeur.css?v=%(version)s"/>
<style>:root { --brand-primary: %(accent)s; --brand-ink: %(encre)s; }</style>
</head>
<body>
<header>
  <h1>%(titre)s</h1>
  <span id="reseau" class="reseau hidden"></span>
</header>
<main>
  <form id="saisie" class="saisie" autocomplete="off">
    <h2 id="question"></h2>
    <div id="visages" class="visages" role="radiogroup"></div>
    <p id="niveau" class="niveau" aria-live="polite"></p>
    <h3 id="titre-activites"></h3>
    <div id="activites" class="activites"></div>
    <button id="ajout-note" class="lien" type="button"></button>
    <textarea id="note" class="hidden" rows="3" maxlength="4000"></textarea>
    <div class="pied">
      <span id="etat" class="etat" aria-live="polite"></span>
      <button id="garder" class="principal" type="submit"></button>
    </div>
  </form>
  <section class="semaine">
    <h3 id="titre-semaine"></h3>
    <ul id="semaine"></ul>
  </section>
  <p id="rappel" class="discret"></p>
  <p id="prive" class="discret"></p>
  <nav class="liens">
    <a id="lien-journal" href="/odoo/action-bf_health.action_mood_settings_open"></a>
    <a id="lien-export" href="%(racine)s/export.csv"></a>
  </nav>
</main>
<div id="toast" class="toast hidden" role="status"></div>
<script type="application/json" id="i18n">%(mots)s</script>
<script src="%(statiques)s/humeur.js?v=%(version)s"></script>
</body>
</html>""") % {
            "titre": escape(_("Humeur")),
            "accent": escape(accent),
            "encre": escape(_encre_sur(accent)),
            "fond": FOND,
            "racine": RACINE,
            "statiques": STATIQUES,
            "version": _version(),
            "mots": Markup(json.dumps(_mots(), ensure_ascii=False).replace("</", "<\\/")),
            "langue": (request.env.lang or "fr_CA").replace("_", "-"),
        }
        return request.make_response(html, headers=[
            ("Content-Type", "text/html; charset=utf-8"),
            ("Cache-Control", "private, no-cache"),
        ])

    @http.route(RACINE + "/manifest.webmanifest", type="http", auth="public",
                methods=["GET"], sitemap=False, save_session=False)
    def manifest(self, **kw):
        return request.make_response(json.dumps(_manifeste(), ensure_ascii=False), headers=[
            ("Content-Type", "application/manifest+json; charset=utf-8"),
            ("Cache-Control", "public, max-age=300"),
        ])

    @http.route(RACINE + "/sw.js", type="http", auth="public", methods=["GET"],
                sitemap=False, save_session=False)
    def service_worker(self, **kw):
        # ⚠️ Sans `Service-Worker-Allowed`, l'agent servi depuis `…/sw.js` ne
        # pourrait pas revendiquer la portée de la page.
        return request.make_response(
            SERVICE_WORKER % {"statiques": STATIQUES, "version": _version(), "racine": RACINE},
            headers=[
                ("Content-Type", "application/javascript; charset=utf-8"),
                ("Service-Worker-Allowed", RACINE),
                ("Cache-Control", "no-cache"),
            ])

    @http.route(RACINE + "/api/etat", type="json", auth="user", methods=["POST"])
    def api_etat(self, **kw):
        return _garde(_etat)

    @http.route(RACINE + "/api/saisir", type="json", auth="user", methods=["POST"])
    def api_saisir(self, level=None, activity_ids=None, note=None, client_uuid=None,
                   date=None, **kw):
        return _garde(lambda: _saisir(level, activity_ids, note, client_uuid, date))

    @http.route(RACINE + "/export.csv", type="http", auth="user", methods=["GET"], sitemap=False)
    def export_csv(self, **kw):
        """Toutes les saisies de la personne, toujours libres. Sous ses droits :
        les règles globales ne rendent que les siennes."""
        Entry = request.env["health.mood.entry"]
        entrees = Entry.search([("create_uid", "=", request.env.uid)], order="date asc, id asc")
        libelles = dict(Entry._fields["level"]._description_selection(request.env))
        tampon = io.StringIO()
        ecrivain = csv.writer(tampon)
        ecrivain.writerow([_("Date"), _("Humeur (1 à 5)"), _("Humeur"), _("Activités"), _("Note")])
        for e in entrees:
            ecrivain.writerow([fields.Date.to_string(e.date), e.score, libelles.get(e.level, ""),
                               ", ".join(e.activity_ids.mapped("name")), e.note or ""])
        contenu = "﻿" + tampon.getvalue()
        nom = "humeur-%s.csv" % fields.Date.to_string(fields.Date.context_today(Entry))
        return request.make_response(contenu.encode("utf-8"), headers=[
            ("Content-Type", "text/csv; charset=utf-8"),
            ("Content-Disposition", content_disposition(nom)),
            ("Cache-Control", "private, no-store"),
        ])
