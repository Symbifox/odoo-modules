"""Recharger les catalogues de traduction du module, en écrasant.

Les trois pages publiques n'exportaient AUCUN terme traduisible jusqu'à 1.16.0 :
Odoo saute la traduction d'une vue dont l'arch commence par un doctype
(`tools/translate.py`, `avoid_pattern`). Le doctype sorti de l'arch, les termes
existent enfin — mais leur créneau `en_CA` contient déjà le texte source
français, et un import de `.po` sans `overwrite` ne remplace pas une valeur
présente. D'où l'écrasement explicite ici : un simple `-u` suffit désormais à
poser les traductions, sans avoir à se souvenir de `--i18n-overwrite`.

Idempotente. `apply_email_translations` est rejouée dans la foulée puisqu'elle
lit le même `.po` (gabarits `noupdate`, invisibles de l'import standard).
"""
from odoo.addons.bf_securetransfer.hooks import apply_email_translations


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    module = env["ir.module.module"].sudo().search(
        [("name", "=", "bf_securetransfer")], limit=1)
    if module:
        module._update_translations(overwrite=True)
    apply_email_translations(env)
