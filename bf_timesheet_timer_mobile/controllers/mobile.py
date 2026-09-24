"""L'API de Symbifox Chronomètre.

⚠️ **Son propre registre d'appareils, et c'est délibéré.** Le chronomètre vit
dans une application séparée : elle a donc son appariement, son jeton et sa
ligne à révoquer. Emprunter le jeton de Messages aurait fait du chronomètre une
dépendance de la boîte de courriel.

🔴 **Toute route porteur tourne dans un point de reprise.** Un refus métier
(``UserError``) est rendu en JSON 400 plutôt que de remonter, et une exception
attrapée n'annule RIEN : Odoo valide la transaction d'une requête qui répond,
quel que soit son code. Sans le point de reprise, « enregistrer » qui arrête le
chrono puis échoue à écrire la feuille de temps laisserait un chrono arrêté que
personne ne confirme, précisément le défaut que cette route existe pour éviter.

🔴 **Le téléphone n'appelle jamais l'arrêt seul.** ``stop_timer`` n'écrit aucune
feuille de temps : un arrêt sans confirmation laisse un chrono « en attente »
qu'il faudra retrouver et confirmer ailleurs. D'où ``/chrono/apercu``, qui ne
mute rien, puis ``/chrono/enregistrer``, qui arrête ET saisit dans la même
requête. Les chronos en attente venus du navigateur sont figés à leur arrêt
depuis ``bf_timesheet_timer`` 18.0.1.12.0 ; ce module refuse de
s'installer sur une version antérieure (voir ``hooks.py``).
"""
import logging
import re
import urllib.parse

from werkzeug.utils import redirect as wz_redirect

from odoo import fields, http
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.http import request

_logger = logging.getLogger(__name__)

# ── Péremption locale : le coupe-circuit qui n'a besoin de personne ──────
#
# 🔴 Un jeton refusé rend 401, et l'application efface alors
# tout ce qu'elle garde. Mais le 401 n'arrive qu'au PROCHAIN appel : un
# téléphone en mode avion, ou une application jamais rouverte, garde ses
# données indéfiniment. C'est le seul cas que ni le 401 ni un message poussé ne
# couvrent, et c'est précisément celui d'un téléphone qui part avec la personne.
#
# Le serveur annonce donc un délai, et l'application s'efface d'elle-même au
# bout de ce délai SANS contact authentifié réussi. Le compteur ne se remet à
# zéro que sur une réponse authentifiée : `/ping` est public, le relire ne
# prouve rien et ne doit rien rallonger.
#
# ⚠️ La clé est PARTAGÉE par les surfaces mobiles de la maison et
# recopiée dans chacune plutôt que mise en commun : aucun de ces modules ne
# dépend des autres, et le coffre de tokens surtout pas. Une valeur,
# plusieurs lecteurs, zéro dépendance.
CLE_PEREMPTION = "bf_mobile.wipe_after_days"
PEREMPTION_DEFAUT = 30


def _peremption_locale(env):
    """Jours sans contact authentifié au bout desquels l'app s'efface.

    Rend 0 quand la garde est volontairement désarmée.

    🔴 Clé ABSENTE n'est PAS zéro. Un paramètre jamais posé doit rendre le
    défaut, sinon la garde serait désarmée partout où personne n'a rien
    configuré, c'est-à-dire partout. L'état qu'on obtient sans rien faire doit
    être l'état sûr.
    """
    brut = env["ir.config_parameter"].sudo().get_param(CLE_PEREMPTION)
    if brut is None or brut is False or str(brut).strip() == "":
        return PEREMPTION_DEFAUT
    try:
        jours = int(str(brut).strip())
    except (TypeError, ValueError):
        _logger.warning(
            "Péremption locale : %r n'est pas un nombre de jours, "
            "le défaut de %s s'applique.", brut, PEREMPTION_DEFAUT)
        return PEREMPTION_DEFAUT
    return max(jours, 0)


BASE = "/bf_timer/mobile/v1"
MODULE = "bf_timesheet_timer_mobile"

# Les schémas d'application autorisés à recevoir un code d'appariement.
# 🔴 Sans cette liste, /auth/start est une redirection ouverte qui remet un code
# d'échange vivant à l'adresse que l'appelant nomme.
PARAM_SCHEMAS = "bf_timesheet_timer_mobile.schemas_appariement"
SCHEMAS_DEFAUT = "com.bluefoxconsultant.chronometre://"

