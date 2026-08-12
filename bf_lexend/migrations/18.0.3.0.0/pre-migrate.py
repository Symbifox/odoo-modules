"""Guard the ownership transfer of brand color fields to bluefox_branding.

Before 18.0.3.0.0, bf_lexend declared `report_brand_primary` and `report_brand_dark`
on res.company and shipped the Settings UI. From this version on, those fields live
in bluefox_branding. If bluefox_branding isn't queued for install/upgrade alongside
this upgrade, the columns survive (Odoo doesn't drop columns) but the Settings UI
disappears and admins lose the ability to edit them through the UI.

Raise a clear error in that situation so the admin reinstalls/installs bluefox_branding
before completing the bf_lexend upgrade.
"""
import logging

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute(
        """
        SELECT state
        FROM ir_module_module
        WHERE name = 'bluefox_branding'
        """
    )
    row = cr.fetchone()
    state = row[0] if row else None

    acceptable = {"installed", "to install", "to upgrade"}
    if state not in acceptable:
        raise UserError(
            "bf_lexend 18.0.3.0.0 transfers ownership of report_brand_primary / "
            "report_brand_dark (and the Settings UI for them) to bluefox_branding. "
            "Install or upgrade bluefox_branding first, then retry this upgrade. "
            f"Current bluefox_branding state: {state or 'not present'}."
        )

    _logger.info(
        "bf_lexend 18.0.3.0.0: bluefox_branding state=%s — ownership transfer cleared.",
        state,
    )
