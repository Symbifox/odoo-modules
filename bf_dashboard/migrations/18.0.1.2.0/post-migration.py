# -*- coding: utf-8 -*-
"""Rendre le gabarit d'usager que le module s'était attribué.

`data/dashboard_data.xml`, retiré en 18.0.1.2.0, écrivait
`base.default_user.action_id` — le gabarit dont héritent les usagers créés par
les chemins qui l'utilisent. Le fichier disparaît du manifeste, mais la valeur
reste : l'enregistrement porte un xmlid de `base`, donc le ménage des xmlids
obsolètes ne le touche pas. Sans cette migration, un module qui ne revendique
plus la porte continue d'aiguiller les usagers neufs vers son écran, pendant
que l'`ir.default` posé par `bf_home` dit le contraire.

Ce qu'on ne fait PAS : toucher au `action_id` des usagers existants. Le
`post_init_hook` retiré en même temps l'écrivait sur tous les internes, et il
reste des bases où quelqu'un porte encore cette action. C'est son réglage
maintenant, pas une trace à effacer — le lui retirer d'autorité serait le même
geste que celui qu'on corrige. Même raisonnement que
`bf_home/migrations/18.0.1.1.1/post-migration.py`.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return                      # installation neuve : rien n'a été posé

    env = api.Environment(cr, SUPERUSER_ID, {})
    action = env.ref("bf_dashboard.bf_dashboard_action", raise_if_not_found=False)
    template = env.ref("base.default_user", raise_if_not_found=False)
    if not action or not template:
        return

    if template.action_id.id != action.id:
        # Personne, ou quelqu'un d'autre : on ne décide pas à sa place.
        return

    template.action_id = False
    _logger.info("bf_dashboard : gabarit d'usager rendu, il ne pointe plus sur "
                 "le tableau de bord comme action d'accueil")
