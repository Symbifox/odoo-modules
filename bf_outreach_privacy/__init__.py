from . import models


def post_init_hook(env):
    """Inscrit à la liste d'opposition les refus antérieurs à l'installation.

    Sans cette reprise, une cible déjà marquée « ne plus contacter » serait
    proposée à la destruction (DEM-002) sans que rien ne retienne son refus :
    la prochaine liste importée la ramènerait.
    """
    env["bf.outreach.optout"]._bf_engrave_existing()
