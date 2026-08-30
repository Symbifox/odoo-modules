def post_init_hook(env):
    """Reprend le réglage de socket d'avant l'unification, s'il y en avait un."""
    env["bf.ai.bridge"]._adopt_legacy_param()
