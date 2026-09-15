"""Push via UnifiedPush (ntfy) — app Android native, SANS Google/Firebase.

L'app s'enregistre auprès d'un distributeur UnifiedPush (l'app ntfy du téléphone)
et obtient une URL d'``endpoint``. Le serveur POST le message JSON à cette URL
(auth par jeton ntfy ``up*``) ; ntfy le relaie au distributeur, qui le remet à
l'app, laquelle dessine ELLE-MÊME la notification (→ deep-link + réponse rapide).

Messages :
  - {type:"sms", title, body, thread_id, message_id}  → nouvelle notification
  - {type:"clear", thread_id}                          → efface la notif d'un fil
  - {type:"clear_all"}                                 → efface toutes les notifs

Chiffrement (C-M3, audit du 2026-09-08) : quand l'appareil a remis la clé
publique et le secret ``auth`` de son abonnement (``/register_push``), le corps
part chiffré en WebPush (RFC 8291, ``aes128gcm``). Sans clés (app ≤ 2.41.0),
le JSON part en clair comme avant. Voir ``push_request``.
"""
import base64
import binascii
import ipaddress
import json
import logging
import re
import socket
from urllib.parse import urlparse

import requests

from odoo import api, models

# ⚠️ Import tolérant : `http_ece` et `cryptography` viennent avec `pywebpush`
# (dépendance déclarée du module), mais une image qui les perdrait ne doit pas
# faire tomber le registre entier pour une notification. Sans eux, le serveur
# annonce `webpush: false` à l'app et pousse en clair, comme avant.
try:
    import http_ece
    from cryptography.hazmat.primitives.asymmetric import ec
except ImportError:  # pragma: no cover
    http_ece = ec = None

_logger = logging.getLogger(__name__)

# ⚠️ Un drapeau de configuration se lit TOLÉRANT, jamais par égalité exacte.
# `fields.Boolean(config_parameter=...)` sur `res.config.settings` stocke la
# chaîne « True » / « False », alors que les `set_param` posés à la main écrivent
# « 1 » / « 0 ». Comparer à « 1 » fait donc basculer le réglage au premier
# enregistrement du formulaire — sans erreur, sans trace, sans retour en arrière.
# Payé trois fois : rapport de sauvegarde muet (2026-06-30), Gen par courriel
# hors service (2026-09-03) et le réveil du softphone, mort huit jours par le
# MÊME clic que Gen (2026-09-10).
# ⚠️ `get_param` rend `False` — pas la valeur par défaut — quand la clé est
# ABSENTE : c'est pourquoi `False` compte ici comme « rien », et non comme « non ».
def _truthy(valeur, defaut=False):
    if valeur is None or valeur is False or valeur == "":
        return defaut
    return str(valeur).strip().lower() in ("1", "true", "vrai", "yes", "oui", "on", "t", "y")



NTFY_TOKEN_PARAM = "bf_sms_archive.ntfy_publish_token"
NTFY_BASE_PARAM = "bf_sms_archive.ntfy_base_url"
POST_TIMEOUT = 8


