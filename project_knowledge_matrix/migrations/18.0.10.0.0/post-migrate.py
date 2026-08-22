"""Corps documentaire: préparation des données existantes.

Aucun document n'est converti. On se contente de rendre le registre
exploitable par les nouvelles fonctions:

1. `body_source` = 'external' partout (les 199 documents restent des
   pointeurs vers Nextcloud tant qu'on ne les bascule pas un par un).
2. `sequence_index` rempli par ordre de publication: les numéros existants
   mélangent « 2.1 » et « 2025.11 », un tri lexicographique sur un Char donne
   déjà 2.10 avant 2.9. Le rang entier est la seule clé d'ordre fiable.
3. `previous_version_id` rempli quand il manque: le champ existait depuis
   l'origine mais aucun code ne l'écrivait. C'est l'ancre de la comparaison
   entre versions.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        UPDATE project_document
           SET body_source = 'external'
         WHERE body_source IS NULL
    """)
    _logger.info("body_source initialisé sur %s document(s)", cr.rowcount)

    # Rang de publication, par document, dans l'ordre chronologique réel.
    cr.execute("""
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY document_id
                       ORDER BY COALESCE(release_date, create_date), id
                   ) AS rank
              FROM project_document_version
        )
        UPDATE project_document_version v
           SET sequence_index = ranked.rank
          FROM ranked
         WHERE v.id = ranked.id
           AND COALESCE(v.sequence_index, 0) = 0
    """)
    _logger.info("sequence_index rempli sur %s version(s)", cr.rowcount)

    # Chaînage des versions: la précédente est celle dont le rang est juste
    # en dessous, dans le même document.
    cr.execute("""
        WITH chain AS (
            SELECT id,
                   LAG(id) OVER (
                       PARTITION BY document_id ORDER BY sequence_index, id
                   ) AS prev_id
              FROM project_document_version
        )
        UPDATE project_document_version v
           SET previous_version_id = chain.prev_id
          FROM chain
         WHERE v.id = chain.id
           AND chain.prev_id IS NOT NULL
           AND v.previous_version_id IS NULL
    """)
    _logger.info("previous_version_id chaîné sur %s version(s)", cr.rowcount)
