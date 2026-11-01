# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Les quatre types de plage : sans ordre du jour et hors du tableau de bord."""

import logging

from odoo.addons.bf_calendar_block.hooks import plages_a_venir, types_du_module

_logger = logging.getLogger(__name__)

DISPENSES = ("bf_skip_agenda", "bf_skip_dashboard")


def post_init_hook(env):
    """Coche les dispenses encore vides des quatre types, puis aligne les plages à venir.

    Une case déjà cochée ou une étiquette renommée ne change rien : seules les
    valeurs vides sont posées. Les plages à venir prennent la dispense de leur
    type, occurrence par occurrence (jamais « tous les événements », qui
    archive et recrée une série). Le passé reste tel quel.
    """
    types = types_du_module(env)
    for champ in DISPENSES:
        types.filtered(lambda t, c=champ: not t[c]).write({champ: True})
    plages = plages_a_venir(env, types)
    posees = 0
    for champ in DISPENSES:
        a_poser = plages.filtered(lambda e, c=champ: not e[c] and e.bf_block_type_id[c])
        if a_poser:
            a_poser.write({champ: True})
            posees += len(a_poser)
    _logger.info("bf_calendar_block_meeting : %d type(s), %d plage(s) à venir, %d dispense(s) posée(s)",
                 len(types), len(plages), posees)
