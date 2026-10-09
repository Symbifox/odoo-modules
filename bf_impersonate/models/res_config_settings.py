from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_impersonate_notify = fields.Selection(
        [
            ("never", "Never (journal only)"),
            ("start", "At the start of each session"),
            ("start_end", "At the start, then a summary at the end"),
        ],
        string="Notify the person seen",
        config_parameter="bf_impersonate.notify",
        default="start",
    )
    bf_impersonate_duration_default = fields.Integer(
        string="Default duration (minutes)",
        config_parameter="bf_impersonate.duration_default",
        default=30,
    )
    bf_impersonate_duration_max = fields.Integer(
        string="Maximum duration (minutes)",
        config_parameter="bf_impersonate.duration_max",
        default=120,
    )
    bf_impersonate_allow_admin_targets = fields.Boolean(
        string="Allow seeing Symbifox as an administrator",
        config_parameter="bf_impersonate.allow_admin_targets",
    )

    @api.constrains("bf_impersonate_duration_default", "bf_impersonate_duration_max")
    def _check_bf_impersonate_durations(self):
        for settings in self:
            if settings.bf_impersonate_duration_max < 1:
                raise ValidationError(_("The maximum duration must be at least one minute."))
            if not 1 <= settings.bf_impersonate_duration_default <= settings.bf_impersonate_duration_max:
                raise ValidationError(_(
                    "The default duration must be between one minute and the "
                    "maximum duration."))
