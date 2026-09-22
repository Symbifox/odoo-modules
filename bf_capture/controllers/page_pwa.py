"""La page ``/capture`` : un mémo vocal, installable à l'écran d'accueil.

Pourquoi une page ici et une application là, alors que l'arbitrage a
retenu l'application : parce que les deux gestes n'ont pas la même durée.

* Une **rencontre** dure une heure, écran éteint. Une page web n'a pas de
  service au premier plan : sur iOS elle est suspendue dès qu'on la quitte, et
  sur Android rien ne garantit que l'onglet survive au verrouillage. Elle reste
  dans l'application, qui tient un service « microphone ».
* Un **mémo** dure trente secondes, au premier plan, une main sur le téléphone.
  C'est exactement le cas où la page de numérisation ``/scan`` a gagné contre un
  paquet de plus, et la même réponse vaut ici.

La page ne sert donc QUE le mémo, et le plafond de cinq minutes est tenu des
deux côtés : par l'écran, qui arrête tout seul, et par le serveur, qui refuse.

⚠️ Routes en ``type="json"`` : la prévérification CORS qu'elles imposent empêche
de rejouer le témoin de session depuis un autre site.
"""

import base64
import binascii
import json
import logging

from markupsafe import Markup, escape

from odoo import _, http
from odoo.exceptions import UserError
from odoo.http import request

_logger = logging.getLogger(__name__)

STATIQUES = "/bf_capture/static/src/capture"

#: Accent de repli, la couleur de la marque maison.
ACCENT_DEFAUT = "#29ABE2"

#: Plafond du mémo côté page, en octets décodés. Le moteur a le sien
#: (`bf_capture.memo_max_bytes`) ; celui-ci refuse avant de décoder une charge
#: manifestement hors sujet.
TAILLE_MAX = 24 * 1024 * 1024

#: Ce que les navigateurs produisent, et l'extension qui leur correspond.
TYPES = {
    "audio/mp4": ".m4a",
    "audio/aac": ".m4a",
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/mpeg": ".mp3",
}

#: ⚠️ Les adresses d'icônes portent la version, comme la feuille de style :
#: Odoo sert ses statiques avec `max-age=604800`, et une page installée garderait
#: sinon l'ancienne icône une semaine. `_manifeste()` la pose à la requête.
MANIFEST = {
    # ``id`` fixe l'identité de l'app. Sans lui, un navigateur la dérive de
    # ``start_url`` : le jour où cette adresse bouge, le lanceur y voit une
    # autre application et installe une seconde icône à côté de la première.
    "id": "/capture",
    "name": "Mémo vocal",
    "short_name": "Mémo",
    "description": "Dicter une note, qui arrive transcrite dans le bloc-notes.",
    "start_url": "/capture",
    "scope": "/capture",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#12161a",
    "theme_color": "#12161a",
    "lang": "fr-CA",
    "icons": [
        {"src": f"{STATIQUES}/icon-192.png", "sizes": "192x192",
         "type": "image/png", "purpose": "any"},
        {"src": f"{STATIQUES}/icon-512.png", "sizes": "512x512",
         "type": "image/png", "purpose": "any"},
        {"src": f"{STATIQUES}/icon-maskable-192.png", "sizes": "192x192",
         "type": "image/png", "purpose": "maskable"},
        {"src": f"{STATIQUES}/icon-maskable-512.png", "sizes": "512x512",
         "type": "image/png", "purpose": "maskable"},
    ],
}


def _manifeste():
    """Le manifeste, icônes marquées de la version du module."""
    version = _version()
    manifeste = dict(MANIFEST)
    manifeste["icons"] = [
        {**icone, "src": "%s?v=%s" % (icone["src"], version)}
        for icone in MANIFEST["icons"]
    ]
    return manifeste

SERVICE_WORKER = """\
/* Agent de service de la page de mémo.

   Il ne met en cache que la coquille : la page elle-même est authentifiée et
   n'a rien à faire dans un cache partagé, et un mémo ne s'envoie pas hors
   ligne (le serveur doit le transcrire et le ranger). */
const CACHE = 'bf-capture-%(version)s';
const SHELL = [
  '%(statiques)s/capture.css?v=%(version)s',
  '%(statiques)s/capture.js?v=%(version)s',
  '%(statiques)s/icon-192.png?v=%(version)s',
  '%(statiques)s/offline.html',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') { return; }
  if (SHELL.includes(url.pathname + url.search)) {
    e.respondWith(caches.match(e.request).then((r) => r || fetch(e.request)));
    return;
  }
  if (url.pathname === '/capture') {
    e.respondWith(fetch(e.request).catch(
      () => caches.match('%(statiques)s/offline.html')));
  }
});
"""


def _version():
    """La version installée du module, telle qu'elle sert de cassure de cache.

    🔴 Odoo sert ses fichiers statiques avec `max-age=604800` : une semaine.
    Sans cette marque dans l'adresse, un correctif de style ou de script
    n'atteint pas une page déjà ouverte, et l'écran continue d'afficher
    l'ancien comportement en donnant toutes les apparences du neuf. Mesuré au
    banc le 2026-09-21, après vingt minutes passées à chercher ailleurs.
    """
    module = request.env["ir.module.module"].sudo().search(
        [("name", "=", "bf_capture")], limit=1)
    return (module.installed_version or "0").replace(".", "-")


