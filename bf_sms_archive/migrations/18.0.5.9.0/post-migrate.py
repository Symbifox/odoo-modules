"""Rattache les fils existants à leur ligne d'origine.

Le partage de lignes (18.0.5.9.0) fait porter la visibilité d'un fil par
``sms.archive.thread.line_id``. Les fils antérieurs n'en ont pas : on le déduit
du DERNIER message qui en porte une, c'est-à-dire la ligne sur laquelle la
conversation se tient réellement aujourd'hui.

Un fil sans aucun message « live » (archive Android importée) reste sans ligne :
il n'appartient qu'à son propriétaire, ce qui est le comportement d'avant.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE sms_archive_thread t
           SET line_id = sub.line_id
          FROM (
                SELECT DISTINCT ON (thread_id) thread_id, line_id
                  FROM sms_archive_message
                 WHERE line_id IS NOT NULL
              ORDER BY thread_id, id DESC
               ) sub
         WHERE t.id = sub.thread_id
           AND t.line_id IS NULL
    """)
    _logger.info("bf_sms_archive : %s fil(s) rattaché(s) à leur ligne d'origine.",
                 cr.rowcount)
