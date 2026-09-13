"""Le lien devient polymorphe : `task_id` cède la place à (`res_model`, `res_id`).

Fait avant que l'ORM ne charge les modèles, pour que les colonnes soient déjà là
et déjà remplies quand il voudra les poser en NOT NULL. Un lien dont la tâche a
disparu est retiré plutôt que porté avec un identifiant mort.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT column_name FROM information_schema.columns
         WHERE table_name = 'federation_link' AND column_name IN ('task_id', 'res_model', 'res_id')
    """)
    present = {row[0] for row in cr.fetchall()}
    if "task_id" not in present:
        return  # déjà migré

    if "res_model" not in present:
        cr.execute("ALTER TABLE federation_link ADD COLUMN res_model varchar")
    if "res_id" not in present:
        cr.execute("ALTER TABLE federation_link ADD COLUMN res_id integer")

    cr.execute("""
        UPDATE federation_link
           SET res_model = 'project.task', res_id = task_id
         WHERE task_id IS NOT NULL
    """)
    porte = cr.rowcount

    # Un lien sans tâche n'a jamais rien voulu dire : la colonne était requise.
    cr.execute("DELETE FROM federation_link WHERE res_id IS NULL OR res_model IS NULL")
    jete = cr.rowcount

    # Les noms relevés sur les deux bases de production le 2026-09-13 : la contrainte
    # est `federation_link_peer_task_unique`, l'index `federation_link__task_id_index`
    # (deux tirets bas, c'est la convention d'Odoo 18). Retirer la colonne emporterait
    # l'index et la clé étrangère de toute façon ; les nommer rend le geste lisible.
    cr.execute("ALTER TABLE federation_link DROP CONSTRAINT IF EXISTS federation_link_peer_task_unique")
    cr.execute("DROP INDEX IF EXISTS federation_link__task_id_index")
    cr.execute("ALTER TABLE federation_link DROP COLUMN task_id")
    cr.execute("""
        CREATE INDEX IF NOT EXISTS federation_link_res_model_res_id_index
            ON federation_link (res_model, res_id)
    """)
    _logger.info("bf_federation: %s lien(s) porté(s) vers (res_model, res_id), %s retiré(s) sans objet",
                 porte, jete)
