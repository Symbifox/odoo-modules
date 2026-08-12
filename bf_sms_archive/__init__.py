from . import controllers
from . import models
from . import wizard


def post_init_hook(env):
    """Génère la paire de clés VAPID à l'installation/mise à jour (évite toute
    course entre deux premiers chargements de la SPA)."""
    env["sms.archive.push.subscription"]._ensure_vapid_keys()
