# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Recalculer la couverture de surveillance des fiches existantes.

🔴 **Pourquoi une migration pour un calcul.** `health_coverage`,
`last_health_status`, `last_health_check` et `is_service_up` sortent du même
calcul STOCKÉ, et sa sémantique change avec ce lot : « sans relevé » ne vaut
plus « en ligne », et « éprouvé » exige désormais une MESURE, pas seulement un
relevé. Odoo ne recalcule pas un champ stocké parce que son code a changé — il
le recalcule quand une dépendance bouge. Sans ce geste, les 137 fiches gardent
leur verdict d'avant jusqu'à ce qu'un relevé les touche : les sondées se
corrigent dans la minute, les autres JAMAIS.

⚠️ Et c'est cette même inertie qui a caché une faute de syntaxe dans la
requête du calcul pendant une montée entière, au banc : le `-u` a rendu
« 0 ERROR » en n'exécutant pas la ligne fautive. Un recalcul explicite fait
tomber la pose plutôt que le premier cron.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

CHAMPS = ["health_coverage", "last_health_status", "last_health_check",
          "is_service_up"]


def migrate(cr, version):
    if not version:
        return
    # 🔴 Les deux colonnes neuves de l'archive quotidienne. `unknown_count` à 0
    # est exact (le statut n'existait pas), mais `measured_count` à 0 ferait
    # sortir TOUTE l'archive des filtres SLA et de ses décorations, qui
    # l'exigent désormais > 0. Avant ce lot, un relevé était compté ou exclu,
    # jamais « sans mesure » : le rattrapage est donc exact.
    cr.execute("""
        UPDATE hosting_health_daily_snapshot
           SET unknown_count = COALESCE(unknown_count, 0),
               measured_count = GREATEST(
                   COALESCE(check_count, 0) - COALESCE(excluded_count, 0), 0)
         WHERE measured_count IS NULL OR unknown_count IS NULL
    """)
    _logger.info(
        "%s ligne(s) d'archive quotidienne rattrapées "
        "(measured_count, unknown_count)", cr.rowcount)
    env = api.Environment(cr, SUPERUSER_ID, {})
    services = env["hosting.service"].with_context(active_test=False).search([])
    if not services:
        return
    # `modified` sur la dépendance du calcul : c'est le chemin qu'Odoo emprunte
    # lui-même quand un relevé change, donc le même code et les mêmes gardes.
    services.invalidate_recordset(CHAMPS)
    services.modified(["health_check_ids"])
    env.flush_all()
    par_etat = {}
    for s in services:
        par_etat[s.health_coverage] = par_etat.get(s.health_coverage, 0) + 1
    # ⚠️ Un `no_data` partout passerait en silence et ne se verrait qu'au
    # tableau de bord, des jours plus tard. Si la base porte des relevés, le
    # recalcul DOIT trouver des fiches éprouvées : sinon la requête du calcul
    # ne mesure pas ce qu'elle croit, et la pose doit tomber ici.
    if (par_etat.get("probed", 0) == 0
            and env["hosting.health.check"].search_count([]) > 0):
        raise AssertionError(
            "aucune fiche « éprouvée » alors que la base porte des "
            "relevés de santé — la condition de couverture ne mesure rien"
        )
    _logger.info(
        "couverture de surveillance recalculée sur %s fiche(s) : %s",
        len(services),
        ", ".join(f"{k or 'sans valeur'}={v}" for k, v in sorted(
            par_etat.items(), key=lambda kv: (kv[0] or ""))),
    )
