"""Transport API VOIP.ms — SMS/MMS bidirectionnel.

Mixin autonome (aucune dépendance à ``hosting_management``) porté par le module
``bf_sms_archive`` afin de rester publiable tel quel sur le dépôt public.

Reprend le pattern éprouvé du connecteur lecture-seule de ``hosting_management`` :
  - GET/POST ``https://voip.ms/api/v1/rest.php`` avec ``api_username`` + ``api_password``,
  - 3 essais avec pause de 2 s,
  - redaction systématique des secrets dans les messages d'erreur (l'URL d'appel
    contient le mot de passe).

GARDE-FOUS :
  - Liste blanche stricte de méthodes : lecture (``getDIDsInfo``/``getSMS``/``getMMS``)
    + messagerie (``sendSMS``/``sendMMS``) + configuration du callback (``setSMS``).
    AUCUNE méthode de commande/annulation, ni ``getSubAccounts`` (expose les mots
    de passe SIP). Toute autre méthode lève une ``ValueError``.
  - Identifiants lus dans ``ir.config_parameter`` (jamais codés en dur).
"""
import logging
import re
import time

import requests

from odoo import api, models

_logger = logging.getLogger(__name__)

VOIPMS_API_URL = "https://voip.ms/api/v1/rest.php"

# Liste blanche : lecture + messagerie + config du callback. Ne JAMAIS y ajouter
# une méthode de commande/annulation ni getSubAccounts.
VOIPMS_ALLOWED_METHODS = frozenset({
    "getDIDsInfo",
    "getSMS",
    "getMMS",
    "sendSMS",
    "sendMMS",
    "setSMS",
})

# Scrubbe tout matériel d'auth d'un message d'erreur (l'URL contient le mot de passe).
_SECRET_RE = re.compile(r"(api_password|api_username|password|pass)=[^&\s]*", re.I)

# Limite VOIP.ms par segment SMS (au-delà : non envoyé). On découpe côté client.
SMS_SEGMENT_LEN = 160


