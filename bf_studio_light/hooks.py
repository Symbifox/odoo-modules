import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Recreate ir.model.fields and ir.ui.view records for any Forge
    metadata that lost its underlying record (typical after -u all when
    a parent module is reinstalled, or after a v-bump migration)."""
    _logger.info("Forge: post_init integrity check starting")
    env["studio.light.field"]._ensure_all_provisioned()
    _logger.info("Forge: post_init integrity check complete")
