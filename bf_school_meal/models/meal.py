from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class School(models.Model):
    _inherit = "bf.school"

    meal_order_days = fields.Integer(
        "Order ahead (days)", default=2,
        help="A family orders a meal at the latest this many days before it is served.")
    meal_cancel_hour = fields.Float(
        "Cancel until (hour, same day)", default=8.0,
        help="A family cancels a meal until this hour on the day it is served; it is credited.")
    meal_payment_modes = fields.Selection(
        [("prepaid", "Prepaid balance"), ("monthly", "Monthly invoice"),
         ("choice", "The family chooses")], "Meal payment", default="choice", required=True)

    def _school_tz(self):
        self.ensure_one()
        return pytz.timezone(self.company_id.partner_id.tz or "America/Toronto")

    def _school_now(self):
        return datetime.now(pytz.utc).astimezone(self._school_tz())


class ResPartner(models.Model):
    _inherit = "res.partner"

    # Every staff member sees them, like the health alert: the lunch supervisors must know.
    meal_allergen_ids = fields.Many2many(
        "bf.school.allergen", "bf_school_student_allergen_rel", "partner_id", "allergen_id",
        "Food allergies", help="A meal containing one of these cannot be ordered for this student.")


class Allergen(models.Model):
    """The priority food allergens (Health Canada) and any other the school adds."""

    _name = "bf.school.allergen"
    _description = "Food allergen"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)


