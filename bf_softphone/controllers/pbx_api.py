"""Réveil par push — le PBX prévient Odoo, Odoo réveille le combiné.

Le problème que ça règle : l'app mobile est un vrai poste SIP, mais son
enregistrement meurt avec son processus. App balayée, ou téléphone redémarré,
et ``Dial(PJSIP/<poste>)`` ne trouve plus aucun contact pour elle — l'appel
n'atteint jamais le combiné, sans le moindre message d'erreur.

Le chemin, dans l'ordre :

1. le plan de numérotation (contexte ``[wake-sip]``) appelle cette route AVANT
   de composer, sur une jambe ``Local/`` parallèle — le poste du navigateur et
   le cellulaire, eux, sonnent tout de suite et ne sont pas retardés ;
2. ici, on pousse ``{"type": "call"}`` aux appareils de l'utilisateur du poste ;
3. l'app se réveille, s'enregistre, et un NOUVEAU contact apparaît sur l'AOR ;
4. le plan de numérotation le voit, et compose CE contact-là.

⚠️ Cette route est sur le chemin critique d'un appel entrant : le correspondant
attend pendant qu'elle répond. Tout y est borné (envois en parallèle, délais
courts) et rien n'y lève : une panne de push doit coûter une sonnerie manquée,
jamais un appel qui n'aboutit pas.
"""

import hmac
import logging
import time
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor

import requests

from odoo import fields, http
from odoo.http import request

from odoo.addons.bf_sms_archive.controllers.mobile_api import _json
from odoo.addons.bf_sms_archive.models.push_transport import (
    NTFY_BASE_PARAM, NTFY_TOKEN_PARAM, TTL_APPEL, ntfy_auth_allowed,
    push_request, safe_push_endpoint,
)

_logger = logging.getLogger(__name__)

BASE = "/bf_softphone/pbx/v1"

# Jeton partagé avec le PBX. Fail closed : sans jeton configuré, la route refuse
# tout — un secret vide ne doit pas devenir un laissez-passer.
TOKEN_PARAM = "bf_softphone.pbx_wake_token"

# ⚠️ Interrupteur PROPRE au réveil, et c'est délibéré.
#
# `bf_sms_archive.push_enabled` peut être à « 0 » : on coupe le push pour
# cesser d'annoncer chaque texto et chaque courriel. Le raisonnement ne
# s'étend pas au téléphone.
# Un texto qui attend le réveil ne coûte rien ; un appel qu'on ne peut pas
# décrocher est manqué pour de bon — et « faire sonner le téléphone » n'est pas
# du bruit de notification, c'est le sujet du lot.
#
# D'où deux interrupteurs plutôt qu'un. Celui-ci est allumé par défaut ; le
# poser à « 0 » suffit à rendre le réveil inerte sans rallumer les textos.
WAKE_PARAM = "bf_softphone.wake_enabled"

# 🔴 Comment on LIT ce drapeau, et pourquoi ça a coûté une ligne d'affaires.
#
# `fields.Boolean(config_parameter=...)` sur `res.config.settings` stocke la
# CHAÎNE « True » / « False », jamais « 1 ». Un lecteur qui compare à « 1 »
# bascule donc au premier enregistrement de la page des réglages — sans erreur,
# sans avertissement, sans trace.
#
# Vécu en production : une sauvegarde de cette page a créé `wake_enabled` =
# « True », `_push` est sorti à zéro sur CHAQUE appel entrant, et la ligne
# d'affaires est tombée sur son filet de sécurité pendant plus d'une semaine.
# Le plan de numérotation lisait « {"ok": true, "sent": 0} » et n'avait aucune
# raison de s'en plaindre : la route répondait parfaitement, elle ne poussait
# simplement rien.
#
# ⚠️ Un drapeau de configuration se lit donc TOLÉRANT — jamais par égalité
# exacte.
_VRAI = frozenset({"1", "true", "vrai", "yes", "oui", "on", "t", "y"})


def _truthy(valeur, defaut=False):
    """Vrai/faux d'un `ir.config_parameter`, quelle que soit son écriture.

    ⚠️ `get_param` rend `False` — et non le défaut — quand la clé est ABSENTE :
    c'est pourquoi le défaut se pose ici, et pas dans l'appel. Décocher la case
    supprimait autrefois la rangée, ce qui rendait l'interrupteur incapable de
    dire « non » ; `res_config_settings` écrit désormais « 0 » explicitement.
    """
    if valeur is None or valeur is False or valeur == "":
        return defaut
    return str(valeur).strip().lower() in _VRAI


