import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Fields that trigger re-sync when changed
_SYNC_FIELDS = {
    "name", "email", "phone", "mobile", "function",
    "parent_id", "company_name", "street", "street2",
    "city", "state_id", "zip", "country_id",
    "website", "comment", "image_1920",
}


class ResPartner(models.Model):
    """Extension of res.partner for Nextcloud contacts sync."""

    _inherit = "res.partner"

    x_nc_contact_uid = fields.Char(
        string="Nextcloud Contact UID",
        help="vCard UID in Nextcloud",
        copy=False,
    )
    x_carddav_etag = fields.Char(
        string="CardDAV ETag",
        help="ETag from Nextcloud for change detection",
        copy=False,
    )
    x_carddav_href = fields.Char(
        string="CardDAV HREF",
        help="Full CardDAV URL of this contact in Nextcloud",
        copy=False,
    )
    x_nc_contacts_config_id = fields.Many2one(
        "nextcloud.contacts.sync.config",
        string="Nextcloud Address Book",
        help="Nextcloud sync configuration for this contact",
        copy=False,
    )
    x_contact_sync_source = fields.Selection(
        [
            ("odoo", "Created in Odoo"),
            ("nextcloud", "Synced from Nextcloud"),
        ],
        string="Contact Sync Source",
        help="Origin of this contact",
        copy=False,
    )
    x_contact_last_sync = fields.Datetime(
        string="Last Contact Sync",
        help="Timestamp of last synchronization",
        copy=False,
    )

    def write(self, vals):
        """Mark contact as needing re-sync when relevant fields change."""
        result = super().write(vals)

        if self.env.context.get("skip_nc_contact_sync"):
            return result

        # Check if any sync-relevant fields changed
        if _SYNC_FIELDS & set(vals.keys()):
            for record in self:
                if (
                    record.x_nc_contact_uid
                    and record.x_nc_contacts_config_id
                    and record.x_contact_sync_source != "nextcloud"
                ):
                    # Clear ETag to mark as dirty — next cron/push will update NC
                    record.with_context(skip_nc_contact_sync=True).write({
                        "x_carddav_etag": False,
                    })

        return result

    def unlink(self):
        """Delete corresponding vCard from Nextcloud when contact is deleted."""
        if not self.env.context.get("skip_nc_contact_sync"):
            for record in self:
                if record.x_nc_contact_uid and record.x_nc_contacts_config_id:
                    try:
                        config = record.x_nc_contacts_config_id
                        if config.exists() and config.active:
                            config._carddav_delete_vcard(record.x_nc_contact_uid)
                    except Exception as e:
                        _logger.warning(
                            "Failed to delete vCard for partner %s: %s",
                            record.id, e,
                        )

        return super().unlink()