class MealItem(models.Model):
    _name = "bf.school.meal.item"
    _description = "Meal on the menu"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    school_id = fields.Many2one("bf.school", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    price = fields.Monetary(required=True)
    description = fields.Text()
    allergen_ids = fields.Many2many("bf.school.allergen", string="Contains")
    active = fields.Boolean(default=True)

    @api.constrains("price")
    def _check_price(self):
        if any(item.price < 0 for item in self):
            raise ValidationError(_("A meal has a price of zero or more."))


class MealDay(models.Model):
    """What the caterer serves on one school day."""

    _name = "bf.school.meal.day"
    _description = "Menu of the day"
    _order = "date"
    _rec_name = "date"

    school_id = fields.Many2one("bf.school", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True)
    date = fields.Date(required=True, index=True)
    item_ids = fields.Many2many("bf.school.meal.item", string="Meals offered",
                                domain="[('school_id', '=', school_id)]")
    closed = fields.Boolean(readonly=True, help="Closed after the fact (storm, closure): every order is credited.")
    closed_reason = fields.Char("Reason", readonly=True)
    order_ids = fields.One2many("bf.school.meal.order", "day_id", "Orders")
    order_count = fields.Integer(compute="_compute_order_count")

    _sql_constraints = [("one_per_date", "UNIQUE(school_id, date)", "A school has one menu per day.")]

    @api.depends("date", "item_ids.name", "closed")
    @api.depends_context("lang")
    def _compute_display_name(self):
        # The calendar of menus shows the meals, not the date it already sits on (QA, 2026-09-27).
        for day in self:
            names = ", ".join(day.item_ids.mapped("name"))
            day.display_name = (_("Closed") if day.closed else names) or fields.Date.to_string(day.date) or ""

    @api.depends("order_ids.state")
    def _compute_order_count(self):
        for day in self:
            day.order_count = len(day.order_ids.filtered(lambda o: o.state == "ordered"))

    @api.constrains("item_ids", "school_id")
    def _check_items(self):
        for day in self:
            if day.item_ids.school_id - day.school_id:
                raise ValidationError(_("A menu offers the meals of its own school."))

    def action_close(self, reason=None):
        """Storm, closure, unplanned day off: the orders are credited, nothing is billed."""
        for day in self:
            day.write({"closed": True, "closed_reason": reason or _("School closed")})
            day.order_ids.filtered(lambda o: o.state == "ordered")._school_release("credited")
        return True

    def _is_orderable(self, now=None):
        """A family still orders for this day (the deadline is in the school's time zone)."""
        self.ensure_one()
        now = now or self.school_id._school_now()
        return not self.closed and now.date() <= self.date - timedelta(days=self.school_id.meal_order_days)

    def _is_cancellable(self, now=None):
        self.ensure_one()
        school = self.school_id
        now = now or school._school_now()
        hours = int(school.meal_cancel_hour)
        minutes = int(round((school.meal_cancel_hour - hours) * 60))
        limit = school._school_tz().localize(datetime.combine(self.date, time(hours, minutes)))
        return not self.closed and now < limit


class MealOrder(models.Model):
    _name = "bf.school.meal.order"
    _description = "Meal ordered"
    _order = "date desc, id desc"
    _rec_names_search = ["student_id", "item_id"]

    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="cascade", index=True,
                                 domain=[("is_student", "=", True)])
    day_id = fields.Many2one("bf.school.meal.day", "Day", required=True, ondelete="restrict", index=True)
    date = fields.Date(related="day_id.date", store=True, index=True)
    school_id = fields.Many2one(related="day_id.school_id", store=True)
    company_id = fields.Many2one(related="day_id.company_id", store=True)
    item_id = fields.Many2one("bf.school.meal.item", "Meal", required=True, ondelete="restrict")
    currency_id = fields.Many2one(related="company_id.currency_id")
    price = fields.Monetary(readonly=True, help="The price when it was ordered.")
    state = fields.Selection([("ordered", "Ordered"), ("cancelled", "Cancelled"),
                              ("credited", "Credited (school closed)")], default="ordered", required=True)
    account_id = fields.Many2one("bf.school.meal.account", "Meal account", readonly=True, index=True)
    payment_mode = fields.Selection(related="account_id.mode", string="Paid by")
    mode = fields.Selection([("prepaid", "Prepaid balance"), ("monthly", "Monthly invoice")], readonly=True,
                            help="Frozen when ordered: a family that changes mode does not change past orders.")
    invoice_id = fields.Many2one("account.move", "Monthly invoice", readonly=True, copy=False)
    ordered_by_id = fields.Many2one("res.partner", "Ordered by", readonly=True)
    student_allergen_ids = fields.Many2many(related="student_id.meal_allergen_ids", string="Allergies")
    group_names = fields.Char("Group", compute="_compute_group_names")

    @api.depends("date", "student_id", "item_id")
    @api.depends_context("lang")
    def _compute_display_name(self):
        # Without it, a meal account's movements read « bf.school.meal.order,222 » (demo École, 2026-10-02).
        for order in self:
            order.display_name = " · ".join(filter(None, [
                fields.Date.to_string(order.date), order.student_id.name, order.item_id.name]))

    def _compute_group_names(self):
        for order in self:
            order.group_names = ", ".join(order.student_id.student_group_ids.filtered(
                lambda g: g.school_id == order.school_id).mapped("name"))

    @api.constrains("item_id", "day_id", "student_id", "state")
    def _check_order(self):
        for order in self.filtered(lambda o: o.state == "ordered"):
            if order.item_id not in order.day_id.item_ids:
                raise ValidationError(_("This meal is not on the menu that day."))
            if order.day_id.closed:
                raise ValidationError(_("The school is closed that day."))
            conflict = order.item_id.allergen_ids & order.student_id.meal_allergen_ids
            if conflict:
                raise ValidationError(_("%(student)s is allergic to %(allergens)s: this meal cannot be ordered.",
                                        student=order.student_id.name,
                                        allergens=", ".join(conflict.mapped("name"))))
            twin = self.search_count([("id", "!=", order.id), ("student_id", "=", order.student_id.id),
                                      ("day_id", "=", order.day_id.id), ("item_id", "=", order.item_id.id),
                                      ("state", "=", "ordered")])
            if twin:
                raise ValidationError(_("This meal is already ordered for this student that day."))

    @api.model
    def _school_payer(self, student):
        links = student.sudo().student_guardian_link_ids
        payer = links.filtered("is_payer").guardian_id[:1] or links.filtered("can_sign").guardian_id[:1]
        if not payer:
            raise UserError(_("%s has no adult who pays: set one on the student's guardians.", student.name))
        return payer

    @api.model_create_multi
    def create(self, vals_list):
        Account = self.env["bf.school.meal.account"].sudo()
        Item = self.env["bf.school.meal.item"]
        Day = self.env["bf.school.meal.day"]
        for vals in vals_list:
            student = self.env["res.partner"].browse(vals["student_id"])
            day = Day.browse(vals["day_id"])
            account = Account._school_get(self._school_payer(student), day.company_id, day.school_id)
            account._school_lock()
            vals.update({"account_id": account.id, "mode": account.mode,
                         "price": Item.browse(vals["item_id"]).sudo().price})
        orders = super().create(vals_list)
        for order in orders.filtered(lambda o: o.mode == "prepaid" and o.price):
            order.account_id._school_debit(order)
        return orders

    def write(self, vals):
        if {"student_id", "day_id", "item_id"} & set(vals):
            raise UserError(_("An order is not changed: cancel it and order again."))
        # 🔴 Price, payment and state move only through the methods of the model: by RPC, an
        # order put back to "ordered" and cancelled again was refunded twice.
        if not self.env.su and {"price", "mode", "account_id", "invoice_id", "state"} & set(vals):
            raise UserError(_("An order is cancelled or credited with its buttons."))
        return super().write(vals)

    def _school_release(self, state):
        """Cancelled or credited: the prepaid balance gets the price back; nothing is billed."""
        for order in self:
            if order.invoice_id:
                raise UserError(_("This meal is already on a monthly invoice."))
            order.sudo().write({"state": state})
            if order.mode == "prepaid" and order.price:
                order.account_id._school_credit(order)

    def action_cancel(self):
        """The office cancels at any time before the meal is billed."""
        self.check_access("write")
        self.filtered(lambda o: o.state == "ordered")._school_release("cancelled")
        return True

    # --- The family's side (portal) ------------------------------------------------

    @api.model
    def _school_place(self, partner, student, day, item):
        """A family orders. Every check lives here; the controller only calls it."""
        links = partner._school_portal_links().filtered(lambda l: l.student_id == student)
        # 🔴 An order spends the payer's money: the payer or a holder of parental authority orders,
        # not a step-parent who only receives notices.
        if not links.filtered(lambda l: l.is_payer or l.has_parental_authority):
            raise UserError(_("You do not order for this student."))
        if day.school_id not in student.sudo().student_group_ids.school_id:
            raise UserError(_("This menu is for another school."))
        if not day._is_orderable():
            raise UserError(_("Orders for this day are closed."))
        # 🔴 The portal catches the error to show it: without a savepoint, an order refused for
        # its balance would stay written (a caught error does not roll back the request).
        with self.env.cr.savepoint():
            return self.sudo().create({"student_id": student.id, "day_id": day.id, "item_id": item.id,
                                       "ordered_by_id": partner.id})

    def _school_cancel_by(self, partner):
        self.ensure_one()
        order = self.sudo()
        if order.student_id not in partner._school_portal_links().filtered(
                lambda l: l.is_payer or l.has_parental_authority).student_id:
            raise UserError(_("You do not order for this student."))
        if order.state != "ordered":
            raise UserError(_("This meal is no longer ordered."))
        if not order.day_id._is_cancellable():
            raise UserError(_("It is too late to cancel this meal."))
        order._school_release("cancelled")
        return True


class MealClose(models.TransientModel):
    _name = "bf.school.meal.close"
    _description = "Close a meal day"

    reason = fields.Char(required=True, default=lambda s: _("School closed"))

    def action_close(self):
        days = self.env["bf.school.meal.day"].browse(self.env.context.get("active_ids", []))
        days.action_close(self.reason)
        return {"type": "ir.actions.act_window_close"}
