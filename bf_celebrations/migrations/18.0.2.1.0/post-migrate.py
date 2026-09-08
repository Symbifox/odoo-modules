# -*- coding: utf-8 -*-
"""2.1.0 : le courriel de livraison porte le lien AVEC la clé du merci.

Le gabarit est `noupdate` : on remplace, dans chaque langue chargée, la seule
expression qui change, et seulement si elle est encore là. Un gabarit réécrit
à la main qui pointe ailleurs n'est pas touché.
"""

from odoo import SUPERUSER_ID, api

ANCIEN = 'object.board_url'
NOUVEAU = 'object.recipient_url'


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    gabarit = env.ref("bf_celebrations.mail_template_livraison",
                      raise_if_not_found=False)
    if not gabarit:
        return
    for lang in env["res.lang"].get_installed():
        g = gabarit.with_context(lang=lang[0])
        corps = g.body_html or ""
        if ANCIEN in corps:
            g.body_html = corps.replace(ANCIEN, NOUVEAU)
