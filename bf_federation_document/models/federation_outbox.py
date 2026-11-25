from odoo import fields, models


class FederationOutbox(models.Model):
    _inherit = "federation.outbox"

    # Le socle ne connaît pas les genres des satellites : chacun ajoute les siens.
    kind = fields.Selection(
        selection_add=[("document.share", "Deliverable handed over"),
                       ("document.card", "New version of a deliverable"),
                       ("document.ack", "Read receipt")],
        ondelete={"document.share": "cascade", "document.card": "cascade",
                  "document.ack": "cascade"})
