from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    bf_kitchen_target_pct = fields.Float(
        "Target food cost (%)", default=30.0,
        help="Default target for a new recipe: plate cost over selling price.")
    bf_kitchen_price_rounding = fields.Selection(
        [("none", "No rounding"), ("0.05", "To the next 0.05"), ("0.25", "To the next 0.25"),
         ("1", "To the next dollar"), ("0.95", "Ending in .95")],
        "Suggested price rounding", default="0.25", required=True)
    bf_kitchen_cost_max_age = fields.Integer(
        "Cost too old after (days)", default=60,
        help="A recipe is flagged when one of its ingredient costs is older than this.")
    bf_kitchen_alert_points = fields.Float(
        "Alert on a rise of (points)", default=2.0,
        help="A dish already above its target raises a new alert only when a bill pushes its "
             "food cost up by at least this many points.")
    bf_kitchen_manager_id = fields.Many2one(
        "res.users", "Kitchen manager",
        help="Receives the plate cost alerts. Empty: the user who validates the bill.")
    bf_kitchen_tax_nonrecoverable_pct = fields.Float(
        "Non-recoverable share of taxes (%)", default=0.0,
        help="Share of the taxes on a vendor bill that the company cannot claim back and that "
             "belongs in the ingredient cost (a charity refunded in part, exempt supplies). "
             "0: the cost is net of taxes.")
    bf_kitchen_receive_without_po = fields.Boolean(
        "Receive from the vendor bill", default=True,
        help="Show the Received button on draft vendor bills that are not linked to a purchase "
             "order, so ingredients enter the stock on the day they arrive.")
    bf_kitchen_warehouse_id = fields.Many2one(
        "stock.warehouse", "Kitchen warehouse", check_company=True,
        help="The warehouse the kitchen's sales leave from when nothing else says it (an invoice with "
             "no sales order, the school meals) and where the Received button brings the ingredients. "
             "Empty: the company's first warehouse.")
    bf_kitchen_count_frequency = fields.Selection(
        [("weekly", "Every week"), ("biweekly", "Every two weeks"), ("monthly", "Every month")],
        "Kitchen count reminder",
        help="Prepares a count once the last finished count is older than this, with an activity for "
             "the kitchen manager when one is set. Empty: no reminder.")

    def _bf_kitchen_warehouse(self):
        """The kitchen's warehouse of this company: the one set, else its first."""
        self.ensure_one()
        company = self.sudo()
        return company.bf_kitchen_warehouse_id or self.env["stock.warehouse"].sudo().search(
            [("company_id", "=", company.id)], limit=1)
