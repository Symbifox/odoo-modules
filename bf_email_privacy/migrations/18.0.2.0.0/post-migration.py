"""18.0.1.0.0 → 18.0.2.0.0 : le cron ne détruit plus, il propose.

⚠️ L'enregistrement du cron est `noupdate="1"` — sinon chaque montée de version
rallumerait un cron que l'administrateur a délibérément éteint. Conséquence :
le `-u` ne réécrit NI son nom NI son `code`, et la version 1 laissait donc en
base un appel à `_cron_privacy_destroy_expired()`, méthode supprimée par cette
version. Activé tel quel, le cron aurait planté à chaque passage.

⚠️ Écrit par l'ORM et non en SQL : `ir_cron` n'a pas de colonne `code`. Le code
et le nom vivent sur `ir_act_server`, et `ir_cron.cron_name` en est une copie
stockée. Un `UPDATE ir_cron SET code = …` échouerait, et viser `ir_act_server`
par l'`id` du cron toucherait une autre action.

`active` n'est pas touché : c'est un choix d'exploitation, pas une donnée de
module.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

NEW_NAME = "Courriels : proposer la destruction du contenu échu (Loi 25)"
NEW_CODE = "model._cron_privacy_propose_destruction()"


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref(
        "bf_email_privacy.cron_bf_email_destroy_expired",
        raise_if_not_found=False,
    )
    if not cron:
        _logger.warning(
            "bf_email_privacy: cron de rétention introuvable, rien à migrer"
        )
        return
    cron.write({"name": NEW_NAME, "code": NEW_CODE})
    _logger.info(
        "bf_email_privacy: cron #%s repointé vers %s (actif = %s, inchangé)",
        cron.id, NEW_CODE, cron.active,
    )
