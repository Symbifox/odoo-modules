"""Reporte le suivi automatique d'une tâche unique vers la liste de tâches.

Jusqu'à la 5.12, ``sms.archive.thread.auto_post_task_id`` était un ``Many2one``:
un fil ne pouvait relayer ses nouveaux messages que vers une seule tâche. La
5.13.0 le remplace par ``auto_post_task_ids``.

Odoo ne supprime pas la colonne d'un champ retiré, donc l'ancienne valeur est
encore lisible en SQL au moment où ce script tourne. On la recopie, puis on
retire la colonne : la laisser en place ferait croire, à la prochaine lecture du
schéma, que le champ existe toujours.

Rien à reconstruire du côté du registre des rattachements
(``sms.archive.link``) : il n'existe aucune trace, dans l'historique, de quel
message a produit quelle note de chatter. Fabriquer des rattachements à partir
des notes déjà publiées reviendrait à inventer des données que personne ne
pourrait vérifier. Le registre part donc vide et se remplit aux envois suivants.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'sms_archive_thread'
           AND column_name = 'auto_post_task_id'
    """)
    if not cr.fetchone():
        _logger.info("auto_post_task_id absent : rien à reporter.")
        return

    # ON CONFLICT : la table de relation vient d'être créée par l'ORM, mais un
    # `-u` rejoué doit rester sans effet plutôt que d'échouer sur la clé.
    cr.execute("""
        INSERT INTO sms_thread_auto_task_rel (thread_id, task_id)
        SELECT t.id, t.auto_post_task_id
          FROM sms_archive_thread t
          JOIN project_task pt ON pt.id = t.auto_post_task_id
         WHERE t.auto_post_task_id IS NOT NULL
        ON CONFLICT DO NOTHING
    """)
    moved = cr.rowcount
    cr.execute("ALTER TABLE sms_archive_thread DROP COLUMN auto_post_task_id")
    _logger.info("Suivi automatique reporté pour %s conversation(s).", moved)
