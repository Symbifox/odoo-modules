# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).

from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    nc_sync_cron_enabled = fields.Boolean(
        string="Enable Calendar Catch-up Sync",
        config_parameter="calendar_nextcloud_sync.cron_enabled",
    )
    nc_sync_cron_interval = fields.Integer(
        string="Sync Interval (minutes)",
        config_parameter="calendar_nextcloud_sync.cron_interval",
        default=15,
    )
    nc_sync_webhook_url = fields.Char(
        string="n8n Webhook URL",
        config_parameter="calendar_nextcloud_sync.webhook_url",
        help="Default webhook URL applied to new Nextcloud calendar configurations",
    )
    nc_sync_webhook_secret = fields.Char(
        string="Webhook Secret",
        config_parameter="calendar_nextcloud_sync.webhook_secret",
        help="Default webhook secret applied to new Nextcloud calendar configurations",
    )
    nc_sync_default_calendar_id = fields.Many2one(
        "nextcloud.calendar.sync.config",
        string="Default Calendar",
        help="New events created in Odoo will automatically sync to this calendar",
    )

    # Google Calendar OAuth 2.0 client credentials (shared across all users)
    google_oauth_client_id = fields.Char(
        string="Google OAuth Client ID",
        config_parameter="calendar_nextcloud_sync.google_oauth_client_id",
        help="OAuth 2.0 Client ID from Google Cloud Console. Type: Web "
        "application. Add redirect URI <base_url>/bf_calendar/google/oauth/callback.",
    )
    google_oauth_client_secret = fields.Char(
        string="Google OAuth Client Secret",
        compute="_compute_google_oauth_client_secret",
        inverse="_inverse_google_oauth_client_secret",
        help="OAuth 2.0 Client Secret from Google Cloud Console. "
        "Stored encrypted (Fernet) in ir.config_parameter.",
    )
    google_oauth_redirect_uri = fields.Char(
        string="Redirect URI to Register",
        compute="_compute_google_oauth_redirect_uri",
        readonly=True,
        help="Copy this URL into your Google OAuth client's "
        "Authorized redirect URIs.",
    )

    @api.depends_context("uid")
    def _compute_google_oauth_client_secret(self):
        ICP = self.env["ir.config_parameter"].sudo()
        enc = ICP.get_param(
            "calendar_nextcloud_sync.google_oauth_client_secret_enc", ""
        )
        Config = self.env["nextcloud.calendar.sync.config"]
        for rec in self:
            rec.google_oauth_client_secret = (
                Config._decrypt_value(enc) if enc else False
            )

    def _inverse_google_oauth_client_secret(self):
        ICP = self.env["ir.config_parameter"].sudo()
        Config = self.env["nextcloud.calendar.sync.config"]
        for rec in self:
            # Password-style field: the Settings form does not echo the stored
            # secret back to the browser, so an empty value on save means
            # "left unchanged", NOT "clear it". Blanking here would silently wipe
            # a working Client Secret every time the Settings page is saved.
            # Only write when a non-empty value is actually submitted. To
            # intentionally clear the OAuth client credentials, edit the
            # ir.config_parameter directly.
            if rec.google_oauth_client_secret:
                enc = Config._encrypt_value(rec.google_oauth_client_secret)
                ICP.set_param(
                    "calendar_nextcloud_sync.google_oauth_client_secret_enc",
                    enc or "",
                )

    def _compute_google_oauth_redirect_uri(self):
        base = self.env["ir.config_parameter"].sudo().get_param(
            "web.base.url", ""
        ).rstrip("/")
        for rec in self:
            rec.google_oauth_redirect_uri = (
                base + "/bf_calendar/google/oauth/callback" if base else ""
            )

    def set_values(self):
        res = super().set_values()
        self.env["ir.config_parameter"].sudo().set_param(
            "calendar_nextcloud_sync.default_calendar_id",
            self.nc_sync_default_calendar_id.id or 0,
        )
        cron = self.env.ref(
            "calendar_nextcloud_sync.cron_nextcloud_calendar_sync",
            raise_if_not_found=False,
        )
        if cron:
            cron.sudo().write({
                "active": self.nc_sync_cron_enabled,
                "interval_number": self.nc_sync_cron_interval or 15,
            })
        return res

    @api.model
    def get_values(self):
        res = super().get_values()
        cal_id = int(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("calendar_nextcloud_sync.default_calendar_id", "0")
        )
        if cal_id:
            cal = self.env["nextcloud.calendar.sync.config"].browse(cal_id)
            if cal.exists():
                res["nc_sync_default_calendar_id"] = cal_id
        cron = self.env.ref(
            "calendar_nextcloud_sync.cron_nextcloud_calendar_sync",
            raise_if_not_found=False,
        )
        if cron:
            res["nc_sync_cron_enabled"] = cron.active
            res["nc_sync_cron_interval"] = cron.interval_number
        return res
