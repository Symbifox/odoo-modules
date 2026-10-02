"""18.0.1.1.0 : l'unité devient facultative, la société porte la portée.

`company_id` était un champ lié stocké (`unit_id.company_id`) sur les
obligations, les comités et les affichages. Il devient un champ calculé
éditable, OBLIGATOIRE. La colonne existe déjà et n'est pas recalculée par la
montée : on la remplit ici depuis l'unité, avant que l'ORM ne pose le NOT NULL,
pour qu'aucune ligne ne reste vide ni en désaccord avec son unité.

Rien d'autre ne bouge : chaque enregistrement existant garde son unité, et le
NOT NULL de `unit_id` est levé par l'ORM lui-même.
"""

from odoo.tools import SQL
from odoo.tools.sql import column_exists

TABLES = ("bf_labour_obligation", "bf_labour_committee", "bf_labour_posting")


def migrate(cr, version):
    if not version:
        return
    for table in TABLES:
        if not column_exists(cr, table, "company_id"):
            continue
        cr.execute(SQL(
            """
            UPDATE %(table)s AS rec
               SET company_id = unit.company_id
              FROM bf_labour_unit AS unit
             WHERE rec.unit_id = unit.id
               AND rec.company_id IS DISTINCT FROM unit.company_id
            """,
            table=SQL.identifier(table),
        ))