# Le groupe qui ouvre `bf.timer` (ACL du chronomètre). Apparier quelqu'un qui
# ne peut pas lire ses chronos lui donnerait une application qui ne répond que
# des 403.
GROUPE_ACCES = "hr_timesheet.group_hr_timesheet_user"

# Odoo pose ces couleurs sur toute base neuve. Une société qui les porte encore
# n'a rien choisi : les traiter comme « la marque du locataire » peindrait
# l'application en mauve Odoo.
COULEURS_ODOO = {"#714B67", "#875A7B", "#212529", "#017E84"}

ETATS_FERMES = ("1_done", "1_canceled")


# ── Outils ─────────────────────────────────────────────────────────────
def _json(charge, statut=200):
    return request.make_json_response(charge, status=statut)


def _corps():
    try:
        charge = request.get_json_data()
    except ValueError:
        return {}
    return charge if isinstance(charge, dict) else {}


def _redirection_permise(redirect):
    schemas = (request.env["ir.config_parameter"].sudo().get_param(PARAM_SCHEMAS)
               or SCHEMAS_DEFAUT)
    permis = tuple(s.strip() for s in schemas.split(",") if s.strip())
    return bool(redirect) and bool(permis) and redirect.startswith(permis)


def _appareil_du_jeton():
    """L'appareil apparié derrière l'en-tête Authorization, ou rien."""
    entete = request.httprequest.headers.get("Authorization", "")
    if not entete.startswith("Bearer "):
        return None
    jeton = entete[7:].strip()
    if not jeton:
        return None
    appareil = request.env["bf.timer.device"]._resolve(jeton)
    return appareil if appareil and appareil.user_id else None


def _iso(valeur):
    """Une date UTC en ISO 8601 avec « Z ».

    ⚠️ Les méthodes de ``bf.timer`` rendent ``start_time_iso`` sous la forme
    ``2026-09-14 02:09:00``, sans fuseau. Un téléphone qui la lit en heure
    locale décale chaque chrono de quatre heures ; on normalise ici plutôt que
    de toucher au module du chronomètre, dont le navigateur lit la forme nue.
    """
    if not valeur:
        return None
    if not isinstance(valeur, str):
        valeur = fields.Datetime.to_string(valeur)
    return valeur.replace(" ", "T") + "Z"


def _entier(brut):
    """Un identifiant entier positif, ou None. `True` n'est pas un 1."""
    if isinstance(brut, bool):
        return None
    try:
        valeur = int(brut)
    except (TypeError, ValueError):
        return None
    return valeur if 0 < valeur < 2 ** 31 else None


def _borne(brut, defaut, minimum, maximum):
    try:
        return max(minimum, min(int(brut), maximum))
    except (TypeError, ValueError):
        return defaut


def _marque():
    """La marque de l'instance, pour que l'application porte ses couleurs.

    ⚠️ Recopié de ``bf_nfc`` et de ``bf_sms_archive`` plutôt qu'une dépendance :
    le chronomètre se pose chez un locataire qui n'a ni l'un ni l'autre.
    Lecture défensive : ``report_brand_*`` vient de ``bluefox_branding``, absent
    de bien des instances ; à défaut les champs natifs de ``res.company`` ; à
    défaut rien, et l'application garde ses propres valeurs.
    """
    societe = request.env.company.sudo()

    def couleur(*noms):
        for nom in noms:
            if nom not in societe._fields:
                continue
            valeur = (societe[nom] or "").strip()
            if re.fullmatch(r"#[0-9A-Fa-f]{6}", valeur) \
                    and valeur.upper() not in COULEURS_ODOO:
                return valeur.upper()
        return None

    return {
        "name": societe.name or "",
        "primary": couleur("report_brand_primary", "primary_color"),
        "dark": couleur("report_brand_dark", "secondary_color"),
        "logo_url": "/web/binary/company_logo",
    }


def _message(exc):
    return exc.args[0] if exc.args else str(exc)


