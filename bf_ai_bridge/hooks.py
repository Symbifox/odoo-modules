def post_init_hook(env):
    """Reprend les réglages d'avant l'unification, s'il y en avait."""
    env["bf.ai.bridge"]._adopt_legacy_param()
    env["bf.ai.bridge"]._adopt_legacy_tenant()
