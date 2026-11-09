# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).

from odoo import api, fields, models

# Valeur d'affichage d'un secret déjà enregistré. `default_get` et le `compute`
# la poussent dans le champ pour ne JAMAIS renvoyer le secret au navigateur ;
# l'`inverse` l'ignore, sinon un simple « Enregistrer » dans Paramètres la
# ré-encrypterait par-dessus le vrai secret OAuth.
MASQUE_SECRET = "••••••••"


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
        readonly=False,
        help="OAuth 2.0 Client Secret from Google Cloud Console. "
        "Stored encrypted (Fernet) in ir.config_parameter. A secret already "
        "stored shows as dots: it never travels back to the browser, and "
        "saving the dots changes nothing.",
    )
    google_oauth_redirect_uri = fields.Char(
        string="Redirect URI to Register",
        compute="_compute_google_oauth_redirect_uri",
        readonly=True,
        help="Copy this URL into your Google OAuth client's "
        "Authorized redirect URIs.",
    )

    @api.model
    def default_get(self, fields_list):
        """Alimente la façade du secret, que le formulaire ne calcule jamais.

        🔴 La page Paramètres est bâtie par `onchange`, et la couche web sème à
        `False` tout champ de la vue que `default_get` ne rend pas
        (`addons/web/models/models.py`). Le `compute` ci-dessous ne tournait
        donc JAMAIS à l'affichage : le champ paraissait vide alors qu'un secret
        était en place, et plus rien ne disait à l'administrateur qu'OAuth
        était configuré. Le secret, lui, n'a jamais été en danger —
        l'`inverse` ignore le vide. Même cause que le défaut voisin de la rétention des courriels.

        Les champs voisins tiennent par `config_parameter` ou par `get_values`
        (le calendrier par défaut et le cron), que `default_get` appelle.
        """
        res = super().default_get(fields_list)
        if "google_oauth_client_secret" in fields_list:
            res["google_oauth_client_secret"] = (
                MASQUE_SECRET if self._google_oauth_secret_pose() else False
            )
        return res

    @api.model
    def _google_oauth_secret_pose(self):
        """Vrai si un secret chiffré est en base. Source du `compute` aussi."""
        return bool(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("calendar_nextcloud_sync.google_oauth_client_secret_enc", "")
        )

    def _compute_google_oauth_client_secret(self):
        """Ne rend JAMAIS le secret au navigateur : un masque, ou rien.

        ⚠️ Le clair ne sert qu'au contrôleur OAuth et à `res.users`, qui
        déchiffrent le paramètre eux-mêmes ; cet écran n'en a pas besoin, et le
        renvoyer au navigateur le déposait dans le DOM de la page Paramètres.
        Même source que `default_get`, à dessein.
        """
        pose = self._google_oauth_secret_pose()
        for rec in self:
            rec.google_oauth_client_secret = MASQUE_SECRET if pose else False

    def _inverse_google_oauth_client_secret(self):
        """Chiffre et range la valeur saisie. Ignore le masque et le vide.

        Password-style field: the Settings form never echoes the stored secret
        back to the browser — it shows a mask — so neither an empty value nor
        the mask means "clear it". Both mean "left unchanged". Blanking here
        would silently wipe a working Client Secret every time the Settings
        page is saved, from any module's tab. To intentionally clear the OAuth
        client credentials, edit the ir.config_parameter directly.
        """
        ICP = self.env["ir.config_parameter"].sudo()
        Config = self.env["nextcloud.calendar.sync.config"]
        for rec in self:
            saisi = (rec.google_oauth_client_secret or "").strip()
            if not saisi or saisi == MASQUE_SECRET:
                continue
            ICP.set_param(
                "calendar_nextcloud_sync.google_oauth_client_secret_enc",
                Config._encrypt_value(saisi) or "",
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
        try:
            cal_id = int(
                self.env["ir.config_parameter"]
                .sudo()
                .get_param("calendar_nextcloud_sync.default_calendar_id", "0")
                or 0
            )
        except ValueError:
            cal_id = 0
        if cal_id:
            cal = self.env["nextcloud.calendar.sync.config"].browse(cal_id)
            # An archived default routes nothing: show none rather than a
            # calendar that new events no longer go to.
            if cal.exists() and cal.active:
                res["nc_sync_default_calendar_id"] = cal_id
        cron = self.env.ref(
            "calendar_nextcloud_sync.cron_nextcloud_calendar_sync",
            raise_if_not_found=False,
        )
        if cron:
            res["nc_sync_cron_enabled"] = cron.active
            res["nc_sync_cron_interval"] = cron.interval_number
        return res
