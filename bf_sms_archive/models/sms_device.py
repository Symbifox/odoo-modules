import logging
import secrets

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class SmsArchiveDevice(models.Model):
    _name = "sms.archive.device"
    _description = "Android device allowed to push SMS/MMS/calls live"
    _order = "last_sync_at desc, id desc"

    name = fields.Char(
        string="Name",
        required=True,
        help="Human-readable name (e.g. \"Pixel 8\")",
    )
    owner_id = fields.Many2one(
        comodel_name="res.users",
        string="Owner",
        required=True,
        default=lambda self: self.env.user,
        index=True,
        help="Received SMS/calls will be assigned to this user.",
    )
    api_token = fields.Char(
        string="API token",
        required=True,
        copy=False,
        default=lambda self: secrets.token_urlsafe(32),
        groups="bf_sms_archive.group_sms_manager",
        help="Bearer token used by the Android app. Can be regenerated "
             "with the button.",
    )
    active = fields.Boolean(
        string="Active",
        default=True,
    )
    last_sync_at = fields.Datetime(
        string="Last sync",
        readonly=True,
    )
    last_sync_count = fields.Integer(
        string="Entries (last batch)",
        readonly=True,
    )
    total_received = fields.Integer(
        string="Total received (lifetime)",
        readonly=True,
        default=0,
    )
    note = fields.Text(
        string="Notes",
    )

    _sql_constraints = [
        (
            "token_unique",
            "UNIQUE(api_token)",
            "This token is already used by another device.",
        ),
    ]

    def action_regenerate_token(self):
        """Generate a new urlsafe token. Invalidates the previous one immediately."""
        # Même motif que la ligne : l'écriture se fait en sudo, le droit se
        # contrôle d'abord.
        self.check_access("write")
        for device in self:
            device.sudo().write({"api_token": secrets.token_urlsafe(32)})
        return True

    @api.model
    def _resolve(self, raw_token):
        """Lookup an active device by raw token. Returns recordset (possibly empty)."""
        if not raw_token or not isinstance(raw_token, str):
            return self.sudo().browse()
        device = self.sudo().search(
            [("api_token", "=", raw_token), ("active", "=", True)],
            limit=1,
        )
        # Le jeton ne vit que tant que son propriétaire est un
        # interne ACTIF. Archiver le compte ou le passer au portail le coupe,
        # vérifié à chaque usage (format du jeton inchangé).
        if device and (not device.owner_id.active or device.owner_id.share):
            return self.sudo().browse()
        return device