def _service_worker():
    """L'agent de service, avec un nom de cache lié à la VERSION du module.

    🔴 Un nom de cache fixe garde l'ancienne feuille de style pour toujours :
    l'agent sert la coquille depuis son cache, et un correctif posé au serveur
    n'atteint jamais une page déjà installée. Relevé au banc le 2026-09-21,
    où le bouton est resté illisible après le correctif jusqu'à ce qu'on
    comprenne d'où venait le fichier.
    """
    return SERVICE_WORKER % {"statiques": STATIQUES, "version": _version()}


def _encre_sur(couleur):
    """Noir ou blanc, celui des deux qui se lit sur cette couleur.

    🔴 L'encre ne peut pas être une constante. La page suit l'accent du
    locataire, et l'accent d'origine d'Odoo est un prune foncé (#714B67) :
    l'encre bleu nuit qui va si bien sur le bleu de la marque y tombait à
    1,6:1, soit un bouton illisible. Mesuré au banc le 2026-09-21, pas deviné.
    """
    couleur = (couleur or "").strip()
    if len(couleur) == 4:                       # #abc → #aabbcc
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
    contraste_noir = (luminance + 0.05) / 0.05
    contraste_blanc = 1.05 / (luminance + 0.05)
    return "#0b0f13" if contraste_noir >= contraste_blanc else "#ffffff"


def _mots():
    """Les phrases que le SCRIPT de la page affiche, traduites par le serveur.

    🔴 Écrites en dur dans le fichier `.js`, elles restaient en français sur une
    instance anglaise : l'en-tête se traduisait, l'état de l'enregistrement non,
    et la page se rendait à moitié dans chaque langue. Relevé en préparant les
    captures de la vitrine, pas par un essai.
    """
    return {
        "en_cours": _("Enregistrement en cours"),
        "pret": _("Prêt à envoyer"),
        "plafond_dans": _("Plafond du mémo dans"),
        "plafond_atteint": _("Enregistrement arrêté : plafond de cinq minutes atteint."),
        "https_requis": _("Cette page a besoin d'une connexion sécurisée (HTTPS) pour "
                          "ouvrir le micro."),
        "navigateur_incapable": _("Ce navigateur ne sait pas enregistrer. Essayez "
                                  "l'application, ou un navigateur récent."),
        "micro_refuse": _("Le micro n'est pas accessible. Autorisez-le pour ce site, "
                          "puis réessayez."),
        "envoi_en_cours": _("Envoi en cours…"),
        "envoi_echoue": _("L'envoi a échoué."),
        "note_avec_texte": _("Note créée :"),
        "note_sans_texte": _("Note créée, avec l'audio en pièce jointe."),
        "ouvrir_la_note": _("Ouvrir la note"),
        "session_expiree": _("Votre session a expiré. Rechargez la page."),
        "serveur_a_repondu": _("Le serveur a répondu"),
        "trop_long": _("Le serveur a mis trop de temps. Le son est encore là, réessayez."),
        "lecture_impossible": _("Lecture du son impossible."),
    }


def _accent():
    """L'accent de la marque du locataire, ou celui de la maison.

    ⚠️ Le champ vit dans un module de marque qui n'est pas une dépendance
    d'ici : le lire sans garde ferait tomber la page sur toute base qui ne le
    porte pas.
    """
    societe = request.env.company.sudo()
    accent = ""
    if "report_brand_primary" in societe._fields:
        accent = (societe.report_brand_primary or "").strip()
    if not (accent.startswith("#") and len(accent) in (4, 7)):
        accent = ACCENT_DEFAUT
    return accent


