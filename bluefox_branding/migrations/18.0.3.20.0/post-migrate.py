"""Rejoue le hook de marque : l'invitation au portail reçoit son sujet en français.

Même patron que 18.0.3.9.0. Le gabarit d'origine est noupdate et ses champs
traduits sont en jsonb par langue : seul post_init_hook, qui écrit chaque
gabarit surchargé dans TOUTES les langues actives, atteint le créneau anglais
où le sujet d'Odoo (« Your account at … ») coiffait un corps français.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.bluefox_branding.hooks import post_init_hook
    post_init_hook(env)
