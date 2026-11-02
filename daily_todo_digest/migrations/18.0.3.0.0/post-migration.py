"""18.0.3.0.0 : moins de bruit, préférences par personne, gabarits par groupes.

Deux gestes, tous deux pour que le briefing de demain matin ressemble à celui
d'aujourd'hui, en plus court — jamais en plus long.

1. Les activités d'entretien d'hébergement sont repliées d'office. C'est la
   moitié du bruit mesuré sur un briefing réel : les activités du jour étaient
   des rappels `hosting.maintenance.schedule` aux titres identiques répétés.
   Semé seulement là où le modèle existe, et seulement sur un gabarit qui n'a
   encore rien choisi.

2. `include_upcoming_tasks` est éteint sur les gabarits existants. La section
   « tâches à venir » était collectée à chaque envoi et **jamais rendue** : la
   rendre maintenant ajouterait au courriel une section que personne n'a
   demandée. Celui qui la veut la cochera.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# Les modèles dont les activités sont des rappels récurrents posés par un
# planificateur, pas des gestes qu'une personne pose un par un.
MODELES_A_REPLIER = ("hosting.maintenance.schedule",)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    configs = env["daily.digest.config"].with_context(active_test=False).search([])
    if not configs:
        return

    modeles = env["ir.model"].search([("model", "in", list(MODELES_A_REPLIER))])
    for config in configs:
        vals = {}
        if modeles and not config.activity_fold_model_ids:
            vals["activity_fold_model_ids"] = [(6, 0, modeles.ids)]
        if config.include_upcoming_tasks:
            vals["include_upcoming_tasks"] = False
        if not vals:
            continue
        config.write(vals)
        gestes = []
        if "activity_fold_model_ids" in vals:
            gestes.append("%s modèle(s) replié(s)" % len(modeles))
        if "include_upcoming_tasks" in vals:
            gestes.append("tâches à venir éteintes")
        _logger.info("Briefing « %s » : %s", config.name, ", ".join(gestes))
    _logger.info("18.0.3.0.0 : %s gabarit(s) revus", len(configs))