# Un appareil qu'on n'a pas vu depuis ce délai ne sert plus qu'à faire attendre
# le correspondant. Les endpoints morts finissent purgés par le 404/410 du
# transport, mais ça n'arrive qu'à l'envoi suivant : ici on ne les attend pas.
DEVICE_STALE_DAYS = 30
MAX_DEVICES = 3

# Délais serrés, assumés : la fenêtre de sonnerie entière fait une dizaine de
# secondes. Mieux vaut un push perdu qu'un plan de numérotation qui poireaute.
PUSH_TIMEOUT = 3.0

# Étranglement par poste. Un appel légitime réveille une fois ; une boucle de
# réessai du PBX, elle, martèlerait ntfy. { extension: [horodatages] }
_HITS = {}
_HIT_MAX = 20
_HIT_WINDOW = 300.0


def _throttled(ext):
    now = time.time()
    hits = [t for t in _HITS.get(ext, []) if now - t < _HIT_WINDOW]
    hits.append(now)
    _HITS[ext] = hits
    return len(hits) > _HIT_MAX


def _authorised(token):
    expected = request.env["ir.config_parameter"].sudo().get_param(TOKEN_PARAM) or ""
    if not expected:
        _logger.warning("Réveil push : %s n'est pas configuré, appel refusé.", TOKEN_PARAM)
        return False
    return hmac.compare_digest(str(token or ""), str(expected))


# Ce qu'il faut pour pousser à un appareil, lu d'avance : les fils d'envoi n'ont
# pas le droit de toucher à l'ORM. Les clés WebPush sont vides pour une app
# d'avant le chiffrement.
Cible = namedtuple("Cible", ["endpoint", "p256dh", "auth"])


def cibles(env, user):
    """Les appareils à réveiller pour ``user``, du plus frais au moins, en
    ``Cible`` (endpoint et clés WebPush).

    ⚠️ UNE seule définition, et c'est délibéré : la sonde canari
    (`res.users.softphone_wake_selftest`) doit interroger EXACTEMENT les mêmes
    appareils que le vrai réveil. Une copie de ces dix lignes dériverait au
    premier correctif, et la sonde rendrait vert sur un chemin que la production
    n'emprunte plus.

    On lit les appareils directement plutôt que par
    ``sms.archive.unifiedpush._devices`` : ce dernier porte l'interrupteur des
    TEXTOS, qui ne gouverne pas la sonnerie (voir WAKE_PARAM).
    """
    devices = env["sms.archive.mobile.device"].sudo().search([
        ("user_id", "=", user.id), ("active", "=", True),
        ("push_endpoint", "!=", False),
    ])
    if not devices:
        # Vaut la peine d'être dit : un poste SIP sans appareil inscrit ne
        # sonnera JAMAIS app fermée, et rien d'autre ne le signale.
        _logger.warning(
            "Réveil push : %s a un poste SIP mais aucun appareil avec "
            "endpoint UnifiedPush — le combiné restera muet.", user.login)
        return []
    recents = devices.filtered(
        lambda d: d.last_seen and (
            fields.Datetime.now() - d.last_seen).days <= DEVICE_STALE_DAYS)
    # Repli : si aucun appareil n'a de last_seen récent, on essaie quand même
    # les plus frais. Un poste qui ne sonne jamais est pire qu'un push perdu.
    choisis = (recents or devices)[:MAX_DEVICES]
    return [Cible(d.push_endpoint, d.push_p256dh or None, d.push_auth or None)
            for d in choisis if d.push_endpoint]


