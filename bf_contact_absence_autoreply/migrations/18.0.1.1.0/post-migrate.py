"""18.0.1.1.0, avant le chargement des traductions : mettre de côté les retouches.

🔴 Chaque mise à jour du module recharge son catalogue, et pour un champ HTML
traduit terme par terme, Odoo RECONSTRUIT la valeur française sur la structure
de la valeur anglaise. Un message de maison retouché en français avec un
paragraphe de plus ou de moins n'a plus la même structure : il revient au texte
livré, `noupdate` ou pas. Mesuré au banc : la retouche avait disparu avant même
que `end-migrate` ne tourne.

Les valeurs hors en_US des données `noupdate` sont donc copiées ici, à l'étape
`post`, qui passe AVANT ce rechargement. `end-migrate` les remet et efface la
table.
"""

import json

from odoo import SUPERUSER_ID, api
from odoo.tools import SQL

MODULE = "bf_contact_absence_autoreply"
TABLE = "_bf_absence_autoreply_retouches"


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.flush_all()
    cr.execute(SQL("DROP TABLE IF EXISTS %s", SQL.identifier(TABLE)))
    cr.execute(SQL("CREATE TABLE %s (nom_table text, colonne text, res_id int, valeurs jsonb)",
                   SQL.identifier(TABLE)))
    donnees = env["ir.model.data"].search([("module", "=", MODULE), ("noupdate", "=", True)])
    for d in donnees:
        if d.model not in env:
            continue
        Model = env[d.model]
        for nom, champ in Model._fields.items():
            if not (champ.translate and champ.store and champ.column_type
                    and champ.column_type[0] == "jsonb"):
                continue
            cr.execute(SQL("SELECT %s FROM %s WHERE id = %s",
                           SQL.identifier(nom), SQL.identifier(Model._table), d.res_id))
            ligne = cr.fetchone()
            autres = {k: v for k, v in ((ligne and ligne[0]) or {}).items() if k != "en_US"}
            if autres:
                cr.execute(SQL("INSERT INTO %s VALUES (%s, %s, %s, %s::jsonb)",
                               SQL.identifier(TABLE), Model._table, nom, d.res_id,
                               json.dumps(autres)))