class BfCapturePage(http.Controller):

    @http.route("/capture", type="http", auth="user", methods=["GET"],
                website=False, sitemap=False)
    def page(self, **kw):
        accent = _accent()
        transcription = request.env["bf.capture"].sudo().transcription_disponible()
        avis = (
            _("Le mémo revient transcrit, dans une note.")
            if transcription else
            _("La dictée n'est pas configurée ici : le mémo arrivera en audio, "
              "sans texte.")
        )
        html = Markup("""<!DOCTYPE html>
<html lang="%(langue)s">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
<meta name="theme-color" content="#12161a"/>
<title>%(titre)s</title>
<link rel="manifest" href="/capture/manifest.webmanifest"/>
<link rel="icon" href="%(statiques)s/icon-192.png?v=%(version)s"/>
<link rel="apple-touch-icon" href="%(statiques)s/icon-192.png?v=%(version)s"/>
<link rel="stylesheet" href="%(statiques)s/capture.css?v=%(version)s"/>
<style>:root { --brand-primary: %(accent)s; --brand-ink: %(encre)s; }</style>
</head>
<body>
<main>
  <h1>%(titre)s</h1>
  <p class="sous-titre">%(avis)s</p>

  <div class="carte">
    <div id="chrono" class="chrono">00:00</div>
    <p class="etat"><span id="pastille" class="pastille hidden"></span><span id="etat"></span></p>
    <div class="boutons">
      <button id="demarrer" class="principal" type="button">%(enregistrer)s</button>
      <button id="arreter" class="principal hidden" type="button">%(arreter)s</button>
      <button id="jeter" class="hidden" type="button">%(jeter)s</button>
      <button id="envoyer" class="principal hidden" type="button">%(envoyer)s</button>
    </div>
    <label for="titre">%(titre_champ)s</label>
    <input id="titre" type="text" autocomplete="off" placeholder="%(exemple)s"/>
  </div>

  <div id="message" class="message hidden"></div>
  <script type="application/json" id="i18n">%(mots)s</script>

  <p class="note">%(pourquoi)s</p>
</main>
<script src="%(statiques)s/capture.js?v=%(version)s"></script>
</body>
</html>""" % {
            "titre": escape(_("Mémo vocal")),
            "avis": escape(avis),
            "accent": escape(accent),
            "encre": escape(_encre_sur(accent)),
            "statiques": STATIQUES,
            "version": _version(),
            "mots": json.dumps(_mots(), ensure_ascii=False),
            "langue": (request.env.lang or "fr_CA").replace("_", "-"),
            "enregistrer": escape(_("Enregistrer")),
            "arreter": escape(_("Arrêter")),
            "jeter": escape(_("Jeter")),
            "envoyer": escape(_("Envoyer")),
            "titre_champ": escape(_("Titre (facultatif)")),
            "exemple": escape(_("Rappeler l'équipe")),
            "pourquoi": escape(_(
                "Cinq minutes au plus, et au premier plan : une page web n'a pas "
                "de service en arrière-plan, et un enregistrement de rencontre "
                "s'y perdrait en chemin. Les rencontres se captent depuis "
                "l'application, qui tient le micro écran éteint.")),
        })
        return request.make_response(html, headers=[
            ("Content-Type", "text/html; charset=utf-8"),
            # Une page authentifiée n'a rien à faire dans un cache partagé.
            ("Cache-Control", "private, no-store"),
        ])

    @http.route("/capture/manifest.webmanifest", type="http", auth="public",
                methods=["GET"], sitemap=False)
    def manifest(self, **kw):
        return request.make_response(json.dumps(_manifeste()), headers=[
            ("Content-Type", "application/manifest+json; charset=utf-8"),
            # Court : c'est lui qui annonce les nouvelles adresses d'icônes.
            ("Cache-Control", "public, max-age=300"),
        ])

    @http.route("/capture/sw.js", type="http", auth="public", methods=["GET"],
                sitemap=False)
    def service_worker(self, **kw):
        # ⚠️ `Service-Worker-Allowed` : sans cet en-tête, un agent servi depuis
        # la racine ne peut pas revendiquer la portée `/capture`, et la page
        # n'est pas installable.
        return request.make_response(_service_worker(), headers=[
            ("Content-Type", "application/javascript; charset=utf-8"),
            ("Service-Worker-Allowed", "/capture"),
            ("Cache-Control", "no-cache"),
        ])

    @http.route("/capture/memo", type="json", auth="user", methods=["POST"])
    def memo(self, audio_b64=None, mimetype=None, titre=None, **kw):
        """Reçoit le mémo, rend la note créée.

        Les erreurs reviennent dans ``error`` plutôt qu'en exception : la page
        les affiche telles quelles, et une phrase du serveur vaut mieux qu'un
        « erreur inattendue ».
        """
        try:
            octets = base64.b64decode(audio_b64 or "", validate=True)
        except (binascii.Error, ValueError):
            return {"error": _("Son illisible.")}
        if not octets:
            return {"error": _("Aucun son reçu.")}
        if len(octets) > TAILLE_MAX:
            return {"error": _("Mémo trop long pour cette page.")}

        extension = TYPES.get((mimetype or "").split(";")[0].strip().lower(), ".m4a")
        try:
            resultat = request.env["bf.capture"].deposer_memo(
                octets, nom_source="memo%s" % extension, titre=titre or None)
        except UserError as exc:
            return {"error": str(exc)}
        except Exception:  # noqa: BLE001
            _logger.exception("bf_capture: échec du mémo depuis la page")
            return {"error": _("Le serveur n'a pas pu enregistrer le mémo.")}

        # `/odoo/m-<modèle>/<id>` : le schéma d'adresse du client web d'Odoo 18
        # (`router.js`, branche `part.startsWith("m-")`). Rendu seulement si la
        # note existe encore : un lien mort vaut moins que pas de lien.
        note = request.env["bf.note"].browse(resultat["note_id"])
        if note.exists():
            resultat["url"] = "/odoo/m-bf.note/%s" % note.id
        # Même forme que la route mobile : la page et l'application lisent le
        # même contrat, et un `ok` absent se remarque tout de suite.
        return {"ok": True, **resultat}