class BfSoftphonePbxApi(http.Controller):

    @http.route(f"{BASE}/wake", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def wake(self, **kw):
        """Réveille les appareils du poste ``ext``.

        Champs (form-urlencoded, ce que ``CURL()`` d'Asterisk envoie par
        défaut) : ``token``, ``ext``, et facultativement ``from`` (l'afficheur
        réel du correspondant) et ``call_id``.
        """
        if not _authorised(kw.get("token")):
            return _json({"error": "forbidden"}, 403)

        ext = (kw.get("ext") or "").strip()
        if not ext or not ext.isalnum():
            return _json({"error": "bad_ext"}, 400)
        if _throttled(ext):
            _logger.warning("Réveil push : poste %s étranglé.", ext)
            return _json({"ok": True, "sent": 0, "throttled": True})

        user = request.env["res.users"].sudo().search(
            [("sip_extension", "=", ext), ("sip_enabled", "=", True),
             ("active", "=", True)], limit=1)
        if not user:
            # Pas une erreur : un poste sans utilisateur Odoo (le 1099 de test,
            # un poste de démo) n'a simplement personne à réveiller.
            return _json({"ok": True, "sent": 0})

        payload = self._payload(kw, ext)
        sent = self._push(user, payload)
        return _json({"ok": True, "sent": sent})

    # ── Interne ───────────────────────────────────────────────────────

    def _payload(self, kw, ext):
        numero = (kw.get("from") or "").strip()[:32]
        return {
            "type": "call",
            "ext": ext,
            "peer": numero,
            "name": self._nom(numero),
            # Sert à l'app pour ne pas empiler deux notifications sur le même
            # appel quand le PBX réveille deux fois (jambe qui rappelle).
            "call_id": (kw.get("call_id") or "")[:64] or ext,
        }

    def _nom(self, numero):
        """Le nom du correspondant, s'il est connu — sinon rien.

        Même source que le journal d'appels : le NOM vit sur le fil, pas sur
        l'appel. ⚠️ ``active_test=False`` : un fil archivé reste le porteur du
        numéro, et sans ça un correspondant connu ressort anonyme.
        """
        if not numero:
            return ""
        Thread = request.env["sms.archive.thread"].sudo()
        norm = Thread.normalize_phone(numero) or numero
        fil = Thread.with_context(active_test=False).search(
            [("phone_normalized", "=", norm)], limit=1)
        if not fil:
            return ""
        return fil.contact_name or (fil.partner_id.name if fil.partner_id else "")

    def _push(self, user, payload):
        """Pousse aux appareils de ``user``, tous en même temps.

        ⚠️ En parallèle et non en série, parce que le correspondant attend :
        quatre appareils enregistrés dont trois périmés, envoyés l'un après
        l'autre, c'est quatre délais d'attente bout à bout dans une fenêtre de
        sonnerie qui en fait dix. On paie le plus lent, pas la somme.

        ⚠️ Aucun accès ORM dans les fils : l'environnement Odoo n'est pas
        partageable entre threads. Tout ce qu'il faut (endpoints, clés WebPush,
        jeton, hôte ntfy) est lu ici, avant de partir.

        🔴 Ce transport n'avait d'abord AUCUNE des gardes des deux autres : le
        jeton de publication ntfy, secret serveur, partait vers l'hôte de
        n'importe quel endpoint inscrit, les redirections étaient suivies et
        l'endpoint n'était jamais revérifié à l'envoi. Ce sont maintenant les
        mêmes règles, tirées des mêmes fonctions de ``bf_sms_archive``.

        Chiffré en WebPush pour un appareil qui a remis ses clés, avec un TTL
        court : un réveil qui arrive après le raccroché ne sert à rien.
        """
        ICP = request.env["ir.config_parameter"].sudo()
        # ⚠️ Lecture TOLÉRANTE, pas une égalité : voir _truthy ci-dessus. Le
        # défaut est vrai — un appel manqué coûte plus cher qu'un push inutile.
        if not _truthy(ICP.get_param(WAKE_PARAM), defaut=True):
            return 0
        destinations = cibles(request.env, user)
        if not destinations:
            return 0
        jeton = ICP.get_param(NTFY_TOKEN_PARAM)
        base_ntfy = ICP.get_param(NTFY_BASE_PARAM)

        def _post(cible):
            try:
                # Revérifié à l'envoi : un nom peut avoir été repointé vers une
                # adresse interne depuis l'inscription. Pure (une
                # résolution DNS), donc permise dans le fil, et en parallèle.
                # ⚠️ Écarté sans être purgé : une résolution ratée sur le chemin
                # d'un appel ne doit pas désinscrire le téléphone. Le transport
                # des textos purge, lui, à son prochain envoi.
                if not safe_push_endpoint(cible.endpoint):
                    _logger.warning("Réveil push : endpoint non public, écarté.")
                    return False
                corps, entetes = push_request(
                    payload, cible.p256dh, cible.auth, ttl=TTL_APPEL)
                # Le jeton ne part que vers NOTRE ntfy.
                if jeton and ntfy_auth_allowed(cible.endpoint, base_ntfy):
                    entetes["Authorization"] = "Bearer %s" % jeton
                resp = requests.post(
                    cible.endpoint, data=corps, headers=entetes,
                    timeout=PUSH_TIMEOUT,
                    # Un 30x enverrait ce POST, jeton compris, là où rien n'a
                    # été vérifié. Et un 30x n'est pas une remise : `< 300`.
                    allow_redirects=False)
                return resp.status_code < 300
            except Exception:
                _logger.warning("Réveil push : envoi échoué.", exc_info=True)
                return False

        with ThreadPoolExecutor(max_workers=len(destinations)) as pool:
            return sum(1 for ok in pool.map(_post, destinations) if ok)
