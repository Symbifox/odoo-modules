import json
import logging
import time
import urllib.request

from odoo import api, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

# Cache process-local des serveurs ICE. Les creds Cloudflare ont un TTL court ;
# on en demande avec 1 h de validité et on rafraîchit bien avant l'échéance.
# { "servers": [...], "exp": epoch_seconds }
_ICE_CACHE = {"servers": None, "exp": 0.0}
_ICE_TTL = 3600          # on demande 1 h de validité à Cloudflare
_ICE_REFRESH_BEFORE = 600  # on rafraîchit 10 min avant l'échéance


class SoftphoneIce(models.AbstractModel):
    _name = "bf.softphone.ice"
    _description = "Fournisseur de serveurs ICE (TURN Cloudflare) pour le softphone"

    @api.private
    @api.model
    def get_ice_servers(self):
        """Serveurs ICE prêts pour RTCPeerConnection, forgés côté serveur.

        Remplace le turn.json public du PBX : les creds ne sortent qu'à un
        membre du groupe, avec un TTL court. On sert une liste
        VOLONTAIREMENT COURTE (UDP 3478 + TURNS 443/TCP) pour ne pas rallonger
        le rassemblement ICE.

        🔴 DEUX gardes, et il faut les deux. ``api.private`` ferme la porte RPC
        (une méthode sans « _ » est appelable par ``call_kw``, et forger des
        identifiants TURN coûte du quota chez le fournisseur). Le contrôle de
        groupe couvre l'appelant interne : ``get_softphone_config`` nous appelle
        en ``sudo()``, mais ``sudo()`` ne change pas ``env.user``, donc le
        contrôle porte bien sur la personne.
        """
        if not self.env.user.has_group("bf_softphone.group_softphone_user"):
            raise AccessError("Accès au téléphone non autorisé.")
        now = time.time()
        if _ICE_CACHE["servers"] and now < _ICE_CACHE["exp"] - _ICE_REFRESH_BEFORE:
            return _ICE_CACHE["servers"]

        servers = self._mint_ice_servers()
        if servers:
            _ICE_CACHE["servers"] = servers
            _ICE_CACHE["exp"] = now + _ICE_TTL
            return servers
        # Échec de génération : on rend le cache périmé s'il existe encore
        # (mieux qu'aucun relais), sinon liste vide.
        return _ICE_CACHE["servers"] or []

    @api.model
    def _mint_ice_servers(self):
        ICP = self.env["ir.config_parameter"].sudo()
        key_id = ICP.get_param("bf_softphone.turn_key_id")
        token = ICP.get_param("bf_softphone.turn_key_token")
        if not (key_id and token):
            _logger.warning(
                "bf_softphone: bf_softphone.turn_key_id / turn_key_token absents "
                "des paramètres système — pas de serveurs TURN."
            )
            return []
        url = ("https://rtc.live.cloudflare.com/v1/turn/keys/%s/"
               "credentials/generate-ice-servers" % key_id)
        req = urllib.request.Request(
            url, method="POST", data=json.dumps({"ttl": _ICE_TTL}).encode(),
        )
        req.add_header("Authorization", "Bearer %s" % token)
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "curl/8.5.0")  # l'edge CF 403 l'UA urllib par défaut
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except Exception:
            _logger.exception("bf_softphone: échec generate-ice-servers Cloudflare")
            return []
        raw = data.get("iceServers") or []
        entry = next(
            (e for e in raw if e.get("username") and e.get("credential")), None,
        )
        if not entry:
            return raw  # pas d'entrée avec creds : on rend tel quel
        urls = entry["urls"]
        urls = [urls] if isinstance(urls, str) else urls
        # Ne garder que 2 URL complémentaires : UDP 3478 (rapide) + TURNS 443/TCP
        # (traverse tout pare-feu). Pas d'entrée STUN (inutile en mode relais).
        keep = [u for u in (
            next((u for u in urls if u.startswith("turn:") and "3478" in u and "udp" in u), None),
            next((u for u in urls if u.startswith("turns:") and "443" in u), None),
        ) if u] or urls
        return [{
            "urls": keep,
            "username": entry["username"],
            "credential": entry["credential"],
        }]
