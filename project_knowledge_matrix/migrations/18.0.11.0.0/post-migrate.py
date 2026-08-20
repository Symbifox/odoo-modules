"""Retire le schéma du catalogue de logiciels et des champs `hr.department`.

Odoo ne supprime jamais de lui-même une colonne devenue obsolète : il se
contente de la signaler dans le journal. On la retire donc explicitement.
"""

import logging

_logger = logging.getLogger(__name__)

TABLES_RETIREES = (
    'document_software_version',   # d'abord : porte la clé étrangère
    'document_software',
)

COLONNES_RETIREES = (
    ('project_document', 'software_id'),
    ('project_document', 'software_version_id'),
    ('project_document', 'department_id'),
    ('project_document_distribution', 'department_id'),
)


def migrate(cr, version):
    if not version:
        return

    for table, colonne in COLONNES_RETIREES:
        cr.execute(
            f'ALTER TABLE IF EXISTS "{table}" DROP COLUMN IF EXISTS "{colonne}"'
        )
        _logger.info('colonne retirée : %s.%s', table, colonne)

    for table in TABLES_RETIREES:
        cr.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
        _logger.info('table retirée : %s', table)

    # Les vues, menus, règles et ACL disparus des fichiers de données sont
    # nettoyés par Odoo lui-même. Restent les traces des deux modèles.
    cr.execute(
        """
        DELETE FROM ir_model_fields
         WHERE model IN ('document.software', 'document.software.version')
            OR (model = 'project.document'
                AND name IN ('software_id', 'software_version_id',
                             'department_id'))
            OR (model = 'project.document.distribution'
                AND name = 'department_id')
        """
    )
    # Supprimer ir_model fait tomber en cascade les ir.rule qui le visent, mais
    # laisse derrière les pointeurs ir_model_data qui les nommaient. On les
    # retire d'abord pour ne pas laisser de références mortes.
    cr.execute(
        """
        DELETE FROM ir_model_data
         WHERE module = 'project_knowledge_matrix'
           AND name IN ('rule_document_software_read',
                        'rule_document_software_manager',
                        'rule_document_software_version_read',
                        'rule_document_software_version_manager')
        """
    )
    cr.execute(
        """
        DELETE FROM ir_model
         WHERE model IN ('document.software', 'document.software.version')
        """
    )
