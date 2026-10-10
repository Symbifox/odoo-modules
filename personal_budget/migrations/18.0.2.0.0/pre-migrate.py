"""18.0.2.0.0, avant le chargement : préparer une base montée en 18.0.1.x.

Deux choses, et elles doivent passer AVANT que le registre charge les modèles.

1. **Le registre de partage change de nom technique.** Le modèle portait un
   nom de personne (un nom qui ne doit plus apparaître nulle part). Il devient
   `personal.budget.share.line`. On renomme la table, sa séquence, ses
   contraintes, et toutes les références d'Odoo au modèle (`ir_model`,
   `ir_model_fields`, `ir_model_data`, vues, action, filtres) : sans ça, Odoo
   créerait une table neuve et laisserait les lignes dans l'ancienne.
   Les contraintes se renomment AVANT la table : renommer une table ne renomme pas ses contraintes.

2. **Les règles d'enregistrement étaient en `noupdate="1"`.** Une montée ne les
   aurait jamais réécrites : les nouvelles règles par budget ne se seraient
   appliquées qu'aux bases neuves. On lève le verrou pour qu'elles suivent le
   code.
"""
import logging

_logger = logging.getLogger(__name__)

NEW_MODEL = 'personal.budget.share.line'
NEW_TABLE = NEW_MODEL.replace('.', '_')


def _find_old_model(cr):
    """L'ancien registre de partage, retrouvé par sa FORME et non par son nom.

    Il s'appelait `personal.budget.<prénom>.transaction`. Ce prénom ne doit plus
    apparaître nulle part, ce script compris : on cherche donc l'unique modèle
    du module de la forme `personal.budget.%.transaction`, autre que les
    transactions elles-mêmes.
    """
    cr.execute("""
        SELECT m.model FROM ir_model m
          JOIN ir_model_data d ON d.model = 'ir.model' AND d.res_id = m.id
         WHERE d.module = 'personal_budget'
           AND m.model LIKE 'personal.budget.%%.transaction'
           AND m.model != 'personal.budget.transaction'
    """)
    rows = [r[0] for r in cr.fetchall()]
    if len(rows) > 1:
        raise RuntimeError("personal_budget : plusieurs anciens registres de partage : %s" % rows)
    return rows[0] if rows else None


def _table_exists(cr, table):
    cr.execute("SELECT 1 FROM pg_class WHERE relname = %s AND relkind = 'r'", (table,))
    return bool(cr.fetchone())


def _rename_share_model(cr):
    old_model = _find_old_model(cr)
    if not old_model:
        return
    old_table = old_model.replace('.', '_')
    token = old_model.split('.')[2]
    if not _table_exists(cr, old_table):
        return
    OLD_MODEL, OLD_TABLE = old_model, old_table
    # ir_model_data : ancien nom -> nouveau nom
    xmlid_renames = {
        'model_' + OLD_TABLE: 'model_' + NEW_TABLE,
        'view_%s_transaction_list' % token: 'view_share_line_list',
        'view_%s_transaction_form' % token: 'view_share_line_form',
        'action_%s_transaction' % token: 'action_share_line',
        'menu_budget_%s' % token: 'menu_budget_share_line',
        'access_budget_%s' % token: 'access_budget_share_line',
        'budget_%s_transaction_rule' % token: 'budget_share_line_rule',
    }
    if _table_exists(cr, NEW_TABLE):
        raise RuntimeError(
            "personal_budget : %s et %s existent tous deux, migration arrêtée." % (OLD_TABLE, NEW_TABLE))

    # Contraintes et index d'abord, tant qu'on les retrouve sous l'ancien préfixe.
    cr.execute("""
        SELECT conname FROM pg_constraint
         WHERE conrelid = %s::regclass AND conname LIKE %s
    """, (OLD_TABLE, OLD_TABLE + '%'))
    for (conname,) in cr.fetchall():
        new_name = NEW_TABLE + conname[len(OLD_TABLE):]
        cr.execute('ALTER TABLE "%s" RENAME CONSTRAINT "%s" TO "%s"' % (OLD_TABLE, conname, new_name))
        cr.execute("UPDATE ir_model_constraint SET name = %s WHERE name = %s", (new_name, conname))
    cr.execute("""
        SELECT indexname FROM pg_indexes
         WHERE tablename = %s AND indexname LIKE %s
    """, (OLD_TABLE, OLD_TABLE + '%'))
    for (indexname,) in cr.fetchall():
        cr.execute('ALTER INDEX "%s" RENAME TO "%s"' % (indexname, NEW_TABLE + indexname[len(OLD_TABLE):]))
    cr.execute("SELECT 1 FROM pg_class WHERE relname = %s AND relkind = 'S'", (OLD_TABLE + '_id_seq',))
    if cr.fetchone():
        cr.execute('ALTER SEQUENCE "%s_id_seq" RENAME TO "%s_id_seq"' % (OLD_TABLE, NEW_TABLE))

    cr.execute('ALTER TABLE "%s" RENAME TO "%s"' % (OLD_TABLE, NEW_TABLE))

    cr.execute("UPDATE ir_model SET model = %s WHERE model = %s", (NEW_MODEL, OLD_MODEL))
    cr.execute("UPDATE ir_model_fields SET model = %s WHERE model = %s", (NEW_MODEL, OLD_MODEL))
    cr.execute("UPDATE ir_model_fields SET relation = %s WHERE relation = %s", (NEW_MODEL, OLD_MODEL))
    cr.execute("UPDATE ir_ui_view SET model = %s WHERE model = %s", (NEW_MODEL, OLD_MODEL))
    cr.execute("UPDATE ir_act_window SET res_model = %s WHERE res_model = %s", (NEW_MODEL, OLD_MODEL))
    cr.execute("UPDATE ir_filters SET model_id = %s WHERE model_id = %s", (NEW_MODEL, OLD_MODEL))
    cr.execute("UPDATE ir_model_data SET model = %s WHERE model = %s", (NEW_MODEL, OLD_MODEL))

    # Identifiants externes des champs : field_<ancienne_table>__<champ>
    cr.execute("""
        UPDATE ir_model_data
           SET name = %s || substr(name, %s)
         WHERE module = 'personal_budget' AND name LIKE %s
    """, ('field_' + NEW_TABLE, len('field_' + OLD_TABLE) + 1, 'field_' + OLD_TABLE + '\\_\\_%'))
    for old, new in xmlid_renames.items():
        cr.execute("""
            UPDATE ir_model_data SET name = %s
             WHERE module = 'personal_budget' AND name = %s
        """, (new, old))
    _logger.info("personal_budget : registre de partage renommé en %s", NEW_MODEL)


def migrate(cr, version):
    if not version:
        return
    _rename_share_model(cr)
    cr.execute("""
        UPDATE ir_model_data SET noupdate = false
         WHERE module = 'personal_budget' AND model = 'ir.rule'
    """)
    _logger.info("personal_budget : %s règle(s) d'enregistrement déverrouillée(s)", cr.rowcount)
