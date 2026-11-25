from odoo import fields, models


class FederationOutbox(models.Model):
    _inherit = "federation.outbox"

    kind = fields.Selection(
        selection_add=[("process.share", "Process map shared"),
                       ("process.card", "Process map updated")],
        ondelete={"process.share": "cascade", "process.card": "cascade"})
