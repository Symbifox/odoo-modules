"""Trace ce qui va disparaître avant que les colonnes ne soient supprimées.

Le catalogue de logiciels (`document.software`) et le champ `department_id`
(seule attache au module `hr`) sortent du module en 18.0.11.0.0.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    for table, colonne, libelle in (
        ('project_document', 'department_id', 'documents'),
        ('project_document_distribution', 'department_id', 'distributions'),
    ):
        cr.execute(
            """
            SELECT 1 FROM information_schema.columns
             WHERE table_name = %s AND column_name = %s
            """,
            (table, colonne),
        )
        if not cr.fetchone():
            continue
        cr.execute(
            f'SELECT id, {colonne} FROM {table} WHERE {colonne} IS NOT NULL'
        )
        lignes = cr.fetchall()
        if lignes:
            _logger.warning(
                "project_knowledge_matrix 11.0.0 : %s %s perdent leur "
                "département (id, department_id) = %s",
                len(lignes), libelle, lignes,
            )

    for table in ('document_software', 'document_software_version'):
        cr.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
            (table,),
        )
        if not cr.fetchone():
            continue
        cr.execute(f'SELECT COUNT(*) FROM {table}')
        nombre = cr.fetchone()[0]
        if nombre:
            _logger.warning(
                "project_knowledge_matrix 11.0.0 : %s enregistrements dans "
                "%s vont être supprimés. hosting.software prend le relais.",
                nombre, table,
            )
