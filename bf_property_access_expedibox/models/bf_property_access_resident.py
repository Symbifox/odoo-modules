"""Le passage de nuit du socle relit aussi les casiers connectés."""
from odoo import api, models


class BfPropertyAccessResident(models.Model):
    _inherit = "bf.property.access.resident"

    @api.model
    def _cron_sync(self):
        """Après la synchro et l'envoi des ordres, confronter chaque casier.

        Dans la même passe, et après elle : les droits créés à l'instant sont
        déjà là, donc aucun d'eux ne passe pour un destinataire orphelin.
        """
        res = super()._cron_sync()
        self.env["bf.property.access.connector"].sudo()._expedibox_reconcile()
        return res
