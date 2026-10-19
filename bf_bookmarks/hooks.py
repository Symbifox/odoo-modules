# -*- coding: utf-8 -*-


def post_init_hook(env):
    """Une racine d'entreprise par société dès l'installation."""
    env["bf.bookmark.folder"]._ensure_company_roots()
