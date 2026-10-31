"""The school's own allergen list (bf.school.allergen) gives way to the range's shared one
(bf.food.allergen), the one the kitchen's dishes carry.

Each school allergen goes to the shared allergen of the same key (the eleven priority allergens of
Health Canada, same xmlid names in both modules), else of the same English name, else a new shared
allergen with its names. The students' allergies and the meals' allergens are copied to the new
tables, then the old tables are dropped: the model itself is removed by Odoo at the end of the
update (its xmlids are no longer loaded).
"""
import logging

from psycopg2.extras import Json

_logger = logging.getLogger(__name__)


def _table_exists(cr, name):
    cr.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s", [name])
    return bool(cr.fetchone())


def _relation_columns(cr, table):
    cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", [table])
    return {row[0] for row in cr.fetchall()}


def migrate(cr, version):
    if not version or not _table_exists(cr, "bf_school_allergen"):
        return
    cr.execute("""SELECT a.id, a.name, a.sequence, a.active, d.name
                    FROM bf_school_allergen a
               LEFT JOIN ir_model_data d ON d.model = 'bf.school.allergen' AND d.res_id = a.id
                                        AND d.module = 'bf_school_meal'""")
    mapping = {}
    for old_id, name, sequence, active, xmlid in cr.fetchall():
        new_id = None
        if xmlid:
            cr.execute("SELECT res_id FROM ir_model_data WHERE module = 'bf_food_allergen' AND name = %s "
                       "AND model = 'bf.food.allergen'", [xmlid])
            row = cr.fetchone()
            new_id = row and row[0]
        if not new_id:
            cr.execute("SELECT id FROM bf_food_allergen WHERE lower(name->>'en_US') = lower(%s) "
                       "ORDER BY id LIMIT 1", [(name or {}).get("en_US") or ""])
            row = cr.fetchone()
            new_id = row and row[0]
        if not new_id:
            cr.execute("""INSERT INTO bf_food_allergen (name, sequence, active, create_uid, write_uid,
                                                         create_date, write_date)
                          VALUES (%s, %s, %s, 1, 1, now() at time zone 'UTC', now() at time zone 'UTC')
                          RETURNING id""", [Json(name or {"en_US": "?"}), sequence or 10, active])
            new_id = cr.fetchone()[0]
            _logger.info("bf_school_meal: school allergen %s added to the shared list (%s)", old_id, new_id)
        if active:
            # The kitchen may have archived the shared allergen the school still uses: an allergy on an
            # archived allergen is not read, and the meal would no longer be refused to the student.
            cr.execute("UPDATE bf_food_allergen SET active = true WHERE id = %s AND active IS NOT TRUE RETURNING id",
                       [new_id])
            if cr.fetchone():
                _logger.warning("bf_school_meal: shared allergen %s reactivated (the school uses it)", new_id)
        mapping[old_id] = new_id

    copied = {}
    for old_table, new_table, owner_new in (
            ("bf_school_student_allergen_rel", "bf_school_student_food_allergen_rel", "partner_id"),
            ("bf_school_allergen_bf_school_meal_item_rel", "bf_school_meal_item_food_allergen_rel", "item_id")):
        if not _table_exists(cr, old_table):
            continue
        columns = _relation_columns(cr, old_table)
        owner_old = "partner_id" if "partner_id" in columns else "bf_school_meal_item_id"
        allergen_old = "allergen_id" if "allergen_id" in columns else "bf_school_allergen_id"
        cr.execute(f'SELECT "{owner_old}", "{allergen_old}" FROM "{old_table}"')
        rows = {(owner, mapping[allergen]) for owner, allergen in cr.fetchall() if allergen in mapping}
        for owner, allergen in rows:
            cr.execute(f'INSERT INTO "{new_table}" ("{owner_new}", allergen_id) VALUES (%s, %s) '
                       f'ON CONFLICT DO NOTHING', [owner, allergen])
        copied[new_table] = len(rows)
        cr.execute(f'DROP TABLE "{old_table}"')
        cr.execute("DELETE FROM ir_model_relation WHERE name = %s", [old_table])
    # The old allergens' xmlids are noupdate: Odoo would leave them pointing at a dropped table.
    cr.execute("DELETE FROM ir_model_data WHERE module = 'bf_school_meal' AND model = 'bf.school.allergen'")
    # Odoo removes the model's records at the end of the update but leaves its table. No CASCADE:
    # a reference forgotten here must fail the update, not vanish with the table.
    cr.execute("DROP TABLE bf_school_allergen")
    _logger.info("bf_school_meal: %s school allergens moved to the shared list, links copied: %s",
                 len(mapping), copied)
