def migrate(cr, version):
    """An export queued for the superuser would read the registry past every rule:
    it fails, saying why, instead of running."""
    cr.execute("""
        UPDATE bf_document_export_run
           SET state = 'failed',
               log = 'Queued for the superuser before 18.0.1.1.1: not run. Ask for it again.'
         WHERE state IN ('queued', 'running') AND user_id = 1
    """)
