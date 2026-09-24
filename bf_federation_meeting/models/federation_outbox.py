from odoo import fields, models


class FederationOutbox(models.Model):
    _inherit = "federation.outbox"

    kind = fields.Selection(
        selection_add=[("agenda.share", "Partage d'un ordre du jour"),
                       ("agenda.card", "Ordre du jour mis à jour"),
                       ("agenda.topic", "Sujet proposé"),
                       ("agenda.state", "État d'un ordre du jour"),
                       ("record.share", "Partage d'un compte rendu"),
                       ("record.card", "Compte rendu mis à jour"),
                       ("record.state", "État d'un compte rendu")],
        ondelete={"agenda.share": "cascade", "agenda.card": "cascade",
                  "agenda.topic": "cascade", "agenda.state": "cascade",
                  "record.share": "cascade", "record.card": "cascade",
                  "record.state": "cascade"})
