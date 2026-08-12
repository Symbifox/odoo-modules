import logging
import secrets

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class SmsArchiveDevice(models.Model):
    _name = "sms.archive.device"
    _description = "Appareil Android autorisé à pousser des SMS/MMS/appels en direct"
    _order = "last_sync_at desc, id desc"

    name = fields.Char(
        string="Nom",
        required=True,
        help="Identification humaine (ex. « Pixel 8 »)",
    )
    owner_id = fields.Many2one(
        comodel_name="res.users",
        string="Propriétaire",
        required=True,
        default=lambda self: self.env.user,
        index=True,
        help="Les SMS/appels reçus seront imputés à cet utilisateur.",
    )
    api_token = fields.Char(
        string="Token API",
        required=True,
        copy=False,
        default=lambda self: secrets.token_urlsafe(32),
        groups="bf_sms_archive.group_sms_manager",
        help="Bearer token utilisé par l'app Android. Régénérable via le bouton.",
    )
    active = fields.Boolean(
        string="Actif",
        default=True,
    )
    last_sync_at = fields.Datetime(
        string="Dernière synchronisation",
        readonly=True,
    )
    last_sync_count = fields.Integer(
        string="Entries (dernier batch)",
        readonly=True,
    )
    total_received = fields.Integer(
        string="Total reçus (lifetime)",
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
            "Ce token est déjà utilisé par un autre appareil.",
        ),
    ]

    def action_regenerate_token(self):
        """Generate a new urlsafe token. Invalidates the previous one immediately."""
        for device in self:
            device.sudo().write({"api_token": secrets.token_urlsafe(32)})
        return True

    @api.model
    def _resolve(self, raw_token):
        """Lookup an active device by raw token. Returns recordset (possibly empty)."""
        if not raw_token or not isinstance(raw_token, str):
            return self.sudo().browse()
        return self.sudo().search(
            [("api_token", "=", raw_token), ("active", "=", True)],
            limit=1,
        )
