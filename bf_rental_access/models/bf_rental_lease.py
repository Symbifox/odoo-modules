"""Le bail prévient les accès quand il change, sans jamais bloquer sa saisie."""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

WATCHED = {"tenant_ids", "date_start", "unit_id", "building_id", "active",
           "rent_due_until"}


class BfRentalLease(models.Model):
    _inherit = "bf.rental.lease"

    @api.model_create_multi
    def create(self, vals_list):
        leases = super().create(vals_list)
        leases._bf_access_resync()
        return leases

    def write(self, vals):
        before = self.building_id
        res = super().write(vals)
        if WATCHED & set(vals):
            self._bf_access_resync(extra_buildings=before)
        return res

    def _bf_access_resync(self, extra_buildings=None):
        """Rafraîchit les résidents des immeubles touchés, tout de suite.

        Le cron de la nuit fait la même chose : une erreur ici est consignée
        et n'empêche jamais d'enregistrer le bail.
        """
        buildings = self.building_id | (extra_buildings or self.env["bf.property.building"])
        if not buildings:
            return
        try:
            with self.env.cr.savepoint():
                self.env["bf.property.access.resident"].sudo()._bf_sync(
                    buildings=buildings.sudo()
                )
        except Exception:  # noqa: BLE001 — le bail passe avant les accès
            _logger.warning("Accès : resynchronisation après le bail %s en échec",
                            self.ids, exc_info=True)


class BfRentalAbandonment(models.Model):
    _inherit = "bf.rental.abandonment"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.lease_id._bf_access_resync()
        return records
