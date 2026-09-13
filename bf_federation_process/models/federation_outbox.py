from odoo import fields, models


class FederationOutbox(models.Model):
    _inherit = "federation.outbox"

    kind = fields.Selection(
        selection_add=[("process.share", "Partage d'une cartographie"),
                       ("process.card", "Cartographie mise à jour")],
        ondelete={"process.share": "cascade", "process.card": "cascade"})
