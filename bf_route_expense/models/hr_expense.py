from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class HrExpense(models.Model):
    _inherit = "hr.expense"

    bf_route_day_id = fields.Many2one(
        "bf.route.day", string="Route day", readonly=True, copy=False, index="btree_not_null",
        ondelete="set null", help="The route day whose kilometres this expense claims.")

    # ⚠️ An employee may write their own expenses over RPC: the link to a route day is set by
    # the route module only (sudo), or an employee could tie any draft to a day and have it
    # replaced, or counted in the yearly kilometres.
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(vals.get("bf_route_day_id") for vals in vals_list):
            raise AccessError(_("Only the route module links an expense to a route day."))
        return super().create(vals_list)

    def write(self, vals):
        if "bf_route_day_id" in vals and not self.env.su:
            raise AccessError(_("Only the route module links an expense to a route day."))
        return super().write(vals)
