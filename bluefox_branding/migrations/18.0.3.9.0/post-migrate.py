"""Rejoue le hook de marque : l'invitation au portail rejoint les gabarits surchargés.

Même patron que 18.0.1.6.0. post_init_hook lit data/mail_template_overrides.xml et écrit
chaque gabarit dans toutes les langues actives (jsonb), ce que le chargeur de données
d'Odoo ne ferait pas (gabarits d'origine en noupdate, et un seul créneau de langue).
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.bluefox_branding.hooks import post_init_hook
    post_init_hook(env)