class SmsArchiveVoipms(models.AbstractModel):
    _name = "sms.archive.voipms"
    _description = "Transport API VOIP.ms (SMS/MMS)"

    # ------------------------------------------------------------------
    # Config + appel bas niveau
    # ------------------------------------------------------------------
    @api.model
    def _voipms_redact(self, text):
        return _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", text or "")

    @api.model
    def _voipms_config(self):
        ICP = self.env["ir.config_parameter"].sudo()
        return {
            "enabled": (ICP.get_param("bf_sms_archive.voipms_enabled", "0") or "").strip().lower()
            in ("1", "true", "yes", "on", "t"),
            "username": (ICP.get_param("bf_sms_archive.voipms_api_username", "") or "").strip(),
            "password": (ICP.get_param("bf_sms_archive.voipms_api_password", "") or "").strip(),
            "public_base_url": (
                ICP.get_param("bf_sms_archive.public_base_url", "") or ""
            ).rstrip("/"),
        }

    @api.model
    def _voipms_call(self, method, _http_method="GET", **params):
        """Appel REST VOIP.ms. 3 essais, secrets redactés. POST si base64 lourd."""
        if method not in VOIPMS_ALLOWED_METHODS:
            raise ValueError("Méthode VOIP.ms non autorisée : %s" % method)
        cfg = self._voipms_config()
        if not cfg["username"] or not cfg["password"]:
            raise ValueError(
                "Identifiants API VOIP.ms absents "
                "(bf_sms_archive.voipms_api_username / _api_password)."
            )
        all_params = {
            "api_username": cfg["username"],
            "api_password": cfg["password"],
            "method": method,
            **params,
        }
        # ⚠️ VoIP.ms REFUSE le User-Agent par défaut de `requests` par un 403,
        # AVANT toute vérification d'identifiants : le même 403 sort avec un
        # mot de passe bidon et avec un usager inexistant. Sans cet en-tête,
        # chaque appel échoue et l'archive s'arrête en silence — elle l'a fait
        # le 2026-07-17 et personne ne l'a vu pendant sept semaines.
        entetes = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }
        for attempt in range(3):
            try:
                if _http_method == "POST":
                    r = requests.post(VOIPMS_API_URL, data=all_params,
                                      headers=entetes, timeout=90)
                else:
                    r = requests.get(VOIPMS_API_URL, params=all_params,
                                     headers=entetes, timeout=90)
                r.raise_for_status()
                return r.json()
            except (requests.RequestException, ValueError) as e:
                if attempt == 2:
                    # str(e) peut contenir l'URL complète (donc api_password) →
                    # assainir + couper le chaînage pour ne rien re-fuiter.
                    raise RuntimeError(
                        "VOIP.ms %s échec : %s : %s"
                        % (method, type(e).__name__, self._voipms_redact(str(e)))
                    ) from None
                time.sleep(2)

    # ------------------------------------------------------------------
    # Helpers de haut niveau
    # ------------------------------------------------------------------
    @api.model
    def _voipms_fetch_dids(self):
        """Liste des DID du compte (getDIDsInfo)."""
        data = self._voipms_call("getDIDsInfo")
        status = data.get("status")
        if status == "success":
            return data.get("dids", []) or []
        if status in ("no_did", "no_dids"):
            return []
        raise RuntimeError("getDIDsInfo : statut %s" % status)

    @api.model
    def _voipms_send_sms(self, did, dst, message):
        """Envoie un segment SMS. Retourne l'id VOIP.ms. Lève sur échec."""
        data = self._voipms_call("sendSMS", did=did, dst=dst, message=message or "")
        if data.get("status") != "success":
            raise RuntimeError("sendSMS : %s" % data.get("status"))
        sms = data.get("sms")
        if isinstance(sms, list):
            return ",".join(str(x) for x in sms)
        return str(sms or "")

    @api.model
    def _voipms_send_mms(self, did, dst, message, medias):
        """Envoie un MMS (POST, base64). ``medias`` = liste de data-URI (max 3)."""
        params = {"did": did, "dst": dst, "message": message or ""}
        for i, media in enumerate((medias or [])[:3], start=1):
            params["media%d" % i] = media
        data = self._voipms_call("sendMMS", _http_method="POST", **params)
        if data.get("status") != "success":
            raise RuntimeError("sendMMS : %s" % data.get("status"))
        return str(data.get("mms") or data.get("sms") or "")

    @api.model
    def _voipms_get_sms(self, did=None, from_date=None, to_date=None, limit=None,
                        all_messages=1):
        """getSMS sur une plage. Retourne la liste brute (peut être vide)."""
        params = {"all_messages": all_messages}
        if did:
            params["did"] = did
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        if limit:
            params["limit"] = limit
        data = self._voipms_call("getSMS", **params)
        status = data.get("status")
        if status == "success":
            return data.get("sms", []) or []
        if status in ("no_sms", "no_message"):
            return []
        raise RuntimeError("getSMS : statut %s" % status)

    @api.model
    def _voipms_set_callback(self, did, url, enable=1):
        """Configure le SMS URL Callback d'un DID (setSMS) en PRÉSERVANT les réglages
        existants (renvoi courriel / transfert SMS).

        ``setSMS`` réécrit l'ensemble de la configuration SMS du DID : les paramètres
        omis retombent à leur valeur par défaut (off). On relit donc ``getDIDsInfo``
        d'abord et on réinjecte le renvoi courriel / le transfert s'ils étaient actifs,
        pour ne rien casser en ajoutant notre callback.
        """
        current = {}
        for d in self._voipms_fetch_dids():
            if str(d.get("did")) == str(did):
                current = d
                break
        params = {
            "did": did,
            "enable": 1 if enable else 0,
            "url_callback_enable": 1,
            "url_callback_retry": 1,
            "url_callback": url,
        }
        if str(current.get("sms_email_enabled")) == "1":
            params["email_enabled"] = 1
            params["email_address"] = current.get("sms_email") or ""
        if str(current.get("sms_forward_enabled")) == "1":
            params["sms_forward_enable"] = 1
            params["sms_forward"] = current.get("sms_forward") or ""
        data = self._voipms_call("setSMS", **params)
        if data.get("status") != "success":
            raise RuntimeError("setSMS : %s" % data.get("status"))
        return True
