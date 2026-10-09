def migrate(cr, version):
    """« Export as » used to default to whoever installed the module: the superuser,
    who reads the registry without any rule. A template that needs the field gets
    it back from a manager; the others no longer need it."""
    cr.execute("UPDATE bf_document_export_template SET user_id = NULL WHERE user_id = 1")
