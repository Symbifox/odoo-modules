"""Rattraper les lignes d'usage que la 18.0.1.0.0 n'a jamais créées.

La 18.0.1.0.0 n'écoutait que `hr.expense.write`. En Odoo 18, l'approbation
passe par la note de frais et l'état de la dépense est recalculé sans `write` :
une dépense rattachée à un avantage et approuvée sous cette version n'a donc
aucune ligne au registre. On la crée ici, comme l'approbation l'aurait fait,
à la date et au montant de la dépense. Idempotent : le garde est `ex_usage_id`.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    missing = env["hr.expense"].with_context(active_test=False).search([
        ("ex_benefit_id", "!=", False),
        ("ex_usage_id", "=", False),
        ("state", "in", ("approved", "done")),
    ])
    missing._ex_sync_usage()
    created = missing.filtered("ex_usage_id")
    _logger.info(
        "bf_employee_experience_expense: %s ligne(s) d'usage rattrapée(s) pour "
        "des dépenses approuvées sous la 18.0.1.0.0", len(created),
    )
