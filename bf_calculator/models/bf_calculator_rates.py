"""Taux de change de la Banque du Canada (API Valet, taux quotidiens en CAD).

Choisie plutôt que XE (API payante) : source officielle, gratuite, sans clé,
publiée chaque jour ouvrable vers 16 h 30, heure de l'Est. Les taux de change
d'une base Odoo sont souvent vides (devises actives à 1,0, aucune ligne de
taux) : la comptabilité n'est donc pas une source fiable.

Les taux sont gardés dans un paramètre système et relus au plus toutes les six
heures : un calcul ne déclenche pas une requête sortante par frappe. Si la
Banque ne répond pas, le dernier relevé sert, et le panneau dit de quel jour il
date.
"""

import json
import logging
from datetime import timedelta
from decimal import Decimal

import requests

from odoo import api, fields, models

from ..lib import expression as calc

_logger = logging.getLogger(__name__)

VALET_URL = "https://www.bankofcanada.ca/valet/observations/group/FX_RATES_DAILY/json"
#: 🔴 Pas `recent=1` sur le groupe : sa « dernière observation » est celle des
#: séries interrompues (mesuré le 2026-09-22 : 2026-04-30, RUB et SAR seulement).
#: On lit une fenêtre de jours et on garde la valeur la plus récente de CHAQUE
#: série ; une série muette depuis plus longtemps disparaît d'elle-même.
WINDOW_DAYS = 10
CACHE_PARAM = "bf_calculator.boc_rates"
REFRESH = timedelta(hours=6)
#: Après un échec, pas de nouvelle tentative avant ce délai : sans lui, chaque
#: aperçu (toutes les 180 ms de frappe) bloquait un processus huit secondes.
BACKOFF = timedelta(minutes=15)
_last_failure = {}  # par processus : {base: datetime}
TIMEOUT = 8


class BfCalculatorRates(models.AbstractModel):
    _name = "bf.calculator.rates"
    _description = "Calculator exchange rates (Bank of Canada)"

    @api.model
    def _fetch(self):
        start = fields.Date.to_string(fields.Date.today() - timedelta(days=WINDOW_DAYS))
        response = requests.get(VALET_URL, params={"start_date": start}, timeout=TIMEOUT)
        response.raise_for_status()
        rates, dates = {}, {}
        for obs in response.json().get("observations", []):
            for key, cell in obs.items():
                if (key.startswith("FX") and key.endswith("CAD") and isinstance(cell, dict)
                        and cell.get("v")):
                    rates[key[2:-3]] = cell["v"]
                    dates[key[2:-3]] = obs["d"]
        if not rates:
            raise ValueError("aucune série FX dans la réponse")
        return {"date": max(dates.values()), "rates": rates, "dates": dates}

    @api.model
    def _get(self):
        """{"date": "AAAA-MM-JJ", "rates": {"USD": "1.4064", …}, "fetched": iso}
        ou {} si aucun relevé n'a jamais réussi."""
        Param = self.env["ir.config_parameter"].sudo()
        try:
            cached = json.loads(Param.get_param(CACHE_PARAM) or "{}")
        except ValueError:
            cached = {}
        fetched = cached.get("fetched")
        now = fields.Datetime.now()
        if fetched and now - fields.Datetime.from_string(fetched) < REFRESH:
            return cached
        failed = _last_failure.get(self.env.cr.dbname)
        if failed and now - failed < BACKOFF:
            return cached
        try:
            fresh = self._fetch()
        except Exception as exc:  # réseau, format : on garde le dernier relevé
            _last_failure[self.env.cr.dbname] = now
            _logger.warning("Banque du Canada injoignable (%s) : dernier relevé gardé", exc)
            return cached
        fresh["fetched"] = fields.Datetime.to_string(now)
        Param.set_param(CACHE_PARAM, json.dumps(fresh))
        return fresh

    @api.model
    def _currencies(self):
        # Les codes de base restent reconnus même sans relevé : « 100 USD en
        # CAD » doit alors dire qu'il manque un taux, pas calculer sans convertir.
        return {"CAD"} | set(calc.DEFAULT_CURRENCIES) | set(self._get().get("rates", {}))

    @api.model
    def _rate(self, src, dst):
        """(taux, date) pour convertir 1 ``src`` en ``dst``, via le CAD."""
        data = self._get()
        rates = {k: Decimal(v) for k, v in data.get("rates", {}).items()}
        rates["CAD"] = Decimal(1)
        if src not in rates or dst not in rates:
            return None
        dates = data.get("dates", {})
        day = min(d for d in (dates.get(src), dates.get(dst), data.get("date")) if d) \
            if (dates.get(src) or dates.get(dst) or data.get("date")) else ""
        return rates[src] / rates[dst], day
