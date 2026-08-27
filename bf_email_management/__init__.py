from . import controllers
from . import models
from . import wizard


def post_init_hook(env):
    """Semer les identités d'expédition à l'installation.

    Le même semis qu'à la migration 18.0.11.0.0, pour que la fraîche
    installation parte avec les identités déjà prouvées plutôt qu'un écran
    vide. Idempotent, donc rejouer ne double rien.
    """
    env["bf.email.identity"]._sync_from_accounts()