def _ip_is_public(ip):
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def _host_is_public(host):
    """Faux quand l'hôte résout vers une adresse privée, locale ou réservée.
    Garde anti-SSRF : le serveur POSTe vers ce que l'appareil a enregistré."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not _ip_is_public(ip):
            return False
    return True


def safe_push_endpoint(url):
    """Vrai pour une URL http(s) qui résout vers une adresse publique."""
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    return _host_is_public(parsed.hostname)


def ntfy_auth_allowed(endpoint, base_url):
    """Vrai quand ``endpoint`` est sur l'hôte du serveur ntfy configuré.

    🔴 Le jeton de publication est un secret SERVEUR. Attaché à tout endpoint
    que l'appareil enregistre, il partait vers n'importe quel hôte public
    choisi par l'appelant. Il ne part que vers notre ntfy. Pure : c'est elle
    que le banc éprouve. Audit du 2026-09-08 (S-M3).
    """
    try:
        e, b = urlparse(endpoint or ""), urlparse(base_url or "")
    except ValueError:
        return False
    return bool(e.hostname and b.hostname and e.hostname.lower() == b.hostname.lower())


# ── WebPush (RFC 8291) ────────────────────────────────────────────────────────
# Ce qu'on garantissait jusqu'ici : le contenu d'un SMS, l'objet d'un courriel
# et la réponse de Gen transitaient EN CLAIR par ntfy (S-I3), et quiconque
# connaissait l'endpoint d'un appareil pouvait y poster une notification forgée,
# une réponse rapide vers un autre fil, ou une sonnerie d'appel plein écran
# (C-M3). Le chiffrement de bout en bout règle les deux d'un coup : ntfy ne voit
# plus qu'un bloc opaque, et l'app refuse ce qu'elle ne sait pas déchiffrer.

# Durée de garde chez ntfy d'un message que l'appareil n'a pas encore relevé.
TTL_DEFAUT = 86400
# Un réveil d'appel vieux de plus d'une minute ferait sonner un correspondant
# qui a déjà raccroché : on préfère qu'il se perde.
TTL_APPEL = 60

# Taille d'enregistrement (RFC 8188) : celle de `http_ece` par défaut, et celle
# de `pywebpush`. Tout message tient dans UN enregistrement ; l'en-tête fait
# 21 + 65 octets et chaque enregistrement coûte 17 octets (délimiteur et étiquette).
WEBPUSH_RS = 4096
# ⚠️ Pas `WEBPUSH_RS - 17` : c'est le MESSAGE ENTIER, en-tête compris, que le
# déchiffreur de l'app (Tink `WebPushHybridDecrypt`, celui du connecteur
# UnifiedPush 3.x) refuse au-delà de 4096 octets. Soit 4096 - 86 - 17 = 3993
# octets de clair. Au-dessus, le message partirait, ntfy le relaierait, et
# l'app le jetterait sans un mot (constat de la 2.42.0, le 2026-09-14) ; mieux
# vaut qu'il échoue ici, dans le journal du serveur.
WEBPUSH_MAX_CLAIR = WEBPUSH_RS - 86 - 17

_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


def webpush_available():
    """Vrai quand l'image sait chiffrer (``http_ece`` et ``cryptography``)."""
    return http_ece is not None and ec is not None


def _b64url_bytes(valeur):
    """Octets d'une chaîne base64url (remplissage facultatif), ou None.

    ⚠️ L'alphabet est vérifié AVANT de décoder : ``urlsafe_b64decode`` écarte
    en silence les caractères qu'il ne connaît pas, et une clé mal copiée
    passerait pour une autre clé, valide celle-là.
    """
    if not isinstance(valeur, str):
        return None
    valeur = valeur.strip()
    if not valeur or not _B64URL_RE.match(valeur):
        return None
    try:
        return base64.urlsafe_b64decode(valeur + "=" * (-len(valeur) % 4))
    except (ValueError, binascii.Error):
        return None


def _b64url(octets):
    return base64.urlsafe_b64encode(octets).decode("ascii").rstrip("=")


def parse_push_keys(p256dh, auth):
    """Clés d'abonnement remises par l'app → ``(p256dh, auth)`` normalisées.

    Rend ``(False, False)`` quand les DEUX sont absentes : une app d'avant le
    chiffrement, qu'on continue de servir en clair. Lève ``ValueError`` pour
    tout le reste de ce qui n'est pas une paire valide : une seule clé, une clé
    qui ne se décode pas, un point qui n'est pas sur la courbe P-256, un secret
    qui n'a pas 16 octets. Pure : c'est elle que le banc éprouve.

    ⚠️ Le point est vérifié sur la courbe, pas seulement sur sa longueur : un
    point invalide ne lèverait qu'à l'envoi, dans un cron, loin de l'app qui
    l'a remis et qui croirait ses notifications chiffrées.
    """
    if not p256dh and not auth:
        return False, False
    cle, secret = _b64url_bytes(p256dh), _b64url_bytes(auth)
    if cle is None or secret is None:
        raise ValueError("clé d'abonnement illisible")
    if len(cle) != 65 or cle[0] != 0x04:
        raise ValueError("p256dh doit être un point P-256 non compressé")
    if len(secret) != 16:
        raise ValueError("auth doit faire 16 octets")
    if webpush_available():
        # Lève ValueError si le point n'est pas sur la courbe. Sans la
        # bibliothèque, l'appelant ne stocke de toute façon rien.
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), cle)
    return _b64url(cle), _b64url(secret)