def _servir(corps):
    """Le cadre de toute route porteur : identité, point de reprise, erreurs.

    ``corps`` rend ``(charge, statut)``. Il s'exécute au nom de la personne à
    qui appartient l'appareil, avec sa langue et son fuseau.

    ⚠️ ``allowed_company_ids`` est RETIRÉ du contexte : sans lui,
    ``env.companies`` vaut toutes les sociétés de la personne et ``env.company``
    sa société principale. Le téléphone n'a pas de sélecteur de sociétés, et une
    seule société cacherait les tâches des autres.
    """
    appareil = _appareil_du_jeton()
    if not appareil:
        return _json({"error": "unauthorized"}, 401)
    appareil._touch_last_seen()
    usager = appareil.user_id
    contexte = {cle: val for cle, val in request.env.context.items()
                if cle != "allowed_company_ids"}
    contexte.update(lang=usager.lang or "en_US", tz=usager.tz or "UTC")
    request.update_env(user=usager.id, context=contexte)
    try:
        with request.env.cr.savepoint():
            charge, statut = corps()
    except MissingError as exc:
        return _json({"error": "not_found", "message": _message(exc)}, 404)
    except AccessError as exc:
        # ⚠️ Attrapée AVANT UserError : en 18, AccessError en hérite.
        return _json({"error": "forbidden", "message": _message(exc)}, 403)
    except (UserError, ValidationError) as exc:
        return _json({"error": "refused", "message": _message(exc)}, 400)
    return _json(charge, statut)


def _introuvable(message):
    return {"error": "not_found", "message": message}, 404


def _mon_chrono(brut):
    """Le chrono désigné, s'il appartient à la personne ; sinon un vide.

    🔴 Le filtre sur la personne est EXPLICITE, et pas laissé aux règles
    d'enregistrement : un approbateur des feuilles de temps voit les chronos de
    toute l'équipe (règle « manager full access » du chronomètre). Sans ce
    filtre, son téléphone lirait l'aperçu du chrono d'un collègue.
    """
    ident = _entier(brut)
    if not ident:
        return request.env["bf.timer"].browse()
    return request.env["bf.timer"].search([
        ("id", "=", ident),
        ("user_id", "=", request.env.uid),
    ], limit=1)


def _tache_visible(brut):
    """La tâche, lue avec les droits de la personne ; sinon un vide.

    ⚠️ ``search`` et pas ``browse().exists()`` : ``exists`` ne consulte aucune
    règle d'enregistrement, et une tâche d'un projet privé passerait le
    contrôle.
    """
    ident = _entier(brut)
    if not ident:
        return request.env["project.task"].browse()
    return request.env["project.task"].search([("id", "=", ident)], limit=1)


def _formes_taches(taches):
    uid = request.env.uid
    Timer = request.env["bf.timer"]
    epinglees = set(request.env["bf.timer.pinned.task"].search([
        ("user_id", "=", uid), ("task_id", "in", taches.ids),
    ]).mapped("task_id").ids)
    en_cours = set(request.env["bf.timer"].search([
        ("user_id", "=", uid), ("is_active", "=", True),
        ("task_id", "in", taches.ids),
    ]).mapped("task_id").ids)
    formes = []
    for t in taches:
        # ⚠️ Nom du projet comme Odoo l'affiche dans un many2one, rien d'autre :
        # une tâche assignée se lit dans un projet réservé qui, lui, ne se lit
        # pas, et toute la liste répondait 403.
        project_name, project_color = Timer._project_label(t.project_id)
        formes.append({
            "task_id": t.id,
            "task_name": t.name,
            "project_id": t.project_id.id,
            "project_name": project_name,
            "project_color": project_color,
            "stage_name": t.stage_id.name or "",
            "is_closed": t.state in ETATS_FERMES,
            "is_pinned": t.id in epinglees,
            "has_active_timer": t.id in en_cours,
            "allow_timesheets": t.allow_timesheets,
        })
    return formes


def _chrono_actif(ident):
    """La forme de `get_active_timers` pour un chrono, dates normalisées."""
    for chrono in request.env["bf.timer"].get_active_timers():
        if chrono["id"] == ident:
            chrono["start_time_iso"] = _iso(chrono.get("start_time_iso"))
            return chrono
    return None


def _url_appareils():
    """Le chemin de « Mes appareils », seulement si la page existe sur CETTE base.

    ⚠️ Lu dans ``ir.module.module`` et pas par un import : un module présent sur
    le disque mais non installé n'a aucune route, et un bouton qui mène à un 404
    est pire que pas de bouton. Chemin RELATIF : l'application le colle à
    l'adresse de l'instance qu'elle connaît déjà.
    """
    installe = request.env["ir.module.module"].sudo().search_count([
        ("name", "=", "bf_devices"), ("state", "=", "installed"),
    ])
    return "/my/appareils" if installe else None


