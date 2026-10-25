"""Un point de sauvegarde pour ce qui ne doit pas casser le geste qui l'a provoqué.

La fête « à jour », la constance refaite après une absence : si elles échouent,
le geste (fermer une tâche, déclarer une absence, valider un congé) doit passer
quand même. Avaler l'erreur sans point de sauvegarde laissait la transaction
avortée : la requête échouait plus loin, sur une autre requête SQL.

Deux pièges du cœur d'Odoo 18, évités ici :

- le point de sauvegarde « qui vide » (`cr.savepoint()`) vide, s'il est annulé,
  TOUS les précommits du curseur : le suivi des champs et les messages de bus
  du geste lui-même seraient perdus. On vide donc l'ORM à la main, avant et
  après, et on n'efface que son cache en cas d'échec ;
- une erreur de concurrence (sérialisation, verrou, interblocage) ne se répare
  pas en revenant au point de sauvegarde : elle remonte, et Odoo rejoue la
  requête entière.

`tolerated` est l'aide commune des crédits d'XP posés sur le travail des autres
modules (tâches, activités, messages, courriels...) : ils avalent leurs erreurs
pour ne pas casser le geste, mais jamais une erreur de concurrence (avalée,
elle laissait la transaction avortée et l'usager voyait une erreur au lieu
d'un rejeu).
"""
import logging
from contextlib import contextmanager

from psycopg2 import errors

_logger = logging.getLogger(__name__)

PG_CONCURRENCY_ERRORS = (errors.SerializationFailure, errors.LockNotAvailable,
                         errors.DeadlockDetected)


class Outcome:
    """Ce que `isolated` rend à qui veut savoir si le bloc est allé au bout : `ok` reste faux
    tant que le point de sauvegarde n'est pas fermé sans erreur."""
    ok = False


@contextmanager
def isolated(env, label, replay=True):
    """Exécute le bloc dans un point de sauvegarde ; une erreur est journalisée et
    annulée, sauf une erreur de concurrence, qui remonte.

    `replay=False` : personne ne rejouera (un cron). L'erreur de concurrence est
    alors annulée comme les autres : un profil en conflit n'emporte pas ceux
    déjà refaits, il se refera à son prochain geste ou au prochain passage.
    """
    outcome = Outcome()
    env.flush_all()
    savepoint = env.cr.savepoint(flush=False)
    try:
        yield outcome
        env.flush_all()
    except PG_CONCURRENCY_ERRORS:
        savepoint.close(rollback=True)
        env.clear()
        if replay:
            raise
        _logger.warning("Fox Quest : conflit de concurrence, annulé (%s)", label, exc_info=True)
    except Exception:
        savepoint.close(rollback=True)
        # Ce que le bloc a mis en cache n'existe plus en base.
        env.clear()
        _logger.warning("Fox Quest : erreur ignorée (%s)", label, exc_info=True)
    else:
        savepoint.close(rollback=False)
        outcome.ok = True


@contextmanager
def tolerated(label):
    """Avale l'erreur du bloc pour ne pas casser le geste, sauf une erreur de
    concurrence de Postgres : elle remonte, pour qu'Odoo rejoue la requête."""
    try:
        yield
    except PG_CONCURRENCY_ERRORS:
        raise
    except Exception:
        _logger.warning("Fox Quest : erreur ignorée (%s)", label, exc_info=True)
