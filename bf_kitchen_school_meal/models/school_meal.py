import logging
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.bf_kitchen.models.kitchen_consumption import STOCK_TYPES

_logger = logging.getLogger(__name__)
MANAGER = "bf_kitchen.group_kitchen_manager"


class StockPicking(models.Model):
    _inherit = "stock.picking"

    bf_kitchen_source = fields.Selection(selection_add=[("school_meal", "School meals")],
                                         ondelete={"school_meal": "set null"})


class MealItem(models.Model):
    _inherit = "bf.school.meal.item"

    bf_kitchen_dish_id = fields.Many2one(
        "product.product", "Kitchen dish", index="btree_not_null",
        domain="[('bf_kitchen_type', '=', 'dish'), ('company_id', 'in', (company_id, False))]",
        help="The dish of the kitchen this meal is: its recipe gives the cost and the allergens, and "
             "the meals served take its ingredients out of the stock.")
    bf_kitchen_dish_date = fields.Date(
        "Dish linked on", readonly=True, copy=False,
        help="The meals served from this day on leave the stock: those before were not counted with the dish.")
    bf_kitchen_allergen_ids = fields.Many2many(
        "bf.food.allergen", compute="_compute_bf_kitchen", string="Allergens of the dish",
        help="Carried up from the recipe's ingredients; they count with the meal's own allergens.")
    bf_kitchen_cost = fields.Monetary("Plate cost", compute="_compute_bf_kitchen", groups=MANAGER)
    bf_kitchen_food_cost_pct = fields.Float("Food cost (%)", compute="_compute_bf_kitchen", groups=MANAGER,
                                            digits=(16, 1))

    @api.depends("bf_kitchen_dish_id", "price")
    def _compute_bf_kitchen(self):
        for item in self:
            dish = item.sudo().bf_kitchen_dish_id
            item.bf_kitchen_allergen_ids = dish.product_tmpl_id.bf_allergen_ids
            cost = 0.0
            if dish and item.company_id:
                bom = self.env["mrp.bom"].sudo()._bom_find(dish, company_id=item.company_id.id,
                                                          bom_type="phantom").get(dish)
                cost = bom.with_company(item.company_id).bf_cost_portion if bom else 0.0
            item.bf_kitchen_cost = cost
            item.bf_kitchen_food_cost_pct = 100.0 * cost / item.price if item.price else 0.0

    @api.constrains("bf_kitchen_dish_id", "school_id")
    def _check_bf_kitchen_dish(self):
        for item in self.sudo():
            dish = item.bf_kitchen_dish_id
            if not dish:
                continue
            if dish.product_tmpl_id.bf_kitchen_type != "dish":
                raise ValidationError(_("A meal points to a dish of the kitchen."))
            if dish.company_id and dish.company_id != item.company_id:
                raise ValidationError(_("The dish of a meal belongs to the school's company."))

    @api.model_create_multi
    def create(self, vals_list):
        items = super().create(vals_list)
        items.filtered("bf_kitchen_dish_id").sudo().bf_kitchen_dish_date = fields.Date.context_today(self)
        return items

    def write(self, vals):
        res = super().write(vals)
        if vals.get("bf_kitchen_dish_id"):
            self.sudo().write({"bf_kitchen_dish_date": fields.Date.context_today(self)})
        return res

    def _school_allergens(self):
        """The meal's allergens and its dish's: a meal is refused to a student allergic to either."""
        return super()._school_allergens() | self.sudo().bf_kitchen_dish_id.product_tmpl_id.bf_allergen_ids


