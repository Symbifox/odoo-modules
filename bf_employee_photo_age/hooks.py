def post_init_hook(env):
    """Date the photos already on file from their attachment."""
    env["hr.employee"].with_context(active_test=False).search([])._bf_photo_sync_date()
