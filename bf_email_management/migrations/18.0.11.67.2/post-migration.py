"""18.0.11.67.2 : le cron du dépôt des avis sous le compte système.

`ir.actions.server.run()` vérifie, avant d'exécuter le code, que l'usager du
cron a le droit d'ÉCRIRE sur le modèle de l'action. Sur `bf.email.depot`, seul
`group_email_admin` l'a, et l'administrateur n'en fait pas forcément partie :
le cron levait AccessError à chaque passe, son `sudo()` jamais atteint.

Le compte système (`base.user_root`) passe ce contrôle comme les crons du cœur.
On ne rend pas le groupe à l'admin : il a été retiré exprès.

Le bloc du cron est `noupdate="1"` : sans cette migration, la base garderait
l'ancien usager. L'état actif n'est pas touché ici : qui a coupé le cron le
rallume lui-même.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref("bf_email_management.ir_cron_bf_email_depot",
                   raise_if_not_found=False)
    if not cron:
        return
    systeme = env.ref("base.user_root")
    if cron.user_id != systeme:
        _logger.info("Cron %s : usager %s -> %s", cron.id, cron.user_id.login,
                     systeme.login)
        cron.user_id = systeme
