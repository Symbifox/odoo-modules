"""Conversion des horodatages VOIP.ms en epoch ms UTC.

VOIP.ms émet ses horodatages (« YYYY-MM-DD HH:MM:SS ») dans le fuseau configuré
du compte (US/Eastern par défaut), **jamais** en UTC. On localise donc la chaîne
naïve dans ce fuseau avant de convertir en UTC — sinon l'heure affichée est
décalée du décalage UTC du compte (p. ex. 4 h en EDT).
"""
from datetime import datetime, timezone

import pytz

_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")

# Fuseau par défaut du compte VOIP.ms (réglage « US/Eastern » du portail).
DEFAULT_TZ = "America/New_York"
ICP_KEY = "bf_sms_archive.voipms_tz"


def account_tz(env):
    """Fuseau du compte VOIP.ms (ICP ``bf_sms_archive.voipms_tz``), repli US/Eastern."""
    name = DEFAULT_TZ
    if env is not None:
        name = (
            env["ir.config_parameter"].sudo().get_param(ICP_KEY, DEFAULT_TZ)
            or DEFAULT_TZ
        ).strip()
    try:
        return pytz.timezone(name)
    except Exception:
        return pytz.timezone(DEFAULT_TZ)


def voipms_date_to_ms(raw, env=None, tz=None):
    """« YYYY-MM-DD HH:MM:SS » (heure du compte VOIP.ms) → epoch ms UTC.

    ``tz`` permet de passer un fuseau déjà résolu (évite une lecture ICP par
    appel dans une boucle). Repli si la chaîne est vide/illisible : maintenant.
    """
    if raw:
        zone = tz or account_tz(env)
        for fmt in _FORMATS:
            try:
                aware = zone.localize(datetime.strptime(raw.strip(), fmt))
                return int(aware.timestamp() * 1000)
            except ValueError:
                continue
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)