def webpush_encrypt(clair, p256dh, auth, private_key=None, salt=None):
    """Chiffre ``clair`` (octets) pour l'abonnement ``(p256dh, auth)``.

    RFC 8291 en ``aes128gcm`` : clé éphémère P-256 NEUVE pour chaque message,
    sel aléatoire, un seul enregistrement, et la clé publique éphémère en
    ``keyid`` de l'en-tête. C'est exactement ce que fait
    ``pywebpush.WebPusher.encode`` ; ``private_key`` et ``salt`` ne servent
    qu'au banc, pour rejouer le vecteur de l'annexe A de la RFC.
    """
    if not webpush_available():
        raise ValueError("chiffrement WebPush indisponible sur ce serveur")
    if len(clair) > WEBPUSH_MAX_CLAIR:
        # Un second enregistrement serait conforme à la RFC 8188, mais ntfy et
        # UnifiedPush plafonnent un message à 4 Ko : il n'arriverait jamais.
        raise ValueError("message trop long pour un seul enregistrement")
    cle, secret = _b64url_bytes(p256dh), _b64url_bytes(auth)
    if cle is None or secret is None:
        raise ValueError("clé d'abonnement illisible")
    return http_ece.encrypt(
        clair,
        salt=salt,
        private_key=private_key or ec.generate_private_key(ec.SECP256R1()),
        dh=cle,
        auth_secret=secret,
        rs=WEBPUSH_RS,
        version="aes128gcm",
    )


def push_request(payload, p256dh=None, auth=None, ttl=TTL_DEFAUT):
    """``(corps, en-têtes)`` d'une poussée, sans ``Authorization``.

    Avec les deux clés : corps chiffré, ``Content-Encoding: aes128gcm`` et
    ``TTL``. Sans : le JSON en clair et les en-têtes d'avant, à l'octet près,
    pour qu'une app d'avant le chiffrement ne voie aucune différence.

    Pure, sans ORM : le réveil du softphone l'appelle depuis ses fils d'envoi.
    """
    if p256dh and auth:
        clair = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return webpush_encrypt(clair, p256dh, auth), {
            "Content-Type": "application/octet-stream",
            "Content-Encoding": "aes128gcm",
            "TTL": str(int(ttl)),
        }
    return json.dumps(payload), {"Content-Type": "application/json"}


