from odoo import fields, models


class BfNfcTap(models.Model):
    _inherit = "bf.nfc.tap"

    door = fields.Selection(
        selection_add=[("qr_public", "Code QR, sans compte")],
        ondelete={"qr_public": "cascade"},
    )
