"""Rebranche ce que le renommage du journal a laissé pendre.

Le cron de purge est en `noupdate` : son `model_id` ne suit pas le changement
de modèle — et ce `model_id` vit sur `ir_act_server`, dont `ir.cron` hérite,
pas sur `ir_cron`. Le pre-migrate le repointe déjà, mais seulement si le nouveau modèle
existait à ce moment-là — ce qui n'est pas le cas la toute première fois, car
Odoo crée les tables entre les deux scripts. D'où cette seconde passe, qui est
celle qui réussit.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("SELECT id FROM ir_model WHERE model = 'bf.email.auto.log'")
    row = cr.fetchone()
    if not row:
        _logger.error(
            "bf.email.auto.log : modèle introuvable après la mise à niveau.")
        return
    model_id = row[0]

    cr.execute(
        """
        UPDATE ir_act_server s
           SET model_id = %s
          FROM ir_cron c, ir_model_data d
         WHERE c.ir_actions_server_id = s.id
           AND d.model = 'ir.cron' AND d.res_id = c.id
           AND d.module = 'bf_email_management'
           AND d.name = 'ir_cron_forward_log_prune'
           AND s.model_id IS DISTINCT FROM %s
        """,
        (model_id, model_id),
    )
    if cr.rowcount:
        _logger.info(
            "ir.cron : purge du journal repointée sur bf.email.auto.log.")

    cr.execute(
        "UPDATE bf_email_auto_log SET kind = 'forward' WHERE kind IS NULL")
    if cr.rowcount:
        _logger.info("bf.email.auto.log : %s ligne(s) typée(s).", cr.rowcount)
