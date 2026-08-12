# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Connecteur API VOIP.ms — LECTURE SEULE.

Mixin partagé par les modèles téléphonie (DID, CDR, transactions). Porte la
mécanique d'appel REST VOIP.ms en réutilisant un pattern REST éprouvé
(GET/redaction/retry) :
  - GET ``https://voip.ms/api/v1/rest.php`` avec ``api_username`` + ``api_password``,
  - 3 essais avec pause de 2 s,
  - redaction systématique des secrets dans les messages d'erreur (l'URL d'appel
    contient le mot de passe).

GARDE-FOUS (cf. mémoire ``voip-ms-api-reference`` + ``feedback_no_dump_auth_files``):
  - liste blanche stricte de méthodes : AUCUNE méthode d'écriture
    (``set*``/``order*``/``cancel*``) ni ``getSubAccounts`` (expose les mots de
    passe SIP). Toute autre méthode lève une ``ValueError``.
"""
import logging
import re
import time
from datetime import datetime, timedelta

import requests

from odoo import api, models

_logger = logging.getLogger(__name__)

VOIPMS_API_URL = "https://voip.ms/api/v1/rest.php"

# Liste blanche : uniquement des méthodes GET de lecture. Ne JAMAIS y ajouter
# une méthode d'écriture sans repasser par la décision « lecture seule » (#23192).
VOIPMS_ALLOWED_METHODS = frozenset({
    "getBalance",
    "getDIDsInfo",
    "getCDR",
    "getTransactionHistory",
})

# Scrubbe tout matériel d'auth d'un message d'erreur (l'URL contient le mot de passe).
_SECRET_RE = re.compile(
    r"(api_password|api_username|password|pass)=[^&\s]*", re.I)


class VoipmsConnectorMixin(models.AbstractModel):
    _name = "voipms.connector.mixin"
    _description = "Connecteur API VOIP.ms (lecture seule)"

    # ------------------------------------------------------------------
    # Config + helpers bas niveau
    # ------------------------------------------------------------------
    @api.model
    def _voipms_redact(self, text):
        return _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", text or "")

    @api.model
    def _voipms_get_config(self):
        ICP = self.env["ir.config_parameter"].sudo()
        return {
            "enabled": (ICP.get_param("hosting.voipms_enabled", "0") or "").strip().lower()
            in ("1", "true", "yes", "on", "t"),
            "username": ICP.get_param("hosting.voipms_api_username", "") or "",
            "password": ICP.get_param("hosting.voipms_api_password", "") or "",
            "low_balance_threshold": float(
                ICP.get_param("hosting.voipms_low_balance_threshold", "10") or 0
            ),
            "backfill_days": int(
                ICP.get_param("hosting.voipms_cdr_backfill_days", "90") or 90
            ),
        }

    @api.model
    def _voipms_api_call(self, method, **params):
        """Appel REST VOIP.ms — LECTURE SEULE. 3 essais, secrets redactés."""
        if method not in VOIPMS_ALLOWED_METHODS:
            raise ValueError(
                "Méthode VOIP.ms refusée (intégration en lecture seule) : %s" % method
            )
        cfg = self._voipms_get_config()
        if not cfg["username"] or not cfg["password"]:
            raise ValueError(
                "Identifiants API VOIP.ms absents "
                "(hosting.voipms_api_username / hosting.voipms_api_password)."
            )
        all_params = {
            "api_username": cfg["username"],
            "api_password": cfg["password"],
            "method": method,
            **params,
        }
        for attempt in range(3):
            try:
                r = requests.get(VOIPMS_API_URL, params=all_params, timeout=90)
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
    # Méthodes de lecture de haut niveau
    # ------------------------------------------------------------------
    @api.model
    def _voipms_fetch_dids(self):
        """Retourne la liste des DID du compte (getDIDsInfo)."""
        data = self._voipms_api_call("getDIDsInfo")
        status = data.get("status")
        if status == "success":
            return data.get("dids", []) or []
        if status in ("no_did", "no_dids"):
            return []
        raise RuntimeError("getDIDsInfo : statut %s" % status)

    @api.model
    def _voipms_fetch_balance(self):
        """Retourne le solde courant (float) ou None."""
        data = self._voipms_api_call("getBalance")
        if data.get("status") != "success":
            raise RuntimeError("getBalance : statut %s" % data.get("status"))
        bal = data.get("balance")
        if isinstance(bal, dict):
            bal = bal.get("current_balance", bal.get("balance"))
        if bal in (None, ""):
            return None
        try:
            return float(bal)
        except (TypeError, ValueError):
            return None

    @api.model
    def _voipms_fetch_cdr(self, date_from, date_to):
        """CDR sur la plage, paginé en fenêtres de 89 j (limite 90 j VOIP.ms).

        ``timezone=0`` → les heures reviennent en UTC, stockables tel quel par
        Odoo (qui re-convertit vers America/Toronto à l'affichage). ``answered``
        /``noanswer``/``busy``/``failed`` sont obligatoires sinon ``no_callstatus``.
        """
        all_records = []
        start = datetime.strptime(date_from, "%Y-%m-%d")
        end = datetime.strptime(date_to, "%Y-%m-%d")
        window_start = start
        while window_start <= end:
            window_end = min(window_start + timedelta(days=89), end)
            data = self._voipms_api_call(
                "getCDR",
                date_from=window_start.strftime("%Y-%m-%d"),
                date_to=window_end.strftime("%Y-%m-%d"),
                timezone=0,
                answered=1, noanswer=1, busy=1, failed=1,
            )
            status = data.get("status")
            if status == "success":
                all_records.extend(data.get("cdr", []) or [])
            elif status != "no_cdr":
                _logger.warning(
                    "VOIP.ms CDR %s→%s : statut %s",
                    window_start.date(), window_end.date(), status,
                )
            window_start = window_end + timedelta(days=1)
            time.sleep(0.3)
        return all_records

    @api.model
    def _voipms_fetch_transactions(self, date_from, date_to):
        """Transactions sur la plage, paginé en fenêtres de 89 j.

        NB : le champ montant de getTransactionHistory s'appelle ``ammount``
        (typo VOIP.ms documentée), pas ``amount``.
        """
        all_records = []
        start = datetime.strptime(date_from, "%Y-%m-%d")
        end = datetime.strptime(date_to, "%Y-%m-%d")
        window_start = start
        while window_start <= end:
            window_end = min(window_start + timedelta(days=89), end)
            data = self._voipms_api_call(
                "getTransactionHistory",
                date_from=window_start.strftime("%Y-%m-%d"),
                date_to=window_end.strftime("%Y-%m-%d"),
            )
            status = data.get("status")
            if status == "success":
                all_records.extend(data.get("transactions", []) or [])
            elif status != "no_transactions":
                _logger.warning(
                    "VOIP.ms transactions %s→%s : statut %s",
                    window_start.date(), window_end.date(), status,
                )
            window_start = window_end + timedelta(days=1)
            time.sleep(0.5)
        return all_records
