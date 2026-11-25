from odoo import fields, models


class FederationOutbox(models.Model):
    _inherit = "federation.outbox"

    kind = fields.Selection(
        selection_add=[("outreach.share", "Prospecting report shared"),
                       ("outreach.card", "Prospecting report updated"),
                       ("outreach.exclude", "Target set aside by the client")],
        ondelete={"outreach.share": "cascade", "outreach.card": "cascade",
                  "outreach.exclude": "cascade"})
