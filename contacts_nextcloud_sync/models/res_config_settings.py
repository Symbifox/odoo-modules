from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    nc_contacts_sync_cron_enabled = fields.Boolean(
        string="Enable Nextcloud Contacts Sync Cron",
        config_parameter="contacts_nextcloud_sync.cron_enabled",
    )
    nc_contacts_sync_cron_interval = fields.Integer(
        string="Sync Interval (minutes)",
        config_parameter="contacts_nextcloud_sync.cron_interval",
        default=15,
    )
    nc_contacts_sync_default_config_id = fields.Many2one(
        "nextcloud.contacts.sync.config",
        string="Default Address Book",
        help="Default Nextcloud address book configuration",
    )

    def set_values(self):
        res = super().set_values()
        self.env["ir.config_parameter"].sudo().set_param(
            "contacts_nextcloud_sync.default_config_id",
            self.nc_contacts_sync_default_config_id.id or 0,
        )
        cron = self.env.ref(
            "contacts_nextcloud_sync.cron_nextcloud_contacts_sync",
            raise_if_not_found=False,
        )
        if cron:
            cron.sudo().write({
                "active": self.nc_contacts_sync_cron_enabled,
                "interval_number": self.nc_contacts_sync_cron_interval or 15,
            })
        return res

    @api.model
    def get_values(self):
        res = super().get_values()
        config_id = int(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("contacts_nextcloud_sync.default_config_id", "0")
        )
        if config_id:
            config = self.env["nextcloud.contacts.sync.config"].browse(config_id)
            if config.exists():
                res["nc_contacts_sync_default_config_id"] = config_id
        cron = self.env.ref(
            "contacts_nextcloud_sync.cron_nextcloud_contacts_sync",
            raise_if_not_found=False,
        )
        if cron:
            res["nc_contacts_sync_cron_enabled"] = cron.active
            res["nc_contacts_sync_cron_interval"] = cron.interval_number
        return res
