from odoo import fields, models


class FederationOutbox(models.Model):
    _inherit = "federation.outbox"

    kind = fields.Selection(
        selection_add=[("gantt.share", "Schedule shared"),
                       ("gantt.card", "Schedule updated")],
        ondelete={"gantt.share": "cascade", "gantt.card": "cascade"})
