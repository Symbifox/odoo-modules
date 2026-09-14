from odoo import fields, models


class FederationOutbox(models.Model):
    _inherit = "federation.outbox"

    kind = fields.Selection(
        selection_add=[("gantt.share", "Partage d'un échéancier"),
                       ("gantt.card", "Échéancier mis à jour")],
        ondelete={"gantt.share": "cascade", "gantt.card": "cascade"})
