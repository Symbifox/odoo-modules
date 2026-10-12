from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class FleetVehicle(models.Model):
    _inherit = "fleet.vehicle"

    bf_route_personal = fields.Boolean(
        string="Employee's own vehicle",
        help="The worker's own car. Each route day finished with it gives the worker a draft "
             "expense for its kilometres, at the per-km rate. A company vehicle gives none.")

    # ⚠️ A driver may write their own vehicle (Fleet rules): ticking a company truck as their
    # own car would turn its kilometres into expenses for them. Fleet managers only.
    def _bf_route_check_personal(self):
        if not (self.env.su or self.env.user.has_group("fleet.fleet_group_manager")):
            raise AccessError(_("Only a fleet manager says whether a vehicle is an employee's own."))

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get("bf_route_personal") for vals in vals_list):
            self._bf_route_check_personal()
        return super().create(vals_list)

    def write(self, vals):
        if "bf_route_personal" in vals and any(v.bf_route_personal != bool(vals["bf_route_personal"])
                                               for v in self):
            self._bf_route_check_personal()
        return super().write(vals)
