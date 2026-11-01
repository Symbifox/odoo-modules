# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""18.0.1.1.0 : les quatre types déjà posés prennent leurs nouveaux défauts.

Ils sont en `noupdate` (posés par le crochet d'installation) : sans cette
migration, seule une installation neuve les aurait. Seules les cases encore
vides sont cochées ; puis les plages À VENIR sont alignées (voir hooks.py).
"""

from odoo import SUPERUSER_ID, api

from odoo.addons.bf_calendar_block.hooks import (
    aligner_plages_a_venir, types_du_module, valeurs_par_defaut)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    types = types_du_module(env)
    for champ, valeur in valeurs_par_defaut(env).items():
        types.filtered(lambda t, c=champ: not t[c]).write({champ: valeur})
    aligner_plages_a_venir(env, types)
