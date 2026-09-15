"""La porte de l'application : le téléphone déjà appairié présente son jeton.

C'est la porte que la maison a retenue. Elle a une propriété qu'aucune autre
n'a : **le geste porte le nom de la personne qui a tapé**, parce que le jeton
qui voyage appartient à un appareil, et l'appareil appartient à quelqu'un.

⚠️ **Son propre registre d'appareils, et c'est délibéré.** Les pastilles vivent
dans une application séparée : elle a donc son appariement, son jeton et sa
ligne à révoquer. Emprunter le jeton de Messages aurait fait d'une application
de pastilles une dépendance de la boîte de courriel, et un locataire qui ne veut
que des pastilles aurait dû installer les deux.

🔴 **Ce qu'un jeton de pastille ouvre** : les gestes que la personne pourrait
faire elle-même, et rien de plus, puisque chaque geste s'exécute avec SES
droits. Le perdre ne donne pas plus que perdre son mot de passe, et ça se
révoque d'un clic.
"""
import json
import logging
import re
import urllib.parse

from werkzeug.utils import redirect as wz_redirect

from odoo import _, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

_logger = logging.getLogger(__name__)

BASE = "/bf_nfc/mobile/v1"

# Les schémas d'application autorisés à recevoir un code d'appariement.
# 🔴 Sans cette liste, /auth/start est une redirection ouverte qui remet un code
# d'échange vivant à l'adresse que l'appelant nomme.
PARAM_SCHEMAS = "bf_nfc.schemas_appariement"
SCHEMAS_DEFAUT = "com.bluefoxconsultant.pastilles://"


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
    appareil = request.env["bf.nfc.device"]._resolve(jeton)
    return appareil if appareil and appareil.user_id else None


def _agir_en_tant_que(appareil):
    """Bascule la requête sur la personne de l'appareil, SA LANGUE comprise.

    🔴 `update_env(user=…)` change l'utilisateur mais pas la langue du contexte :
    l'application n'envoie pas d'`Accept-Language` et n'a pas de session, donc
    la requête gardait la langue par défaut, et une personne réglée en anglais
    lisait « Passage consigné. » en français, catalogues chargés ou non.
    """
    request.update_env(user=appareil.user_id.id)
    request.update_context(lang=appareil.user_id.lang or request.env.context.get("lang"))


def _json(charge, statut=200):
    return request.make_json_response(charge, status=statut)


# Odoo pose ces couleurs sur toute base neuve. Une société qui les porte encore
# n'a rien choisi : les traiter comme « la marque du locataire » peindrait
# l'application en mauve Odoo, ce que ce produit existe justement pour éviter.
COULEURS_ODOO = {"#714B67", "#875A7B", "#212529", "#017E84"}


