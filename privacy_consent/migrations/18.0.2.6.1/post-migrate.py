"""Post-migration: Force-reload notice and purpose data.

The XML data files have noupdate="1" so normal upgrades skip them.
Use mode='init' to force Odoo to re-read the XML and update existing
records to the authoritative consent form texts (F-01 through F-07).
"""
import logging

from odoo import api, SUPERUSER_ID
from odoo.tools import convert

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    _logger.info(
        "privacy_consent post-migrate 2.6.1: "
        "force-reloading notice and purpose data from XML"
    )

    env = api.Environment(cr, SUPERUSER_ID, {})

    for data_file in (
        "data/privacy_purpose_data.xml",
        "data/privacy_notice_data.xml",
    ):
        _logger.info("  Reloading %s", data_file)
        convert.convert_file(
            env, "privacy_consent", data_file, {}, mode="init",
        )

    _logger.info("  Done reloading consent data")
