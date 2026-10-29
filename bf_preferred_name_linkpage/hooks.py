def post_init_hook(env):
    """Crée la clé de la réserve des adresses retirées, une fois."""
    env["bf.linkpage.retired.slug"]._key()