def _marque():
    """La marque de l'instance, pour que l'application porte ses couleurs.

    ⚠️ Quinze lignes dupliquées depuis ``bf_sms_archive`` plutôt qu'une
    dépendance vers lui : les deux modules s'installent séparément, et celui
    des pastilles doit pouvoir se poser chez un locataire qui n'a ni messages
    ni courriel. C'est le même arbitrage que ``bf_sms_archive`` a fait envers
    ``bf_email_management``.

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


def _modeles_cibles():
    """La liste blanche partagée avec le site : cf. ``bf.nfc.tag._modeles_cibles``.

    🔴 Elle borne aussi la route de recherche : sans elle, le jeton d'un téléphone
    interrogerait n'importe quel modèle par ``name_search``.
    """
    return request.env["bf.nfc.tag"]._modeles_cibles()


def _date(valeur):
    return valeur.isoformat() if valeur else None


def _corps():
    try:
        charge = request.get_json_data()
    except ValueError:
        return {}
    return charge if isinstance(charge, dict) else {}


class MobileNfc(http.Controller):

    # ── Découverte ────────────────────────────────────────────────────
    @http.route(f"{BASE}/ping", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def ping(self, **kw):
        """Sonde de capacité : l'app n'offre la lecture que si le module répond."""
        module = request.env["ir.module.module"].sudo().search(
            [("name", "=", "bf_nfc")], limit=1)
        return _json({
            "ok": True,
            "module": "bf_nfc",
            # api 1 : lire une pastille et en graver une. Ce qui viendra
            # ensuite (la provision d'une puce signée) montera ce numéro, et
            # une app plus ancienne ignorera simplement ce qu'elle ne connaît pas.
            # api 2 : une question peut porter un formulaire (relevé, identité
            # d'une personne sans compte), rempli dans ``reponses``.
            "api": 2,
            "version": module.installed_version or "",
            # La marque voyage dès le ping : l'application se peint AVANT
            # l'appariement, sinon elle affiche du bleu Symbifox le temps d'un
            # aller-retour puis se repeint sous les yeux de la personne.
            "branding": _marque(),
        })

    # ── Appariement ───────────────────────────────────────────────────
    @http.route(f"{BASE}/auth/start", type="http", auth="user", methods=["GET"],
                csrf=False)
    def auth_start(self, **kw):
        """Émet un code à usage unique et rebondit vers l'application.

        🔴 ``auth="user"`` fait tout le travail : ouverte dans le navigateur du
        téléphone, cette route profite de la session Odoo existante. Quelqu'un
        déjà connecté ne voit aucun écran de connexion.

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
        if not utilisateur.has_group("bf_nfc.group_nfc_user"):
            return rebondir(error="no_access")

        # 🔴 PKCE obligatoire. Un schéma d'application personnalisé n'est pas
        # exclusif sur Android : sans défi, qui intercepte le code l'échange.
        defi = (kw.get("code_challenge") or "").strip()
        methode = (kw.get("code_challenge_method") or "S256").upper()
        if not defi or methode != "S256":
            return rebondir(error="pkce_required")

        try:
            code = request.env["bf.nfc.device"]._issue_pending(
                utilisateur.id, name=(kw.get("device_name") or "")[:120],
                challenge=defi)
        except UserError:
            return rebondir(error="too_many_devices")
        return rebondir(code=code)

    @http.route(f"{BASE}/auth/exchange", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def auth_exchange(self, **kw):
        charge = _corps()
        appareil, jeton = request.env["bf.nfc.device"]._exchange(
            (charge.get("code") or "").strip(),
            (charge.get("code_verifier") or "").strip())
        if not appareil:
            return _json({"error": "invalid_or_expired_code"}, 401)
        _agir_en_tant_que(appareil)
        return _json({
            "token": jeton,
            "user_id": appareil.user_id.id,
            "user_name": appareil.user_id.name or "",
        })

    @http.route(f"{BASE}/logout", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def logout(self, **kw):
        appareil = _appareil_du_jeton()
        if not appareil:
            return _json({"error": "unauthorized"}, 401)
        appareil.sudo().write({"active": False})
        return _json({"ok": True})

    # ── Le tapotement ─────────────────────────────────────────────────
    @http.route(f"{BASE}/tap", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def tap(self, **kw):
        """Un tapotement lu par l'application.

        Corps JSON : ``{code, choix, texte, quand, nonce}``. Seul ``code``
        est obligatoire. ``choix`` et ``texte`` répondent à une question que le
        geste a posée ; ``quand`` et ``nonce`` viennent de la file hors ligne du
        téléphone, qui peut renvoyer le même tapotement plusieurs fois.

        🔴 ``save_session=False`` : l'application ne porte pas de témoin et n'en
        veut pas. Sans ce drapeau, chaque tapotement créerait une session
        serveur de plus, jetée aussitôt.
        """
        appareil = _appareil_du_jeton()
        if not appareil:
            return _json({"error": "unauthorized"}, 401)

        charge = _corps()
        code = (charge.get("code") or kw.get("code") or "").strip()
        if not code:
            return _json({"error": "no_code",
                          "message": _("Aucun code de pastille.")}, 400)

        tag = request.env["bf.nfc.tag"]._resoudre(code)
        if not tag:
            # 404 franc, comme au navigateur : l'application doit pouvoir dire
            # « cette pastille n'est pas chez nous » plutôt que « erreur ».
            return _json({"error": "unknown_tag",
                          "message": _("Cette pastille n'est pas enregistrée ici.")}, 404)

        if hasattr(appareil, "_touch_last_seen"):
            appareil._touch_last_seen()
        _agir_en_tant_que(appareil)
        tag_acteur = request.env["bf.nfc.tag"].sudo().browse(tag.id)
        # 🔴 Un éventuel ``params`` du corps est IGNORÉ : ce qu'une pastille fait
        # se décide à sa création, pas au tapotement (cf. ``bf.nfc.tag._params``).

        def texte_ou_rien(cle, borne):
            valeur = charge.get(cle)
            return str(valeur)[:borne] if valeur not in (None, "") else None

        resultat = tag_acteur.taper(
            "app",
            appareil=appareil.display_name,
            appareil_id=appareil.id,
            choix=texte_ou_rien("choix", 64),
            texte=texte_ou_rien("texte", 2000),
            quand=texte_ou_rien("quand", 40),
            nonce=texte_ou_rien("nonce", 64),
            reponses=charge.get("reponses") if isinstance(charge.get("reponses"), dict) else None,
        )
        statut = 200 if resultat["statut"] in ("ok", "duplicate", "choice", "info") else 409
        return _json(resultat, statut)

    # ── Lire sans jouer ───────────────────────────────────────────────
    @http.route(f"{BASE}/pastille/infos", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def infos_pastille(self, code=None, **kw):
        """Ce qu'est une pastille et ce qu'elle fait, SANS jouer son geste.

        Sert à l'écran « Lire une pastille » : on approche la puce pour savoir ce
        qu'elle est avant de décider de s'en servir, ou pour vérifier ce qu'on
        vient de graver.

        🔴 Le nom de la fiche visée n'est rendu que si la personne a le droit de
        la lire. Sinon on dit qu'une fiche existe, sans la nommer : la pastille a
        été retrouvée en sudo par son code, et sans ce contrôle son inspection
        ferait fuir le nom d'une fiche que son porteur ne peut pas ouvrir.
        """
        appareil = _appareil_du_jeton()
        if not appareil:
            return _json({"error": "unauthorized"}, 401)
        tag = request.env["bf.nfc.tag"]._resoudre(code or "")
        if not tag:
            return _json({"error": "unknown_tag",
                          "message": _("Cette pastille n'est pas enregistrée ici.")}, 404)
        _agir_en_tant_que(appareil)
        # 🔴 RE-parcourir dans le nouvel environnement, pas `tag.sudo()`. La
        # pastille a été retrouvée AVANT la bascule, donc son environnement
        # porte encore l'utilisateur public : `_cible()` y ferait son contrôle
        # d'accès en public, et masquerait le nom de TOUTES les fiches, même
        # celles que la personne a le droit de lire. Même précaution que /tap.
        tag = request.env["bf.nfc.tag"].sudo().browse(tag.id)
        cible = None
        if tag.res_model and tag.res_id:
            try:
                fiche = tag._cible()
                cible = {"modele": tag.res_model, "id": tag.res_id,
                         "nom": fiche.display_name, "lisible": True}
            except (AccessError, UserError):
                cible = {"modele": tag.res_model, "id": None,
                         "nom": None, "lisible": False}
        refus = tag._refus_eventuel("app")
        return _json({
            "code": tag.code,
            "nom": tag.name,
            "endroit": tag.place or None,
            "geste": {"code": tag.gesture_id.code, "nom": tag.gesture_id.name,
                      "ecrit": tag.gesture_id.writes},
            "cible": cible,
            "tapotements": tag.tap_count,
            "dernier": _date(tag.last_tap_date),
            "signee": tag.sdm_enabled,
            "utilisable": refus is None,
            "raison": refus,
        }, 200)

    @http.route(f"{BASE}/pastilles", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def mes_pastilles(self, limit=100, **kw):
        """Les pastilles que la personne peut voir, pour l'écran « Mes pastilles ».

        ⚠️ Lu avec ses droits, jamais en sudo : la règle d'enregistrement par
        société s'applique, et une personne ne voit que les pastilles de ses
        sociétés.
        """
        appareil = _appareil_du_jeton()
        if not appareil:
            return _json({"error": "unauthorized"}, 401)
        _agir_en_tant_que(appareil)
        try:
            borne = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            borne = 100
        tags = request.env["bf.nfc.tag"].search([], order="last_tap_date desc, name", limit=borne)
        return _json({"pastilles": [{
            "code": t.code,
            "nom": t.name,
            "endroit": t.place or None,
            "geste": t.gesture_id.name,
            "geste_code": t.gesture_id.code,
            "tapotements": t.tap_count,
            "dernier": _date(t.last_tap_date),
            "signee": t.sdm_enabled,
            # Pour regraver une pastille composée dans le site (un menu, un
            # point de tournée) : l'application écrit cette adresse telle quelle.
            "url": t.url or None,
        } for t in tags]}, 200)

    @http.route(f"{BASE}/journal", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def journal(self, limit=50, **kw):
        """Les derniers tapotements de la personne.

        🔴 Ses tapotements à elle, par la règle d'enregistrement « les miens ».
        Même un gestionnaire, qui voit tout le journal dans le site, ne reçoit ici
        que les siens : le téléphone est un outil personnel, pas une console de
        surveillance de l'équipe.
        """
        appareil = _appareil_du_jeton()
        if not appareil:
            return _json({"error": "unauthorized"}, 401)
        _agir_en_tant_que(appareil)
        try:
            borne = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            borne = 50
        lignes = request.env["bf.nfc.tap"].search(
            [("user_id", "=", request.env.uid)], order="tapped_at desc", limit=borne)
        return _json({"tapotements": [{
            "id": l.id,
            "quand": _date(l.tapped_at or l.create_date),
            "differe": l.offline,
            "pastille": l.tag_id.name if l.tag_id else l.tag_code,
            "geste": l.gesture_code,
            "statut": l.status,
            "titre": l.titre,
            "message": l.message,
            "porte": l.door,
        } for l in lignes]}, 200)

    @http.route(f"{BASE}/cibles", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def chercher_cibles(self, modele=None, q="", **kw):
        """Chercher la fiche qu'une pastille va viser, par son nom.

        Remplace la saisie d'un nom de modèle et d'un numéro d'enregistrement à la
        main, qui rendait la gravure à peu près inutilisable.

        ⚠️ Seulement sur la liste blanche de `_modeles_cibles`, et avec les droits
        de la personne : `name_search` applique ACL et règles d'enregistrement.
        """
        appareil = _appareil_du_jeton()
        if not appareil:
            return _json({"error": "unauthorized"}, 401)
        _agir_en_tant_que(appareil)
        if not modele or modele not in _modeles_cibles():
            return _json({"error": "model_not_allowed",
                          "message": _("On ne peut pas viser ce type de fiche.")}, 400)
        try:
            trouves = request.env[modele].name_search((q or "").strip(), limit=20)
        except AccessError:
            return _json({"error": "forbidden",
                          "message": _("Votre compte ne peut pas lire ces fiches.")}, 403)
        return _json({"cibles": [{"id": i, "nom": n} for i, n in trouves]}, 200)

    @http.route(f"{BASE}/liens", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def pages_de_liens(self, **kw):
        """Les pages de liens qu'on peut graver sur une carte d'affaires.

        La carte ne porte PAS une pastille : elle porte l'adresse publique de la
        page, que n'importe quel téléphone ouvre sans application ni compte. Le
        serveur ne sert qu'à la retrouver, pour qu'on n'ait pas à la taper.

        ⚠️ `bf_linkpage` n'est pas une dépendance. Chez un locataire qui ne l'a
        pas, la liste est vide et l'application n'offre que la saisie.

        ⚠️ Avec les droits de la personne. Sans le groupe des pages de liens, on
        retombe sur SES pages seulement : une page d'organisation porte une
        adresse opaque, et la lister à tout employé la rendrait devinable.
        """
        appareil = _appareil_du_jeton()
        if not appareil:
            return _json({"error": "unauthorized"}, 401)
        _agir_en_tant_que(appareil)
        if "bf.linkpage" not in request.env:
            return _json({"liens": []}, 200)
        personne = request.env.user
        Pages = request.env["bf.linkpage"]
        publiees = [("state", "=", "published")]
        try:
            pages = Pages.search(publiees, limit=200)
            pages.check_access("read")
        except AccessError:
            pages = Pages.sudo().search(publiees + [
                "|", ("user_id", "=", personne.id),
                ("partner_id", "=", personne.partner_id.id)], limit=200)
        pages = pages.filtered("is_live")

        def a_moi(page):
            return page.user_id == personne or page.partner_id == personne.partner_id

        ordonnees = sorted(pages, key=lambda page: (not a_moi(page), (page.name or "").lower()))
        return _json({"liens": [
            {"nom": page.name, "url": page.public_url, "a_moi": a_moi(page)}
            for page in ordonnees if page.public_url
        ]}, 200)

    # ── La gravure ────────────────────────────────────────────────────
    @http.route(f"{BASE}/catalogue", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def catalogue(self, **kw):
        """Ce que l'application peut graver sur une pastille vierge.

        L'application écrit les pastilles elle-même : sans ça il faut un
        programmateur tiers sur le téléphone, et personne ne le fera.
        """
        appareil = _appareil_du_jeton()
        if not appareil:
            return _json({"error": "unauthorized"}, 401)
        _agir_en_tant_que(appareil)
        # ⚠️ Pas de menu au catalogue de gravure : ses choix se composent dans le
        # site, puis la pastille se grave depuis « Mes pastilles ».
        gestes = request.env["bf.nfc.gesture"].search([("kind", "!=", "menu")])
        if not request.env.user.has_group("bf_nfc.group_nfc_manager"):
            gestes = gestes.filtered(lambda g: not g.reserve_gestion)
        peut_graver = request.env.user.has_group("bf_nfc.group_nfc_manager")
        modeles = _modeles_cibles()
        return _json({
            "base_url": request.env["bf.nfc.tag"].sudo().get_base_url(),
            "peut_graver": peut_graver,
            # Une carte d'affaires se grave avec ou sans ce module ; avec lui,
            # l'application propose la page au lieu de la faire taper.
            "pages_de_liens": "bf.linkpage" in request.env,
            "branding": _marque(),
            # Où la personne gère ses appareils. Rendue par le serveur plutôt
            # que fabriquée par l'application : c'est le module des appareils
            # qui décide de son adresse, pas nous.
            # ⚠️ Seulement quand ce module est là : sans lui, l'adresse est un 404,
            # et l'application afficherait une tuile qui ne mène nulle part.
            "url_appareils": (request.env["bf.nfc.tag"].sudo().get_base_url().rstrip("/")
                              + "/my/appareils") if "bf.device.registry" in request.env else None,
            # Ce que l'écran de gravure peut proposer comme fiche à viser, avec
            # le libellé du modèle dans la langue de la personne.
            "modeles_cibles": [
                # ⚠️ `ir.model` en sudo pour le NOM TRADUIT : `_description` ne se
                # traduit jamais, et la personne n'a pas le droit de lire `ir.model`.
                {"modele": nom, "libelle": request.env["ir.model"].sudo()._get(nom).name or nom}
                for nom in modeles
            ],
            "gestes": [{
                "code": g.code,
                "nom": g.name,
                "description": g.description or "",
                "ecrit": g.writes,
                "exige_cible": g.needs_target,
                "modele": g.target_model or None,
                "saisie": g.saisie,
                "reserve_gestion": g.reserve_gestion,
                "accepte_differe": g.accepte_differe,
            } for g in gestes],
        }, 200)

    @http.route(f"{BASE}/pastille", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def creer_pastille(self, **kw):
        """Crée la fiche, et rend l'adresse à graver sur la puce.

        ⚠️ La fiche naît AVANT la gravure, jamais l'inverse. Une puce gravée
        pour une fiche qui n'existe pas est une pastille morte qu'on ne peut
        plus identifier ; une fiche sans puce se supprime d'un clic.
        """
        appareil = _appareil_du_jeton()
        if not appareil:
            return _json({"error": "unauthorized"}, 401)
        _agir_en_tant_que(appareil)

        charge = _corps()
        geste_code = (charge.get("geste") or "").strip()
        geste = request.env["bf.nfc.gesture"].sudo().search(
            [("code", "=", geste_code), ("kind", "!=", "menu")], limit=1)
        if not geste:
            return _json({"error": "unknown_gesture",
                          "message": _("Ce geste n'existe pas ici.")}, 404)
        # 🔴 Les filtres du catalogue se rejouent ICI. Sans eux, ils n'étaient que
        # décoratifs : le catalogue cachait les gestes réservés et bornait les types
        # de fiche, mais cette route acceptait n'importe lequel des deux.
        if geste.reserve_gestion and not request.env.user.has_group("bf_nfc.group_nfc_manager"):
            return _json({"error": "forbidden",
                          "message": _("Ce geste est réservé à la gestion des pastilles.")}, 403)
        modele = (charge.get("modele") or "").strip()
        if modele and modele not in _modeles_cibles():
            return _json({"error": "model_not_allowed",
                          "message": _("On ne peut pas viser ce type de fiche.")}, 400)
        params = charge.get("params")
        if params:
            try:
                if not isinstance(json.loads(params), dict):
                    raise ValueError
            except (TypeError, ValueError):
                # Une pastille dont les paramètres ne sont pas un objet JSON est morte
                # au premier tapotement : elle lève au lieu de jouer son geste.
                return _json({"error": "bad_params",
                              "message": _("Les paramètres doivent être un objet JSON.")}, 400)

        valeurs = {
            "name": (charge.get("nom") or geste.name)[:120],
            "gesture_id": geste.id,
        }
        for champ, cle in (("res_model", "modele"), ("place", "endroit"),
                           ("params", "params")):
            if charge.get(cle):
                valeurs[champ] = charge[cle]
        if charge.get("fiche"):
            try:
                valeurs["res_id"] = int(charge["fiche"])
            except (TypeError, ValueError):
                return _json({"error": "bad_target"}, 400)

        try:
            tag = request.env["bf.nfc.tag"].create(valeurs)
        except AccessError:
            return _json({"error": "forbidden",
                          "message": _("Votre compte ne peut pas créer de "
                                       "pastille.")}, 403)
        except UserError as exc:
            return _json({"error": "refused", "message": str(exc)}, 400)

        base = request.env["bf.nfc.tag"].sudo().get_base_url().rstrip("/")
        return _json({
            "id": tag.id,
            "code": tag.code,
            "url": "%s/nfc/%s" % (base, tag.code),
            "nom": tag.name,
            "geste": geste.name,
        }, 200)
