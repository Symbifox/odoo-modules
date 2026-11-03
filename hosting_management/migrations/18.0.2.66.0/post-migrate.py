import logging
from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

MARQUEUR = "hosting.backup_timestamps_utc_migrated"


def _corriger_les_horodatages(env):
    """Ramener en UTC les horodatages des rapports, écrits en heure locale.

    🔴 `backup-all-services.restic.sh` pose son horodatage avec
    `date +'%Y-%m-%d %H:%M:%S'`, donc l'heure locale de l'hôte, sans fuseau.
    Odoo range les Datetime en UTC : les rapports arrivaient donc datés de
    cinq heures trop tôt, mesuré sur une base réelle (snapshots à 04:00:01 pendant
    que le watchdog, lui en ISO, datait les mêmes sauvegardes de 09:00 à
    11:31 UTC). Le contrôleur convertit désormais à l'entrée ; sans cette
    reprise, l'historique resterait décalé et la couverture déclarerait
    périmées, pendant deux heures chaque nuit, des sauvegardes qui ont
    tourné.

    Le décalage se calcule date par date (l'heure avancée change de 4 à 5 h),
    et la reprise ne joue qu'une fois : le marqueur empêche une seconde
    conversion, qui décalerait tout une deuxième fois.
    """
    ICP = env["ir.config_parameter"].sudo()
    if ICP.get_param(MARQUEUR):
        _logger.info("Horodatages déjà ramenés en UTC, reprise ignorée")
        return 0
    tz_name = ICP.get_param("hosting.backup_report_timezone", "America/Toronto")
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        _logger.warning(
            "Fuseau %r inconnu : les horodatages restent tels quels", tz_name
        )
        return 0

    touches = 0
    for table, colonne in (
        ("hosting_backup_run", "run_date"),
        ("hosting_backup_snapshot", "snapshot_date"),
    ):
        env.cr.execute(
            "SELECT id, %s FROM %s WHERE %s IS NOT NULL"
            % (colonne, table, colonne)
        )
        lignes = env.cr.fetchall()
        for rec_id, valeur in lignes:
            juste = (
                valeur.replace(tzinfo=tz)
                .astimezone(timezone.utc)
                .replace(tzinfo=None)
            )
            if juste == valeur:
                continue
            env.cr.execute(
                "UPDATE %s SET %s = %%s WHERE id = %%s" % (table, colonne),
                (juste, rec_id),
            )
            touches += 1
        _logger.info("%s : %d ligne(s) lue(s)", table, len(lignes))

    ICP.set_param(MARQUEUR, tz_name)
    _logger.info(
        "Horodatages des rapports ramenés de %s vers UTC : %d ligne(s)",
        tz_name,
        touches,
    )
    return touches


def migrate(cr, version):
    """Rattacher les snapshots à leur service, et remettre leurs dates en UTC.

    `hosting.backup.snapshot.service_id` tient au nom du conteneur
    observé dans la charge utile. Les snapshots reçus avant que le champ
    existe portent ce nom sans le lien : sans cette reprise, la couverture
    d'un service démarrerait à zéro là où les sauvegardes tournent depuis des
    mois. Un conteneur partagé par plusieurs services reste non résolu, à
    dessein.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    _corriger_les_horodatages(env)
    env["hosting.backup.snapshot"]._backfill_service_ids()
