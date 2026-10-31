"""School meals and the kitchen: a meal points to its dish (cost, allergens), the meals served leave
the stock at the cancellation hour, a meal cancelled after it is a loss in the variance report.

The fixtures are bf_kitchen's TestKitchen (a dish of 240 g of salmon, 30 g of a cream sauce, the
cream carrying milk, and 3 g of salt). Odoo 18 runs only the test methods a class defines itself."""
from datetime import date, timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged

from odoo.addons.bf_kitchen.tests.test_kitchen import TestKitchen


@tagged("post_install", "-at_install")
class TestKitchenSchoolMeal(TestKitchen):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.user.groups_id = [Command.link(env.ref("bf_school_core.group_school_manager").id)]
        # No cancelling once the day has begun: today's meals are served, tomorrow's are not.
        cls.school = env["bf.school"].create({"name": "École cuisine essai", "meal_payment_modes": "monthly",
                                              "meal_cancel_hour": 0.0, "company_id": cls.company.id})
        year = env["bf.school.year"].create({"name": "2026-2027", "school_id": cls.school.id,
                                             "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        year.action_set_current()
        group = env["bf.school.group"].create({"name": "401", "school_id": cls.school.id, "year_id": year.id})
        payer = new_test_user(env, login="ks_payer", groups="base.group_portal", email="ks@essai.test")
        cls.students = env["res.partner"]
        for name in ("Ana Essai", "Ben Essai", "Cléo Essai", "Dom Essai"):
            student = env["res.partner"].create({"name": name, "is_student": True})
            env["bf.school.enrollment"].create({"student_id": student.id, "group_id": group.id})
            env["bf.school.guardian.link"].create({"student_id": student.id, "guardian_id": payer.partner_id.id,
                                                   "is_payer": True})
            cls.students |= student
        cls.milk = env.ref("bf_food_allergen.allergen_milk")
        cls.students[3].meal_allergen_ids = [(6, 0, cls.milk.ids)]
        cls.meal = env["bf.school.meal.item"].create({
            "name": "Tartare du midi", "school_id": cls.school.id, "price": 20.0, "bf_kitchen_dish_id": cls.dish.id})
        cls.plain = env["bf.school.meal.item"].create({"name": "Salade", "school_id": cls.school.id, "price": 5.0})
        cls.stock = env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1).lot_stock_id
        cls.today = cls.school._school_now().date()
        env["ir.config_parameter"].sudo().set_param("bf_kitchen_school_meal.since",
                                                    fields.Date.to_string(cls.today - timedelta(days=7)))

    def day(self, offset=0):
        return self.env["bf.school.meal.day"].create({
            "school_id": self.school.id, "date": self.today + timedelta(days=offset),
            "item_ids": [(6, 0, (self.meal | self.plain).ids)]})

    def order(self, day, student, item=None):
        return self.env["bf.school.meal.order"].create(
            {"student_id": student.id, "day_id": day.id, "item_id": (item or self.meal).id})

    def on_stock(self, product):
        product.invalidate_recordset()
        return product.with_context(location=self.stock.id).qty_available

    def run_cron(self):
        return self.env["bf.school.meal.order"]._cron_bf_kitchen_consume_meals()

    def test_m1_dish_allergens_count(self):
        """The dish's allergens (milk, from the cream of its sauce) count with the meal's: a student
        allergic to milk cannot order it; the plain meal stays open."""
        self.assertEqual(self.meal.bf_kitchen_allergen_ids, self.milk)
        self.assertIn(self.milk, self.meal._school_allergens())
        day = self.day()
        with self.assertRaises(ValidationError):
            self.order(day, self.students[3])
        self.order(day, self.students[3], self.plain)

    def test_m2_meals_served_leave_once(self):
        """Past the cancellation hour, today's meals leave the stock in one consumption; each meal once;
        a meal added later leaves at the next pass; tomorrow's wait."""
        today, tomorrow = self.day(0), self.day(1)
        orders = self.order(today, self.students[0]) | self.order(today, self.students[1]) | \
            self.order(today, self.students[2], self.plain)
        later = self.order(tomorrow, self.students[0])
        taken = self.run_cron()
        self.assertEqual(taken, orders[:2], "the dish meals of today; not the plain meal, not tomorrow")
        picking = orders[0].bf_kitchen_picking_id
        self.assertEqual(picking.state, "done")
        self.assertEqual((picking.bf_kitchen_source, picking.bf_kitchen_event_date), ("school_meal", self.today))
        self.assertAlmostEqual(self.on_stock(self.salmon), -480.0)
        self.assertFalse(self.run_cron(), "nothing twice")
        self.assertFalse(later.bf_kitchen_picking_id)
        late = self.order(today, self.students[2])
        self.assertEqual(self.run_cron(), late)
        self.assertAlmostEqual(self.on_stock(self.salmon), -720.0)

    def test_m3_cancelled_after_is_a_loss(self):
        """A meal cancelled after it left: nothing comes back; the variance report moves it from
        consumption to losses."""
        today = self.day(0)
        orders = self.order(today, self.students[0]) | self.order(today, self.students[1]) | \
            self.order(today, self.students[2])
        self.run_cron()
        orders[1].action_cancel()
        self.assertAlmostEqual(self.on_stock(self.salmon), -720.0, msg="cooked food does not come back")
        report = self.env["bf.kitchen.variance"].create({"warehouse_id": self.stock.warehouse_id.id})
        rows = {}
        for key, qty in report._bf_variance_adjustments(False, False, orders[0].bf_kitchen_picking_id.move_ids):
            rows[key] = rows.get(key, 0.0) + qty
        salmon = [(key, qty) for key, qty in rows.items() if key[0] == self.salmon.id]
        # Labels in the language of the test user (English or French).
        kinds = sorted((key[1], qty) for key, qty in salmon)
        self.assertEqual(kinds, [("consumption", -240.0), ("loss", 240.0)])
        reasons = [key[2] for key, _qty in salmon if key[1] == "loss"]
        self.assertIn(reasons[0], ("Meal cancelled after the cut-off", "Repas annulé après l'heure limite"))
        self.assertNotIn(self.dish.id, [key[0] for key in rows], "a dish is a kit")

    def test_m4_days_before_the_bridge_left_alone(self):
        """The meals of days before the bridge was installed do not leave the stock."""
        self.env["ir.config_parameter"].sudo().set_param("bf_kitchen_school_meal.since",
                                                         fields.Date.to_string(self.today))
        old = self.order(self.day(-2), self.students[0])
        self.assertFalse(self.run_cron())
        self.assertFalse(old.bf_kitchen_picking_id)

    def test_m5_cost_on_the_meal(self):
        """The kitchen manager reads the plate cost and the food cost ratio on the meal."""
        self.bill([(self.salmon, 1000.0, 0.02, {})])
        self.meal.invalidate_recordset()
        self.assertAlmostEqual(self.meal.bf_kitchen_cost, self.dish_bom.bf_cost_portion)
        self.assertAlmostEqual(self.meal.bf_kitchen_food_cost_pct, 100.0 * self.dish_bom.bf_cost_portion / 20.0)
        cook = self.mkuser("m5_cook", "bf_kitchen.group_kitchen_cook,bf_school_core.group_school_user")
        with self.assertRaises(AccessError):
            self.meal.with_user(cook).read(["bf_kitchen_cost"])

    def test_m6_link_set_by_the_kitchen_only(self):
        """The consumption of a meal is the server's to write; a meal points to a dish."""
        order = self.order(self.day(0), self.students[0])
        self.run_cron()
        office = self.mkuser("m6_office", "bf_school_core.group_school_manager")
        with self.assertRaises(AccessError):
            order.with_user(office).write({"bf_kitchen_picking_id": False})
        with self.assertRaises(ValidationError):
            self.plain.bf_kitchen_dish_id = self.salmon

    def test_r1_failure_said_once(self):
        """A day whose meals cannot leave the stock is said once on the school, then
        taken out when it works."""
        today = self.day(0)
        order = self.order(today, self.students[0])
        Picking = type(self.env["stock.picking"])
        notes = lambda: self.env["mail.message"].search_count(
            [("model", "=", "bf.school"), ("res_id", "=", self.school.id), ("body", "ilike", "panne d'essai")])
        with patch.object(Picking, "_bf_kitchen_consume", side_effect=UserError("panne d'essai")):
            self.run_cron()
            self.run_cron()
        self.assertEqual(notes(), 1)
        self.assertIn("panne d'essai", today.bf_kitchen_issue)
        self.run_cron()
        self.assertTrue(order.bf_kitchen_picking_id)
        self.assertFalse(today.bf_kitchen_issue)

    def test_r1_meal_link_not_by_context(self):
        """A `default_*` of the context does not mark a meal as taken out."""
        wh = self.stock.warehouse_id
        picking = self.env["stock.picking"].create({"picking_type_id": wh.out_type_id.id})
        office = self.mkuser("r1m_office", "bf_school_core.group_school_manager")
        order = self.env["bf.school.meal.order"].with_user(office).with_context(
            default_bf_kitchen_picking_id=picking.id).create(
            {"student_id": self.students[0].id, "day_id": self.day(0).id, "item_id": self.meal.id})
        self.assertFalse(order.bf_kitchen_picking_id)

    def test_r2_declared_allergen_not_archived(self):
        """An allergen a student is declared allergic to stays active: archived, the
        allergy would no longer be read and the meal no longer refused."""
        with self.assertRaises(UserError):
            self.milk.active = False
        self.assertTrue(self.milk.active)
        lupin = self.env["bf.food.allergen"].create({"name": "Lupin (essai)"})
        lupin.active = False

    def test_r2_dish_linked_later(self):
        """A dish linked to a meal after it was served does not take those meals out:
        only the meals from the day it was linked."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_kitchen_school_meal.since", fields.Date.to_string(self.today - timedelta(days=7)))
        plain_day = self.day(-2)
        earlier = self.order(plain_day, self.students[0], self.plain)
        self.plain.bf_kitchen_dish_id = self.dish
        self.assertEqual(self.plain.bf_kitchen_dish_date, fields.Date.context_today(self.plain))
        self.run_cron()
        self.assertFalse(earlier.bf_kitchen_picking_id, "served before its dish was linked")
