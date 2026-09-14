from odoo import fields, models


class FederationOutbox(models.Model):
    _inherit = "federation.outbox"

    kind = fields.Selection(
        selection_add=[("outreach.share", "Partage d'un suivi de démarchage"),
                       ("outreach.card", "Suivi de démarchage mis à jour"),
                       ("outreach.exclude", "Cible écartée par le client")],
        ondelete={"outreach.share": "cascade", "outreach.card": "cascade",
                  "outreach.exclude": "cascade"})
