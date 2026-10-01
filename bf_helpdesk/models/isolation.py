"""Une tâche planifiée qui traite des billets un à un ne doit pas tomber en bloc.

Une erreur de base sur UN billet interrompait
le curseur, annulait toute la passe, et la même erreur revenait à la passe
suivante, qui bloquait pour de bon toutes les relances (ou tous les sondages,
ou toute la file de triage). Chaque billet passe donc sous un savepoint ; hors
des essais, on valide après chacun, pour qu'un travailleur tué au-delà de
limit_time_real ne défasse pas ce qui a réussi avant lui.
"""
import logging
import time

from odoo import modules

_logger = logging.getLogger(__name__)


def each_isolated(records, fn, label, budget_s=None):
    """Appeler fn(record) pour chaque enregistrement, isolément.

    Rend le nombre d'enregistrements traités sans erreur. `budget_s` borne la
    durée de la passe : ce qui reste attend la passe suivante.
    """
    start = time.monotonic()
    ok = 0
    env = records.env
    for record in records:
        if budget_s is not None and time.monotonic() - start > budget_s:
            _logger.info("%s : budget de %s s atteint, la suite à la prochaine passe", label, budget_s)
            break
        try:
            with env.cr.savepoint():
                fn(record)
            ok += 1
        except Exception:  # noqa: BLE001 - un billet ne bloque pas les autres
            _logger.exception("%s : échec sur %s,%s", label, record._name, record.id)
        if not modules.module.current_test:
            env.cr.commit()
    return ok