class MealOrder(models.Model):
    _inherit = "bf.school.meal.order"

    bf_kitchen_picking_id = fields.Many2one(
        "stock.picking", "Kitchen consumption", readonly=True, copy=False, index="btree_not_null",
        help="The transfer that took this meal's ingredients out of the stock, at the cancellation hour.")

    def write(self, vals):
        if not self.env.su and "bf_kitchen_picking_id" in vals:
            raise AccessError(_("The kitchen consumption of a meal is set by the kitchen itself."))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            if any(v.get("bf_kitchen_picking_id") for v in vals_list):
                raise AccessError(_("The kitchen consumption of a meal is set by the kitchen itself."))
            # A `default_*` of the context would set it after this check: the meal would never leave.
            self = self.with_context({k: v for k, v in self.env.context.items()
                                      if k != "default_bf_kitchen_picking_id"})
        return super().create(vals_list)

    @api.model
    def _cron_bf_kitchen_consume_meals(self):
        """Once a family can no longer cancel (the school's hour, in its time zone), the meals still
        ordered for the day leave the stock: one Kitchen consumption per school and day. Each meal is
        taken once; a meal added after the hour is taken at the next pass. Days before the bridge was
        installed are left alone."""
        since = self.env["ir.config_parameter"].sudo().get_param("bf_kitchen_school_meal.since")
        domain = [("state", "=", "ordered"), ("bf_kitchen_picking_id", "=", False),
                  ("item_id.bf_kitchen_dish_id", "!=", False),
                  ("date", "<=", fields.Date.today() + timedelta(days=1))]
        if since:
            domain.append(("date", ">=", since))
        # A meal ordered before its dish was linked was not counted with the dish: leaving now, all at
        # once, it would be counted twice against a count made in between.
        orders = self.sudo().search(domain).filtered(
            lambda o: o.date >= (o.item_id.bf_kitchen_dish_date or o.date))
        Picking = self.env["stock.picking"].sudo()
        done = self.browse()
        for day in orders.day_id:
            if day.closed or day._is_cancellable():
                continue
            day_orders = orders.filtered(lambda o: o.day_id == day)
            company = day.company_id
            warehouse = company._bf_kitchen_warehouse()
            if not warehouse:
                day._bf_kitchen_issue(_("the company %s has no warehouse", company.name))
                continue
            qty = defaultdict(float)
            for order in day_orders:
                qty[order.item_id.bf_kitchen_dish_id] += 1.0
            try:
                with self.env.cr.savepoint():
                    picking = Picking._bf_kitchen_consume(
                        warehouse, [(dish, n, dish.uom_id) for dish, n in qty.items()], "school_meal",
                        "%s %s" % (day.school_id.name, fields.Date.to_string(day.date)), event_date=day.date)
                    day_orders.sudo().write({"bf_kitchen_picking_id": picking.id})
                    done |= day_orders
                if day.bf_kitchen_issue:
                    day.sudo().bf_kitchen_issue = False
            except (UserError, ValidationError) as error:
                day._bf_kitchen_issue(error.args[0] if error.args else str(error))
        return done


class MealDay(models.Model):
    _inherit = "bf.school.meal.day"

    bf_kitchen_issue = fields.Char(
        "Kitchen issue", readonly=True, copy=False,
        help="Why the meals of this day have not left the kitchen stock yet. The hourly pass tries again.")

    def _bf_kitchen_issue(self, reason):
        """Say once, on the school, why the meals of a day did not leave the stock; the next passes
        retry quietly until it works."""
        self.ensure_one()
        _logger.warning("School meals of %s, %s did not leave the stock: %s", self.school_id.name, self.date, reason)
        if self.bf_kitchen_issue:
            return
        self.sudo().bf_kitchen_issue = reason[:500]
        self.school_id.sudo()._message_log(body=_(
            "The meals of %(day)s did not leave the kitchen stock: %(reason)s. They will be taken out "
            "as soon as it is fixed.", day=fields.Date.to_string(self.date), reason=reason))


class KitchenVariance(models.TransientModel):
    _inherit = "bf.kitchen.variance"

    def _bf_variance_adjustments(self, date_from, date_to, moves):
        """A meal cancelled (or its day closed) after it left the stock: the food was cooked and
        nobody ate it. Nothing goes back in stock; here its ingredients move from consumption to
        losses."""
        rows = super()._bf_variance_adjustments(date_from, date_to, moves)
        pickings = moves.picking_id.filtered(lambda p: p.bf_kitchen_source == "school_meal")
        if not pickings:
            return rows
        orders = self.env["bf.school.meal.order"].sudo().search(
            [("bf_kitchen_picking_id", "in", pickings.ids), ("state", "!=", "ordered")])
        source = dict(self.env["stock.picking"]._fields["bf_kitchen_source"]._description_selection(
            self.env))["school_meal"]
        reason = _("Meal cancelled after the cut-off")
        Bom = self.env["mrp.bom"].sudo()
        for order in orders:
            picking = order.bf_kitchen_picking_id
            for product, qty in self._bf_kitchen_meal_leaves(Bom, picking, order.item_id.bf_kitchen_dish_id):
                rows.append(((product.id, "consumption", False, source), -qty))
                rows.append(((product.id, "loss", reason, False), qty))
        return rows

    @staticmethod
    def _bf_kitchen_meal_leaves(Bom, picking, dish):
        """What one portion of the dish took out with this transfer: the kitchen items of its recipe,
        exploded as the stock exploded it, in their stock unit."""
        if not dish:
            return []
        bom = Bom._bom_find(dish, company_id=picking.company_id.id, bom_type="phantom").get(dish)
        if not bom:
            return []
        factor = dish.uom_id._compute_quantity(1.0, bom.product_uom_id) / bom.product_qty
        _boms, lines = bom.explode(dish, factor)
        taken = set(picking.move_ids.product_id.ids)
        leaves = []
        for line, data in lines:
            product = line.product_id
            if product.product_tmpl_id.bf_kitchen_type not in STOCK_TYPES or product.id not in taken:
                continue
            leaves.append((product, line.product_uom_id._compute_quantity(data["qty"], product.uom_id, round=False)))
        return leaves
