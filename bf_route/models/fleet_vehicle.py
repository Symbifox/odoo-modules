from odoo import fields, models


class FleetVehicle(models.Model):
    _inherit = "fleet.vehicle"

    bf_route_heavy = fields.Boolean(
        string="Heavy vehicle",
        help="Gross vehicle weight rating of 4,500 kg or more. The worker confirms the pre-trip "
             "inspection before the route day can start (Quebec heavy vehicle rules).")
