from odoo import fields, models


class FederationOutbox(models.Model):
    _inherit = "federation.outbox"

    # Le socle ne connaît pas les genres des satellites : chacun ajoute les siens.
    kind = fields.Selection(
        selection_add=[("document.share", "Remise d'un livrable"),
                       ("document.card", "Nouvelle version d'un livrable"),
                       ("document.ack", "Accusé de réception")],
        ondelete={"document.share": "cascade", "document.card": "cascade",
                  "document.ack": "cascade"})
