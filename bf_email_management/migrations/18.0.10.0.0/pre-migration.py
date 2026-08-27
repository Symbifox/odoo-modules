"""Retire l'ancien journal de réacheminement, devenu le journal des envois auto.

`bf.email.rule.forward.log` est né ce matin même, en 18.0.9.11.0, pour le
réacheminement. La réponse d'absence a le même besoin — savoir ce qui est parti
et pourquoi ce qui n'est pas parti n'est pas parti — alors le journal devient
`bf.email.auto.log`, avec un champ `kind`.

Le renommage se fait par **suppression de l'ancienne table**, pas par un
`ALTER TABLE … RENAME` : le modèle est neuf du jour et n'a aucune ligne en
production, donc il n'y a rien à transporter. Le script le **vérifie** au lieu
de le supposer — si des lignes existent, il refuse et laisse tout en place
plutôt que de jeter un journal d'audit sans le dire.

⚠️ **L'ordre des trois opérations n'est pas décoratif.** Le cron de purge est en
`noupdate`, donc son action serveur pointe encore sur l'ancien `ir.model`, et
`ir_act_server.model_id` cascade à la suppression du modèle. Supprimer le modèle
en premier fait donc disparaître l'action serveur, ce que la clé étrangère de
`ir_cron` refuse — la mise à niveau s'arrête au milieu. On débranche le cron
d'abord, on supprime ensuite, et le post-migrate le rebranche sur le nouveau
modèle une fois celui-ci créé.
"""

import logging

_logger = logging.getLogger(__name__)

ANCIENNE_TABLE = "bf_email_rule_forward_log"
ANCIEN_MODELE = "bf.email.rule.forward.log"
# Un modèle du même module, certain d'exister avant comme après : il sert de
# point d'attache provisoire au cron, le temps de la bascule.
MODELE_RELAIS = "bf.email"


def _table_exists(cr, table):
    cr.execute("SELECT to_regclass(%s)", (table,))
    return cr.fetchone()[0] is not None


def migrate(cr, version):
    if not _table_exists(cr, ANCIENNE_TABLE):
        _logger.info("bf.email.auto.log : rien à reprendre, table absente.")
        return

    cr.execute("SELECT COUNT(*) FROM %s" % ANCIENNE_TABLE)
    lignes = cr.fetchone()[0]
    if lignes:
        _logger.error(
            "bf.email.auto.log : %s ligne(s) dans %s. La table est CONSERVÉE "
            "et le nouveau modèle démarre vide — reprenez l'historique à la "
            "main plutôt que de le perdre.", lignes, ANCIENNE_TABLE,
        )
        return

    # 1. Débrancher le cron du modèle qu'on va supprimer.
    cr.execute(
        """
        UPDATE ir_act_server s
           SET model_id = (SELECT id FROM ir_model WHERE model = %s)
          FROM ir_cron c, ir_model_data d
         WHERE c.ir_actions_server_id = s.id
           AND d.model = 'ir.cron' AND d.res_id = c.id
           AND d.module = 'bf_email_management'
           AND d.name = 'ir_cron_forward_log_prune'
        """,
        (MODELE_RELAIS,),
    )
    _logger.info(
        "ir.cron : purge du journal débranchée le temps de la bascule "
        "(%s ligne(s)).", cr.rowcount)

    # 2. Couper tout ce qui pointerait encore sur l'ancien modèle.
    cr.execute(
        """
        UPDATE ir_act_server SET model_id = NULL
         WHERE model_id IN (SELECT id FROM ir_model WHERE model = %s)
        """,
        (ANCIEN_MODELE,),
    )

    # 3. Supprimer.
    cr.execute("DROP TABLE IF EXISTS %s CASCADE" % ANCIENNE_TABLE)
    cr.execute("DELETE FROM ir_model_fields WHERE model = %s", (ANCIEN_MODELE,))
    cr.execute(
        """
        DELETE FROM ir_model_data
         WHERE model = 'ir.model'
           AND res_id IN (SELECT id FROM ir_model WHERE model = %s)
        """,
        (ANCIEN_MODELE,),
    )
    cr.execute("DELETE FROM ir_model WHERE model = %s", (ANCIEN_MODELE,))
    _logger.info(
        "bf.email.auto.log : ancienne table %s retirée (elle était vide).",
        ANCIENNE_TABLE,
    )
