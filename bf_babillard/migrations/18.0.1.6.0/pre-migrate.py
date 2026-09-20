# -*- coding: utf-8 -*-
"""`bf.babillard.jaime` devient `bf.babillard.geste`.

🔴 Ce script doit tourner AVANT le chargement du modèle neuf. Sans lui, Odoo ne
reconnaît pas `bf.babillard.geste`, crée une table vide à côté, et les réactions
déjà posées restent dans une table que plus personne ne lit. Elles ne sont pas
perdues, elles sont invisibles, ce qui est pire : rien ne le signale.

🔴 Le vrai piège n'est pas le renommage, c'est la CONTRAINTE. La table portait
`UNIQUE(post_id, user_id)` : une personne, une réaction. Le renommage ne
l'emporte pas, et Odoo ne la retrouve pas sous le nouveau nom de modèle, donc il
la laisse en place. Le module aurait alors annoncé « plusieurs réactions par
personne » et la base en aurait refusé la deuxième, sur les locataires montés
seulement. Un essai sur base neuve n'aurait rien vu : la table y naît avec la
bonne contrainte.
"""
import logging

_logger = logging.getLogger(__name__)

ANCIEN = "bf_babillard_jaime"
NOUVEAU = "bf_babillard_geste"


def _existe(cr, table):
    cr.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s",
               (table,))
    return bool(cr.fetchone())


def migrate(cr, version):
    if not version:
        return
    if _existe(cr, NOUVEAU) or not _existe(cr, ANCIEN):
        _logger.info("bf_babillard : renommage déjà fait, rien à reprendre")
        return

    # 1. La contrainte d'abord : elle porte le nom de l'ancienne table, et une
    #    fois celle-ci renommée on ne la retrouverait plus par son nom.
    cr.execute(
        "ALTER TABLE %s DROP CONSTRAINT IF EXISTS bf_babillard_jaime_jaime_unique"
        % ANCIEN)
    cr.execute(
        "DELETE FROM ir_model_constraint WHERE name = 'bf_babillard_jaime_jaime_unique'")

    # 2. La table, sa séquence et ses index.
    cr.execute("ALTER TABLE %s RENAME TO %s" % (ANCIEN, NOUVEAU))
    cr.execute("ALTER SEQUENCE IF EXISTS %s_id_seq RENAME TO %s_id_seq"
               % (ANCIEN, NOUVEAU))
    for ancien_index, nouvel_index in (
        ("bf_babillard_jaime_pkey", "bf_babillard_geste_pkey"),
        ("bf_babillard_jaime__post_id_index", "bf_babillard_geste__post_id_index"),
        ("bf_babillard_jaime__user_id_index", "bf_babillard_geste__user_id_index"),
    ):
        cr.execute("ALTER INDEX IF EXISTS %s RENAME TO %s"
                   % (ancien_index, nouvel_index))

    # 3. Les métadonnées. `ir_model_fields.relation` aussi : le One2many de la
    #    publication pointe vers l'ancien nom de modèle.
    cr.execute("UPDATE ir_model SET model = 'bf.babillard.geste' "
               "WHERE model = 'bf.babillard.jaime'")
    cr.execute("UPDATE ir_model_fields SET model = 'bf.babillard.geste' "
               "WHERE model = 'bf.babillard.jaime'")
    cr.execute("UPDATE ir_model_fields SET relation = 'bf.babillard.geste' "
               "WHERE relation = 'bf.babillard.jaime'")

    # 4. Les identifiants externes, pour que `ref('model_bf_babillard_geste')`
    #    des règles neuves résolve au lieu de tomber.
    cr.execute("""
        UPDATE ir_model_data
           SET name = 'model_bf_babillard_geste'
         WHERE module = 'bf_babillard'
           AND model = 'ir.model'
           AND name = 'model_bf_babillard_jaime'
    """)
    cr.execute("""
        UPDATE ir_model_data
           SET name = replace(name, 'field_bf_babillard_jaime__',
                                    'field_bf_babillard_geste__')
         WHERE module = 'bf_babillard'
           AND model = 'ir.model.fields'
           AND name LIKE 'field_bf_babillard_jaime__%'
    """)

    _logger.info("bf_babillard : %s renommée en %s, unicité de la paire levée",
                 ANCIEN, NOUVEAU)
