from odoo import fields, models


class FederationOutbox(models.Model):
    _inherit = "federation.outbox"

    kind = fields.Selection(
        selection_add=[("breach.share", "Avis de violation"),
                       ("breach.ack", "Accusé d'un avis de violation")],
        ondelete={"breach.share": "cascade", "breach.ack": "cascade"})