def _totaux():
    Timer = request.env["bf.timer"]
    return {"jour": float(Timer.get_today_total() or 0.0),
            "semaine": float(Timer.get_week_total() or 0.0)}


class MobileChronometre(http.Controller):

    # ── Découverte ────────────────────────────────────────────────────
    @http.route(f"{BASE}/ping", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def ping(self, **kw):
        """Sonde de capacité : l'application ne s'apparie que si le module répond."""
        module = request.env["ir.module.module"].sudo().search(
            [("name", "=", MODULE)], limit=1)
        return _json({
            "ok": True,
            "module": MODULE,
            # Le délai de péremption locale : voir `_peremption_locale`.
            "wipe_after_days": _peremption_locale(request.env),
            "api": 1,
            "version": module.latest_version or "",
            # La marque voyage dès le ping : l'application se peint AVANT
            # l'appariement, sinon elle se repeint sous les yeux de la personne
            # au retour du navigateur.
            "branding": _marque(),
        })

    # ── Appariement ───────────────────────────────────────────────────
    @http.route(f"{BASE}/auth/start", type="http", auth="user", methods=["GET"],
                csrf=False)
    def auth_start(self, **kw):
        """Émet un code à usage unique et rebondit vers l'application.

        🔴 ``auth="user"`` fait tout le travail : ouverte dans un onglet
        personnalisé, cette route profite de la session du navigateur.
        Quelqu'un déjà connecté ne voit aucun écran de connexion.

        ⚠️ Les échecs repartent par le lien profond en ``?error=``, jamais en
        page HTML : une page d'erreur laisserait l'application attendre pour
        toujours un retour qui n'arrive pas.
        """
        redirect = kw.get("redirect") or ""
        state = kw.get("state") or ""
        if not _redirection_permise(redirect):
            return request.make_response(
                "Redirection non autorisée.", status=400,
                headers=[("Content-Type", "text/plain; charset=utf-8")])

        separateur = "&" if "?" in redirect else "?"

        def rebondir(**params):
            requete = urllib.parse.urlencode({**params, "state": state})
            return wz_redirect(f"{redirect}{separateur}{requete}", code=302)

        utilisateur = request.env.user
        if not utilisateur.has_group(GROUPE_ACCES):
            return rebondir(error="no_access")

        defi = (kw.get("code_challenge") or "").strip()
        methode = (kw.get("code_challenge_method") or "S256").upper()
        if not defi or methode != "S256":
            return rebondir(error="pkce_required")

        try:
            code = request.env["bf.timer.device"]._issue_pending(
                utilisateur.id, name=(kw.get("device_name") or "")[:120],
                challenge=defi)
        except UserError:
            return rebondir(error="too_many_devices")
        return rebondir(code=code)

    @http.route(f"{BASE}/auth/exchange", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def auth_exchange(self, **kw):
        charge = _corps()
        # ⚠️ Un code ou un vérificateur qui n'est pas du texte ({"code": 123})
        # levait sur `.strip()` et rendait un 500 : c'est un 401 comme un autre.
        code, verificateur = (
            valeur.strip() if isinstance(valeur, str) else ""
            for valeur in (charge.get("code"), charge.get("code_verifier")))
        appareil, jeton = request.env["bf.timer.device"]._exchange(code, verificateur)
        if not appareil:
            return _json({"error": "invalid_or_expired_code"}, 401)
        maj = {}
        if isinstance(charge.get("device_name"), str) and charge["device_name"].strip():
            maj["name"] = charge["device_name"].strip()[:120]
        if isinstance(charge.get("app_version"), str) and charge["app_version"].strip():
            maj["app_version"] = charge["app_version"].strip()[:40]
        if maj:
            appareil.sudo().write(maj)
        usager = appareil.user_id
        request.update_env(user=usager.id)
        return _json({
            "token": jeton,
            "user": {"name": usager.name or "", "login": usager.login or ""},
            "branding": _marque(),
        })

    @http.route(f"{BASE}/auth/logout", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def logout(self, **kw):
        appareil = _appareil_du_jeton()
        if not appareil:
            return _json({"error": "unauthorized"}, 401)
        appareil.sudo().write({"active": False})
        return _json({"ok": True})

    # ── Lire ──────────────────────────────────────────────────────────
    @http.route(f"{BASE}/etat", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def etat(self, limite=15, **kw):
        """Tout l'écran principal en une lecture.

        ``maintenant`` est l'heure du SERVEUR : l'application fait avancer ses
        compteurs à partir d'elle, pas de l'horloge du téléphone, qui peut
        dériver de plusieurs minutes.
        """
        def corps():
            Timer = request.env["bf.timer"]
            actifs = Timer.get_active_timers()
            for chrono in actifs:
                chrono["start_time_iso"] = _iso(chrono.get("start_time_iso"))
            return {
                "maintenant": _iso(fields.Datetime.now()),
                "actifs": actifs,
                "en_attente": Timer.get_pending_timers(),
                "taches": Timer.get_recent_tasks(_borne(limite, 15, 1, 50)),
                "totaux": _totaux(),
                "arrondi": Timer.get_rounding_settings(),
                "gabarits": Timer.get_description_presets(),
                "url_appareils": _url_appareils(),
            }, 200
        return _servir(corps)

    @http.route(f"{BASE}/taches", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def taches(self, q="", limite=20, **kw):
        """Chercher une tâche où démarrer un chrono.

        ⚠️ Sans cette route, une tâche neuve (aucune feuille de temps encore)
        n'est atteignable que si elle est épinglée : la liste récente du
        chronomètre se bâtit sur les feuilles de temps existantes.

        🔴 Lue avec les droits de la personne, jamais en sudo.
        """
        def corps():
            texte = (q or "").strip()
            borne = _borne(limite, 20, 1, 100)
            domaine = [("allow_timesheets", "=", True), ("project_id", "!=", False)]
            exact = _entier(texte) if texte.isdigit() and len(texte) <= 9 else None
            if exact:
                domaine += ["|", ("id", "=", exact), ("name", "ilike", texte)]
            elif texte:
                domaine += [("name", "ilike", texte)]
            trouvees = request.env["project.task"].search(
                domaine, limit=borne, order="id desc")
            if exact and exact in trouvees.ids:
                # Le numéro tapé en premier : c'est ce qu'on cherchait.
                trouvees = trouvees.browse([exact] + [i for i in trouvees.ids if i != exact])
            return {"taches": _formes_taches(trouvees)}, 200
        return _servir(corps)

    @http.route(f"{BASE}/tache/<int:task_id>", type="http", auth="public",
                methods=["GET"], csrf=False, save_session=False)
    def tache(self, task_id, **kw):
        """Une tâche, pour le lien partagé depuis le navigateur."""
        def corps():
            tache = _tache_visible(task_id)
            if not tache:
                return _introuvable("Cette tâche n'existe pas ou vous n'y avez pas accès.")
            return {"tache": _formes_taches(tache)[0]}, 200
        return _servir(corps)

    # ── Agir ──────────────────────────────────────────────────────────
    @http.route(f"{BASE}/chrono/demarrer", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def demarrer(self, **kw):
        def corps():
            tache = _tache_visible(_corps().get("task_id"))
            if not tache:
                return _introuvable("Cette tâche n'existe pas ou vous n'y avez pas accès.")
            chrono = request.env["bf.timer"].start_timer(tache.id)
            chrono["start_time_iso"] = _iso(chrono.get("start_time_iso"))
            return {"ok": True, "chrono": chrono}, 200
        return _servir(corps)

    @http.route(f"{BASE}/chrono/pause", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def pause(self, **kw):
        def corps():
            chrono = _mon_chrono(_corps().get("timer_id"))
            if not chrono:
                return _introuvable("Ce chrono n'existe pas ou n'est pas le vôtre.")
            request.env["bf.timer"].pause_timer(chrono.id)
            return {"ok": True, "chrono": _chrono_actif(chrono.id)}, 200
        return _servir(corps)

    @http.route(f"{BASE}/chrono/reprendre", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def reprendre(self, **kw):
        def corps():
            chrono = _mon_chrono(_corps().get("timer_id"))
            if not chrono:
                return _introuvable("Ce chrono n'existe pas ou n'est pas le vôtre.")
            request.env["bf.timer"].resume_timer(chrono.id)
            return {"ok": True, "chrono": _chrono_actif(chrono.id)}, 200
        return _servir(corps)

    @http.route(f"{BASE}/chrono/apercu", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def apercu(self, **kw):
        """Ce que « enregistrer » proposerait, SANS rien arrêter.

        L'écoulé vient de ``bf.timer._elapsed_seconds`` : celui d'un chrono qui
        tourne avance, celui d'un chrono en pause ou arrêté est figé (depuis
        ``bf_timesheet_timer`` 18.0.1.12.0). ``arrete_le`` dit
        depuis quand un chrono arrêté au navigateur attend sa confirmation.
        """
        def corps():
            chrono = _mon_chrono(_corps().get("timer_id"))
            if not chrono:
                return _introuvable("Ce chrono n'existe pas ou n'est pas le vôtre.")
            Timer = request.env["bf.timer"]
            if not chrono.is_active:
                etat = "en_attente"
            elif chrono.is_paused:
                etat = "pause"
            else:
                etat = "actif"
            ecoule = chrono._elapsed_seconds()
            minutes = Timer._compute_suggested_minutes(ecoule)
            return {
                "timer_id": chrono.id,
                "etat": etat,
                "task_id": chrono.task_id.id,
                "task_name": chrono.task_id.name,
                "project_id": chrono.project_id.id,
                "project_name": Timer._project_label(chrono.project_id)[0],
                "elapsed_seconds": ecoule,
                "suggested_minutes": minutes,
                "suggested_hours": round(minutes / 60.0, 4),
                "description": chrono.description or chrono.task_id.name,
                "arrondi": Timer.get_rounding_settings(),
                "arrete_le": _iso(chrono.claimed_at) if not chrono.is_active else None,
            }, 200
        return _servir(corps)

    @http.route(f"{BASE}/chrono/enregistrer", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def enregistrer(self, **kw):
        """Arrête ET saisit, dans la même requête. Voir l'en-tête du fichier.

        ``minutes`` est la durée que la personne a confirmée à l'écran, pas
        l'écoulé : l'application a pu la corriger. ``heures`` rend ce qui a
        réellement été écrit, après le palier minimum et l'arrondi.
        """
        def corps():
            charge = _corps()
            minutes = charge.get("minutes")
            if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
                return {"error": "bad_minutes",
                        "message": "La durée doit être un nombre entier de minutes, "
                                   "plus grand que zéro."}, 400
            chrono = _mon_chrono(charge.get("timer_id"))
            if not chrono:
                return _introuvable("Ce chrono n'existe pas ou n'est pas le vôtre.")
            description = charge.get("description")
            description = description.strip() if isinstance(description, str) else ""
            Timer = request.env["bf.timer"]
            # Même règle que le dialogue du navigateur et l'assistant : un
            # palier au minimum, puis deux décimales (25 min = 0,42 h).
            heures = Timer._duration_hours(minutes)
            if chrono.is_active:
                Timer.stop_timer(chrono.id)
            Timer.confirm_timesheet(chrono.id, heures, description or None)
            return {"ok": True, "minutes": minutes, "heures": heures,
                    "totaux": _totaux()}, 200
        return _servir(corps)

    @http.route(f"{BASE}/chrono/abandonner", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def abandonner(self, **kw):
        def corps():
            chrono = _mon_chrono(_corps().get("timer_id"))
            if not chrono:
                return _introuvable("Ce chrono n'existe pas ou n'est pas le vôtre.")
            request.env["bf.timer"].discard_timer(chrono.id)
            return {"ok": True}, 200
        return _servir(corps)

    @http.route(f"{BASE}/tache/epingler", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def epingler(self, **kw):
        """Épingler ou désépingler une tâche.

        La visibilité est vérifiée ici pour rendre un 404 franc plutôt qu'un
        403 ; ``pin_task`` refuse lui aussi une tâche que la personne ne peut
        pas lire (depuis ``bf_timesheet_timer`` 18.0.1.12.0), sur toutes ses
        sociétés.
        """
        def corps():
            charge = _corps()
            epingle = charge.get("epingle")
            if not isinstance(epingle, bool):
                return {"error": "bad_request",
                        "message": "« epingle » doit valoir true ou false."}, 400
            tache = _tache_visible(charge.get("task_id"))
            if not tache:
                return _introuvable("Cette tâche n'existe pas ou vous n'y avez pas accès.")
            Timer = request.env["bf.timer"]
            if epingle:
                Timer.pin_task(tache.id)
            else:
                Timer.unpin_task(tache.id)
            return {"ok": True, "is_pinned": epingle}, 200
        return _servir(corps)