class SmsUnifiedPush(models.AbstractModel):
    _name = "sms.archive.unifiedpush"
    _description = "Envoi push UnifiedPush/ntfy (app native, sans Google)"

    @api.model
    def _devices(self, owner):
        # Même interrupteur que côté courriel — voir bf_email_management.
        if not _truthy(self.env["ir.config_parameter"].sudo().get_param(
                "bf_sms_archive.push_enabled"), defaut=True):
            return self.env["sms.archive.mobile.device"]
        return self.env["sms.archive.mobile.device"].sudo().search([
            ("user_id", "=", owner.id), ("active", "=", True),
            ("push_endpoint", "!=", False),
        ])

    @api.model
    def _auth_headers(self, endpoint):
        icp = self.env["ir.config_parameter"].sudo()
        headers = {"Content-Type": "application/json"}
        token = icp.get_param(NTFY_TOKEN_PARAM)
        if token and ntfy_auth_allowed(endpoint, icp.get_param(NTFY_BASE_PARAM)):
            headers["Authorization"] = "Bearer %s" % token
        return headers

    @api.model
    def _webpush_types(self):
        """Les types de messages que CE serveur chiffre toujours pour un
        appareil qui a remis ses clés.

        ⚠️ L'app refuse un message NON déchiffré d'un type de cette liste : un
        type n'y entre donc que s'il passe réellement par ``_send``. Point
        d'extension : ``bf_softphone`` y ajoute « call », de sorte qu'un
        softphone d'avant le chiffrement n'annonce jamais un réveil qu'il
        pousserait en clair (et que l'app jetterait).
        """
        # « genfox » : bf_claude_chat pousse la réponse de Gen par ``_send``,
        # et par nul autre chemin.
        return ["sms", "clear", "clear_all", "genfox"]

    @api.model
    def _post(self, endpoint, payload, p256dh=None, auth=None, ttl=TTL_DEFAUT):
        corps, entetes = push_request(payload, p256dh, auth, ttl=ttl)
        # Le jeton ntfy suit la même règle qu'avant, chiffré ou non (S-M3).
        jeton = self._auth_headers(endpoint).get("Authorization")
        if jeton:
            entetes["Authorization"] = jeton
        return requests.post(
            endpoint, data=corps, headers=entetes,
            timeout=POST_TIMEOUT,
            # L'endpoint a été vérifié public ; un 30x enverrait ce POST, jeton
            # compris, là où il n'a jamais été vérifié. Audit 2026-09-08 (S-M4).
            allow_redirects=False,
        )

    @api.model
    def _send(self, owner, payload):
        """Envoie ``payload`` aux endpoints de l'utilisateur. Défensif : ne lève
        jamais. Purge les endpoints morts (403/404/410).

        Chiffré pour chaque appareil qui porte ses deux clés, en clair pour les
        autres : deux téléphones d'une même personne peuvent tourner deux
        versions de l'app."""
        for dev in self._devices(owner):
            # Revérifié à l'envoi, pas seulement à l'inscription : un nom peut
            # être repointé vers une adresse interne après coup (S-M4).
            if not safe_push_endpoint(dev.push_endpoint):
                _logger.warning("UnifiedPush : endpoint non public sur l'appareil %s, purgé.",
                                dev.id)
                dev.write({"push_endpoint": False})
                continue
            try:
                # ⚠️ Un appareil qui a remis ses clés ne reçoit JAMAIS de clair :
                # si le chiffrement échoue, `push_request` lève et le message
                # est perdu, ce que l'app aurait fait de toute façon.
                resp = self._post(dev.push_endpoint, payload,
                                  dev.push_p256dh, dev.push_auth)
                if resp.status_code in (403, 404, 410):
                    dev.write({"push_endpoint": False})
                    _logger.info("UnifiedPush endpoint mort (device %s, HTTP %s) purgé.",
                                 dev.id, resp.status_code)
                elif resp.status_code >= 400:
                    _logger.warning("UnifiedPush HTTP %s : %s",
                                    resp.status_code, resp.text[:150])
            except Exception:
                _logger.warning("UnifiedPush : erreur d'envoi.", exc_info=True)

    @api.model
    def _notify_new_message(self, msg):
        thread = msg.thread_id
        payload = {
            "type": "sms",
            "title": thread.contact_name or thread.phone_normalized or "Nouveau SMS",
            "body": (msg.body or "")[:180] or "📎 Pièce jointe",
            "thread_id": thread.id,
            "message_id": msg.id,
        }
        for user in msg._notify_users():
            if self._devices(user):
                self._send(user, dict(payload))

    @api.model
    def _notify_clear(self, owner, thread_id):
        """Efface la notification d'un fil sur les appareils (lu ailleurs)."""
        if not owner:
            return
        self._send(owner, {"type": "clear", "thread_id": int(thread_id)})

    @api.model
    def _notify_clear_all(self, owner):
        if not owner:
            return
        self._send(owner, {"type": "clear_all"})
