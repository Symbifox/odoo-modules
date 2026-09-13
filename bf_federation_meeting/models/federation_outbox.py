from odoo import fields, models


class FederationOutbox(models.Model):
    _inherit = "federation.outbox"

    kind = fields.Selection(
        selection_add=[("agenda.share", "Partage d'un ordre du jour"),
                       ("agenda.card", "Ordre du jour mis à jour"),
                       ("agenda.topic", "Sujet proposé")],
        ondelete={"agenda.share": "cascade", "agenda.card": "cascade",
                  "agenda.topic": "cascade"})
