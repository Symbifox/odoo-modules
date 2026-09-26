"""Public controllers serving the Blue Fox OS policy plane.

GET  /api/v1/policy/me        merged policy for an authenticated *person*
POST /api/v1/policy/enroll    give the machine an identity of its own
GET  /api/v1/policy/machine   merged policy for an enrolled *machine*
GET  /bf_policy/extensions/update.xml
                              update manifest of the Symbifox browser extensions
                              this module serves (public, no secret)

Authentication on /me and /enroll: Authorization: Bearer <token> only,
validated against the org's Authentik userinfo endpoint. ONE claim (the org's
``identity_claim``, email by default) is matched against the Odoo login; no
match or several matches is a refusal. This is the path used by the Anaconda
%pre device flow. An Odoo browser session does not open /me or /enroll.

Whoever the bearer resolves to must then pass ``org.is_user_authorized``,
which refuses portal, archived and out-of-company users in every mode.

/machine is different by design: no human is present when the re-sync timer
fires, so it authenticates with the per-machine secret issued at enrolment
(``Authorization: Bearer bfos-machine <token>``) and serves the policy of the
user that machine was enrolled for. See bf.policy.machine for what that secret
can and cannot do.

The caller must pass the org's authorization policy (provision_mode) before the
payload is returned. Two secrets can appear in a response: the enrolment
token, which /enroll returns exactly once and never again, and, for orgs in
sssd login mode only, the LDAP bind password that the machine needs to write
its sssd.conf (give that directory account read-only rights).
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import urllib.error
import urllib.request

from xml.sax.saxutils import escape as xml_escape

from odoo import http
from odoo.http import request

from ..models.bf_policy import EnrolConflict

_logger = logging.getLogger(__name__)

_USERINFO_TIMEOUT = 10

# Un jeton machine voyage dans le meme en-tete Authorization qu'un porteur
# Authentik, prefixe de ce marqueur. Deux raisons : garder un seul schema
# d'authentification cote client, et pouvoir RECONNAITRE un jeton machine
# envoye par erreur a /me — sinon il partirait en validation chez Authentik,
# qui repondrait « invalide » sans dire pourquoi.
_MACHINE_PREFIX = "bfos-machine "

# Forme de l'UUID tire par la machine. Volontairement large (un uuid4 canonique
# passe, un identifiant maison aussi) mais borne : la valeur arrive d'un client
# non authentifie au moment ou on la lit, et elle finit dans un index SQL.
_MACHINE_UUID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")

_MAX_ENROL_BODY = 4096  # de quoi porter 3 champs courts, pas un televersement


def _json_response(payload: dict, status: int = 200):
    return request.make_response(
        json.dumps(payload),
        headers=[
            ("Content-Type", "application/json; charset=utf-8"),
            ("Cache-Control", "no-store, no-cache, must-revalidate"),
        ],
        status=status,
    )


def _introspect(userinfo_url: str, token: str) -> dict | None:
    """Validate the bearer token via the IdP userinfo endpoint.

    Returns the claims dict on success, None on any failure (so the caller
    answers 401 rather than leaking IdP error detail).
    """
    if not userinfo_url:
        return None
    req = urllib.request.Request(userinfo_url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=_USERINFO_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        _logger.warning("[bf_policy] userinfo validation failed: %s", exc)
        return None


def _jwt_claims(token: str) -> dict:
    """Revendications d'un jeton d'acces JWT, SANS verifier la signature.

    Acceptable ici et seulement ici : on ne l'appelle qu'apres que le point
    userinfo du fournisseur a accepte ce jeton-la, ce qui prouve qu'il l'a
    emis tel quel. On lit ce qu'il y a ecrit ; on ne l'authentifie pas.
    Rend {} si le jeton n'est pas un JWT lisible.
    """
    parts = (token or "").split(".")
    if len(parts) != 3:
        return {}
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode()).decode())
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return {}
    return claims if isinstance(claims, dict) else {}


def _issued_to(claims: dict, client_id: str) -> bool:
    """Le jeton a-t-il ete emis pour ce client (aud ou azp) ?"""
    if not client_id:
        return False
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    return client_id in auds or claims.get("azp") == client_id


def _match_user(claim_value: str):
    """Usager dont le login est exactement `claim_value`, a la casse pres.

    Rend ``(user, error)``. Pas d'ILIKE : `_` et `%` y sont des jokers, et un
    courriel qui en contient designerait quelqu'un d'autre. Archives compris
    dans la recherche, pour que le refus dise « non autorise » plutot que de
    laisser croire a un jeton inconnu.
    """
    value = (claim_value or "").strip()
    if not value:
        return None, "no usable identity claim"
    request.env.cr.execute(
        "SELECT id FROM res_users WHERE lower(login) = lower(%s) LIMIT 2",
        (value,))
    ids = [row[0] for row in request.env.cr.fetchall()]
    if len(ids) > 1:
        return None, "identity matches several users"
    if not ids:
        return None, None
    return request.env["res.users"].sudo().with_context(
        active_test=False).browse(ids[0]), None


def _request_host() -> str:
    """The host the operator typed at GRUB. Behind a reverse proxy the original
    host arrives in X-Forwarded-Host; fall back to the werkzeug host
    for non-proxied dev."""
    return (
        request.httprequest.headers.get("X-Forwarded-Host")
        or request.httprequest.host
        or ""
    )


def _client_ip() -> str:
    # proxy_mode = True → remote_addr is the ProxyFix-corrected client; the
    # client-supplied X-Forwarded-For must not be trusted for the audit log.
    return request.httprequest.remote_addr or "unknown"


def _select_org():
    """Route the request to a bf.policy.org. Returns ``(org, error_response)``;
    exactly one of the two is falsy."""
    Org = request.env["bf.policy.org"].sudo()
    host = _request_host()
    org, status = Org._select_for_host(host)
    if status == 503:
        return None, _json_response({"error": "no policy configured"}, 503)
    if status == 404:
        _logger.warning("[bf_policy] no policy for host=%r", host)
        return None, _json_response(
            {"error": "no policy configured for this domain"}, 404)
    return org, None


def _authorization() -> str:
    return request.httprequest.headers.get("Authorization", "") or ""


def _resolve_person(org):
    """Resolve the *person* behind the request, from an Authentik bearer.

    Returns ``(user, error_response)``; exactly one of the two is falsy."""
    auth = _authorization()
    if not auth.startswith("Bearer "):
        return None, _json_response({"error": "authentication required"}, 401)
    credential = auth[len("Bearer "):].strip()
    if credential.startswith(_MACHINE_PREFIX):
        # Repondre precisement plutot que de faire valider un jeton machine
        # par Authentik, qui dirait juste « invalide ».
        return None, _json_response(
            {"error": "machine token: use /api/v1/policy/machine"}, 401)
    claims = _introspect(org.authentik_userinfo_url, credential)
    if not isinstance(claims, dict):
        return None, _json_response(
            {"error": "invalid or unverifiable token"}, 401)
    if org.token_audience_check and not _issued_to(
            _jwt_claims(credential), org.oidc_client_id or ""):
        _logger.warning("[bf_policy] bearer not issued to %r ip=%s",
                        org.oidc_client_id, _client_ip())
        return None, _json_response(
            {"error": "token not issued to the install client"}, 401)
    user, problem = _match_user(str(claims.get(org.identity_claim or "email") or ""))
    if problem:
        _logger.warning("[bf_policy] identity refused (%s) ip=%s",
                        problem, _client_ip())
        return None, _json_response({"error": problem}, 403)
    if not user:
        return None, _json_response({"error": "authentication required"}, 401)
    if not org.is_user_authorized(user):
        _logger.warning("[bf_policy] user %s not authorized ip=%s",
                        user.login, _client_ip())
        return None, _json_response(
            {"error": "user not authorized to provision"}, 403)
    return user, None


class BfPolicyController(http.Controller):
    @http.route("/api/v1/policy/me", type="http", auth="public",
                csrf=False, methods=["GET"])
    def policy_me(self, **kwargs):
        org, error = _select_org()
        if error:
            return error
        user, error = _resolve_person(org)
        if error:
            return error
        _logger.info("[bf_policy] served policy for user=%s ip=%s",
                     user.login, _client_ip())
        return _json_response(org.get_policy_json(user), 200)

    # ------------------------------------------------------------------ enrol
    @http.route("/api/v1/policy/enroll", type="http", auth="public",
                csrf=False, methods=["POST"])
    def policy_enroll(self, **kwargs):
        """Donne une identite propre a la machine en cours d'installation.

        Appele depuis le %pre, juste apres le device flow, pendant que le
        porteur de l'operateur est encore en main — c'est le seul moment ou une
        personne autorisee est demontrablement devant la machine.
        """
        org, error = _select_org()
        if error:
            return error
        user, error = _resolve_person(org)
        if error:
            return error

        raw = request.httprequest.get_data(cache=False)[:_MAX_ENROL_BODY]
        try:
            payload = json.loads(raw.decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            payload = None
        if not isinstance(payload, dict):
            return _json_response({"error": "malformed JSON body"}, 400)

        machine_uuid = str(payload.get("machine_uuid") or "").strip()
        if not _MACHINE_UUID_RE.match(machine_uuid):
            return _json_response({"error": "invalid machine_uuid"}, 400)

        try:
            machine, token = request.env["bf.policy.machine"].sudo()._enrol(
                org, user,
                machine_uuid=machine_uuid,
                hostname=str(payload.get("hostname") or "")[:253],
                os_version=str(payload.get("os_version") or "")[:128],
            )
        except EnrolConflict:
            _logger.warning(
                "[bf_policy] enrol conflict for uuid=%r user=%s ip=%s",
                machine_uuid, user.login, _client_ip())
            return _json_response(
                {"error": "machine already enrolled for another user"}, 409)
        if not machine:
            # Seul cas restant : UUID d'une machine revoquee (cf. _enrol).
            _logger.warning("[bf_policy] enrol refused for uuid=%r ip=%s",
                            machine_uuid, _client_ip())
            return _json_response({"error": "machine revoked"}, 403)

        # Sequestre de la phrase de passe du disque. Optionnel, et
        # volontairement NON bloquant : un enrolement reste valide meme si le
        # depot echoue. C'est l'installateur qui tranche ensuite — il ne se sert
        # de la phrase qu'il a tiree QUE si `disk_escrowed` revient vrai, et
        # retombe sinon sur la saisie manuelle. Le pire scenario, un disque
        # ferme par une phrase que personne ne detient, est ainsi impossible.
        disk_escrowed, escrow_error = False, ""
        if org.disk_escrow:
            passphrase = payload.get("disk_passphrase")
            if isinstance(passphrase, str) and passphrase:
                disk_escrowed, escrow_error = machine._escrow_disk_passphrase(
                    passphrase)
            else:
                escrow_error = "aucune phrase fournie"
        else:
            escrow_error = "sequestre desactive pour cette organisation"

        _logger.info(
            "[bf_policy] enrolled machine=%s user=%s ip=%s escrow=%s",
            machine.hostname, user.login, _client_ip(),
            "oui" if disk_escrowed else f"non ({escrow_error})")
        return _json_response({
            "machine_id": machine.id,
            "machine_uuid": machine.machine_uuid,
            # Rendu UNE fois. Odoo n'en garde que le sha256.
            "token": token,
            "endpoint": f"https://{org._effective_domain()}/api/v1/policy/machine",
            "hostname": machine.hostname,
            "user": user.login,
            # ⚠️ Contrat avec le %pre : tant que ce drapeau n'est pas vrai, la
            # phrase generee cote machine ne doit servir a rien.
            "disk_escrowed": disk_escrowed,
            "disk_escrow_error": "" if disk_escrowed else escrow_error,
        }, 200)

    # ---------------------------------------------------------------- machine
    @http.route("/api/v1/policy/machine", type="http", auth="public",
                csrf=False, methods=["GET"])
    def policy_machine(self, **kwargs):
        """Sert la politique a une machine enrolee, sans personne devant."""
        org, error = _select_org()
        if error:
            return error

        auth = _authorization()
        token = ""
        if auth.startswith("Bearer "):
            credential = auth[len("Bearer "):].strip()
            if credential.startswith(_MACHINE_PREFIX):
                token = credential[len(_MACHINE_PREFIX):].strip()
        if not token:
            return _json_response({"error": "machine token required"}, 401)

        machine = request.env["bf.policy.machine"].sudo()._authenticate(token)
        # Jeton inconnu, revoque (archive → invisible de _authenticate), ou
        # presente sur le domaine d'un autre locataire : meme reponse, pour ne
        # rien apprendre a qui essaie.
        if not machine or machine.org_id != org:
            _logger.warning("[bf_policy] machine token refused ip=%s host=%r",
                            _client_ip(), _request_host())
            return _json_response({"error": "invalid machine token"}, 401)

        user = machine.user_id
        if not user or not org.is_user_authorized(user):
            # L'autorisation se rejoue a chaque synchronisation : sortir
            # quelqu'un du groupe autorise doit couper ses postes, pas
            # seulement l'empecher d'en installer un neuf.
            _logger.warning("[bf_policy] machine=%s : user %s no longer authorized",
                            machine.hostname, user.login if user else "?")
            return _json_response({"error": "user not authorized to provision"}, 403)

        machine._touch(_client_ip())
        _logger.info("[bf_policy] served policy to machine=%s user=%s ip=%s",
                     machine.hostname, user.login, _client_ip())
        return _json_response(org.get_policy_json(user), 200)

    # --- Extensions Symbifox hors boutique --------------------------------
    # Brave, sur un poste Blue Fox OS, interroge cette adresse pour chaque
    # extension « symbifox » imposee par la politique. On lui repond au format
    # gupdate de Chromium : pour chaque id, la version courante et l'adresse du
    # paquet CRX3 signe, servi en statique par ce module.
    #
    # Public par necessite : le navigateur n'a aucune session a presenter. Rien
    # de secret ici, les paquets sont signes et leur signature fixe leur id :
    # un paquet altere en chemin serait refuse par le navigateur, pas installe.
    @http.route("/bf_policy/extensions/update.xml", type="http", auth="public",
                methods=["GET"], csrf=False, save_session=False)
    def extensions_update(self, **kwargs):
        org, error = _select_org()
        if error:
            return error
        domain = org._effective_domain()
        if not domain:
            return request.make_response("", status=404)
        return request.make_response(
            _extensions_update_xml(domain, _hosted_extensions()),
            headers=[("Content-Type", "application/xml; charset=utf-8"),
                     ("Cache-Control", "no-store")])


_EXTENSIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                               "static", "extensions")
_EXT_ID_RE = re.compile(r"^[a-p]{32}$")
# Les attributs sont entre apostrophes : `escape` ne les echappe pas d'office.
_XML_ATTR = {"'": "&apos;", '"': "&quot;"}
_VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}$")


def _hosted_extensions() -> dict:
    """index.json des paquets servis, filtre : un id, une version, un fichier
    present. Une entree mal formee est ecartee plutot que servie : elle
    promettrait au navigateur un paquet qu'il ne trouverait pas."""
    try:
        with open(os.path.join(_EXTENSIONS_DIR, "index.json"), encoding="utf-8") as f:
            index = json.load(f)
    except (OSError, ValueError):
        return {}
    ok = {}
    for ext_id, info in (index or {}).items():
        if not (_EXT_ID_RE.match(ext_id or "") and isinstance(info, dict)
                and _VERSION_RE.match(str(info.get("version", "")))):
            continue
        if os.path.isfile(os.path.join(_EXTENSIONS_DIR, f"{ext_id}.crx")):
            ok[ext_id] = str(info["version"])
    return ok


def _extensions_update_xml(domain: str, hosted: dict) -> str:
    apps = "".join(
        f"  <app appid='{ext_id}'>"
        f"<updatecheck codebase='https://{xml_escape(domain, _XML_ATTR)}/bf_policy/static/extensions/{ext_id}.crx'"
        f" version='{version}' /></app>\n"
        for ext_id, version in sorted(hosted.items()))
    return ("<?xml version='1.0' encoding='UTF-8'?>\n"
            "<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>\n"
            f"{apps}</gupdate>\n")
