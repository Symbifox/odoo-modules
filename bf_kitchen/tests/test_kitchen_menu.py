"""Menu engineering, the effect of an ingredient price increase, and the food cost of a period.

Fixtures: TestKitchen (salmon at 50 % yield, lemon sauce of cream, salt; a tartare at 20 $, target 30 %). The sales
of the matrix come from the bridges: here, `_bf_menu_sales` is replaced by the sales a test gives."""
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .test_kitchen import TestKitchen

MENU = "odoo.addons.bf_kitchen.models.kitchen_menu.KitchenMenu._bf_menu_sales"


@tagged("post_install", "-at_install")
class TestKitchenMenu(TestKitchen):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.mains = env["product.category"].create({"name": "Plats essai"})
        cls.desserts = env["product.category"].create({"name": "Desserts essai"})
        cls.lemon.with_company(cls.company).bf_cost_manual = 1.0
        Product, Bom = env["product.product"], env["mrp.bom"]

        def dish(name, price, lemons, categ):
            product = Product.create({"name": name, "type": "consu", "bf_kitchen_type": "dish", "list_price": price,
                                      "uom_id": cls.unit.id, "uom_po_id": cls.unit.id, "categ_id": categ.id})
            Bom.create({"product_tmpl_id": product.product_tmpl_id.id, "type": "phantom", "product_qty": 1.0,
                        "product_uom_id": cls.unit.id, "bf_target_pct": 30.0,
                        "bom_line_ids": [Command.create({"product_id": cls.lemon.id, "product_qty": lemons,
                                                         "product_uom_id": cls.unit.id})]})
            return product

        # Plate costs 2, 8, 2, 8 $: margins 18, 12, 18, 12 $ at 20 $.
        cls.star = dish("Étoile essai", 20.0, 2.0, cls.mains)
        cls.horse = dish("Cheval essai", 20.0, 8.0, cls.mains)
        cls.puzzle = dish("Énigme essai", 20.0, 2.0, cls.mains)
        cls.dog = dish("Poids mort essai", 20.0, 8.0, cls.mains)
        cls.cake = dish("Gâteau essai", 6.0, 3.0, cls.desserts)
        cls.chef = env["res.users"].create({
            "name": "Cuisinier menu", "login": "menu_cook", "company_id": cls.company.id,
            "company_ids": [Command.set(cls.company.ids)],
            "groups_id": [Command.set([env.ref("base.group_user").id, env.ref("bf_kitchen.group_kitchen_cook").id])]})

    def sales(self, rows, estimated=()):
        """{product: portions} sold at the list price."""
        return {p.id: {"qty": q, "revenue": q * p.lst_price, "estimated": p in estimated, "sources": {"Essai"},
                       "type": p.product_tmpl_id.bf_kitchen_type} for p, q in rows.items()}

    def compute(self, rows, estimated=()):
        menu = self.env["bf.kitchen.menu"].create({"date_from": self.today - timedelta(days=7),
                                                   "date_to": self.today})
        with patch(MENU, return_value=self.sales(rows, estimated)):
            menu.action_compute()
        return menu, {l.product_id: l for l in menu.line_ids}

    def test_m1_the_four_quadrants(self):
        """Popular: at least 70 % of an even share of the section; profitable: at least the section's average
        margin, weighted by the portions sold."""
        menu, lines = self.compute({self.star: 40, self.horse: 40, self.puzzle: 5, self.dog: 5, self.cake: 10})
        self.assertEqual([lines[p].quadrant for p in (self.star, self.horse, self.puzzle, self.dog)],
                         ["star", "plowhorse", "puzzle", "dog"])
        self.assertAlmostEqual(lines[self.star].margin_unit, 18.0)
        self.assertAlmostEqual(lines[self.horse].cost_portion, 8.0)
        self.assertAlmostEqual(lines[self.star].popularity_pct, 100.0 * 40 / 90, places=1)
        self.assertAlmostEqual(lines[self.star].section_margin_avg, (40 * 18 + 40 * 12 + 5 * 18 + 5 * 12) / 90.0)
        self.assertEqual((menu.star_count, menu.dog_count), (2, 1), "the cake is the star of its section")
        self.assertAlmostEqual(menu.revenue, 90 * 20.0 + 10 * 6.0)
        self.assertAlmostEqual(menu.food_cost_pct, 100.0 * (45 * 2 + 45 * 8 + 10 * 3) / menu.revenue, places=1)
        self.assertTrue(lines[self.dog].advice)

    def test_m2_sections_compared_apart(self):
        """A dessert is compared with the desserts: alone in its section, sold, it is a star there."""
        _menu, lines = self.compute({self.star: 40, self.horse: 40, self.cake: 3})
        self.assertEqual((lines[self.cake].section, lines[self.cake].quadrant), (self.desserts.display_name, "star"))

    def test_m3_unsold_dish_and_real_price(self):
        """A dish of the menu that sold nothing is on the matrix, at its selling price; a dish sold at a
        discount counts its real price."""
        sales = self.sales({self.star: 10})
        sales[self.star.id]["revenue"] = 10 * 15.0
        menu = self.env["bf.kitchen.menu"].create({"date_from": self.today, "date_to": self.today})
        with patch(MENU, return_value=sales):
            menu.action_compute()
        lines = {l.product_id: l for l in menu.line_ids}
        self.assertAlmostEqual(lines[self.star].price_avg, 15.0)
        self.assertEqual((lines[self.dog].qty, lines[self.dog].price_avg, lines[self.dog].popular), (0.0, 20.0, False))

    def test_m4_estimated_and_access(self):
        """Estimated sales are said; the matrix is the manager's (it shows the costs)."""
        menu, _lines = self.compute({self.star: 4}, estimated=(self.star,))
        self.assertTrue(menu.estimated)
        with self.assertRaises(AccessError):
            self.env["bf.kitchen.menu"].with_user(self.chef).create({"date_from": self.today, "date_to": self.today})
        with self.assertRaises(UserError):
            self.env["bf.kitchen.menu"].create({"date_from": self.today, "date_to": self.today - timedelta(days=1)}) \
                .action_compute()

    def test_m5_period_in_local_days(self):
        """The period is whole local days: its end is the next day's start."""
        menu = self.env["bf.kitchen.menu"].create({"date_from": self.today, "date_to": self.today})
        start, end = menu._bf_bounds()
        self.assertEqual(end - start, timedelta(days=1))
        first, last = menu._bf_local_days(self.company, start, end)
        self.assertEqual((first, last), (self.today, self.today))

    # --- if an ingredient goes up
    def test_i1_ingredient_through_a_sub_recipe(self):
        """Cream up 50 %: the tartare's sauce costs more, through its sub-recipe; the dishes without cream
        are not listed."""
        self.cream.with_company(self.company).bf_cost_manual = 0.02
        self.salmon.with_company(self.company).bf_cost_manual = 0.01
        self.salt_g.with_company(self.company).bf_cost_manual = 0.0
        self.env["product.product"].flush_model()
        before = self.dish_bom._bf_raw_cost(self.company)[1]
        impact = self.env["bf.kitchen.price.impact"].create(
            {"product_ids": [Command.set(self.cream.ids)], "increase_pct": 50.0})
        impact.action_compute()
        self.assertEqual(impact.line_ids.bom_id, self.dish_bom)
        line = impact.line_ids
        self.assertAlmostEqual(line.cost_before, before, places=2)
        self.assertAlmostEqual(line.cost_after - line.cost_before, 30 * 0.02 * 0.5, places=2)

    def test_i2_going_over_the_target(self):
        """Lemons up 80 %: the dishes that cross their 30 % target are flagged, with a suggested price."""
        impact = self.env["bf.kitchen.price.impact"].create(
            {"product_ids": [Command.set(self.lemon.ids)], "increase_pct": 80.0})
        impact.action_compute()
        lines = {l.product_tmpl_id: l for l in impact.line_ids}
        star = lines[self.star.product_tmpl_id]
        self.assertAlmostEqual((star.cost_before, star.cost_after), (2.0, 3.6))
        self.assertFalse(star.newly_over)
        horse = lines[self.horse.product_tmpl_id]
        self.assertTrue(horse.over_after)
        self.assertFalse(horse.newly_over, "it was over already: 8 $ on 20 $")
        self.assertTrue(lines[self.cake.product_tmpl_id].over_after)
        self.assertGreater(horse.price_suggested, 20.0)
        self.assertEqual(star.price_suggested, 0.0, "no price to suggest under the target")
        self.assertIn("Citron", impact.display_name)
        self.assertEqual(impact.over_count, len(impact.line_ids.filtered("newly_over")))

    def test_i3_preparation_chosen_itself(self):
        """A preparation chosen goes up as such, not through its ingredients."""
        self.cream.with_company(self.company).bf_cost_manual = 0.02
        sauce_cost = self.cost(self.sauce)
        impact = self.env["bf.kitchen.price.impact"].create(
            {"product_ids": [Command.set(self.sauce.ids)], "increase_pct": 10.0})
        impact.action_compute()
        line = impact.line_ids.filtered(lambda l: l.bom_id == self.dish_bom)
        self.assertAlmostEqual(line.cost_after - line.cost_before, 30 * sauce_cost * 0.1, places=3)

    # --- the food cost of a period
    def test_v1_theoretical_and_actual_food_cost(self):
        """Theoretical: what the recipes say the sales consumed; actual: consumed + lost - unexplained (bought
        less what the stock gained), both as a share of the food sales."""
        variance = self.env["bf.kitchen.variance"].create({"sales_value": 1000.0, "line_ids": [
            Command.create({"product_id": self.lemon.id, "kind": "purchase", "qty": 1, "value": 500.0}),
            Command.create({"product_id": self.lemon.id, "kind": "consumption", "qty": 1, "value": 280.0}),
            Command.create({"product_id": self.lemon.id, "kind": "loss", "qty": 1, "value": 20.0}),
            Command.create({"product_id": self.lemon.id, "kind": "unexplained", "qty": -1, "value": -30.0}),
        ]})
        self.assertAlmostEqual(variance.theoretical_pct, 28.0)
        self.assertAlmostEqual(variance.actual_value, 330.0)
        self.assertAlmostEqual(variance.actual_pct, 33.0)

    # --- companies, time zones, set menus, dishes off the menu
    def test_r1_another_company(self):
        """A kitchen manager of another company neither computes nor reads this company's matrix."""
        other = self.env["res.company"].create({"name": "Autre cuisine essai"})
        outsider = self.env["res.users"].create({
            "name": "Responsable ailleurs", "login": "menu_out", "company_id": other.id,
            "company_ids": [Command.set(other.ids)],
            "groups_id": [Command.set([self.env.ref("base.group_user").id,
                                       self.env.ref("bf_kitchen.group_kitchen_manager").id])]})
        menu, _lines = self.compute({self.star: 4})
        with self.assertRaises(AccessError):
            self.env["bf.kitchen.menu"].with_user(outsider).create(
                {"company_id": self.company.id, "date_from": self.today, "date_to": self.today}).action_compute()
        self.assertFalse(self.env["bf.kitchen.menu.line"].with_user(outsider).search([("menu_id", "=", menu.id)]))
        with self.assertRaises(AccessError):
            self.env["bf.kitchen.price.impact"].with_user(outsider).create(
                {"company_id": self.company.id, "product_ids": [Command.set(self.lemon.ids)]}).action_compute()

    def test_r1_restaurant_time(self):
        """The period is in the restaurant's time zone, whoever looks."""
        self.company.partner_id.tz = "America/Toronto"
        self.env.user.tz = "Pacific/Auckland"
        menu = self.env["bf.kitchen.menu"].create({"date_from": self.today, "date_to": self.today})
        start, _end = menu._bf_bounds()
        self.assertEqual(start.hour in (4, 5), True, "midnight in Toronto, %s UTC" % start)

    def test_r1_count_day_in_one_period(self):
        """A source dated by day counts the day of a count in the period after it, never in both."""
        menu = self.env["bf.kitchen.menu"]
        self.company.partner_id.tz = "UTC"
        day = fields.Datetime.to_datetime("2026-11-02 20:00:00")
        before = menu._bf_local_days(self.company, day - timedelta(days=7), day)
        after = menu._bf_local_days(self.company, day, day + timedelta(days=7))
        self.assertEqual(before[1] + timedelta(days=1), after[0])
        self.assertEqual(after[0], day.date())

    def test_r1_set_menu_of_dishes(self):
        """A set menu made of dishes goes up with the ingredients of its dishes."""
        menu_dish = self.env["product.product"].create({
            "name": "Table d'hôte essai", "type": "consu", "bf_kitchen_type": "dish", "list_price": 30.0,
            "uom_id": self.unit.id, "uom_po_id": self.unit.id, "categ_id": self.mains.id})
        bom = self.env["mrp.bom"].create({
            "product_tmpl_id": menu_dish.product_tmpl_id.id, "type": "phantom", "product_qty": 1.0,
            "product_uom_id": self.unit.id, "bom_line_ids": [
                Command.create({"product_id": self.star.id, "product_qty": 1.0, "product_uom_id": self.unit.id}),
                Command.create({"product_id": self.cake.id, "product_qty": 1.0, "product_uom_id": self.unit.id})]})
        impact = self.env["bf.kitchen.price.impact"].create(
            {"product_ids": [Command.set(self.lemon.ids)], "increase_pct": 100.0})
        impact.action_compute()
        line = impact.line_ids.filtered(lambda l: l.bom_id == bom)
        self.assertTrue(line)
        self.assertAlmostEqual(line.cost_after - line.cost_before, 5.0)

    def test_r1_dishes_off_the_menu(self):
        """A dish sold without a recipe is shown, not classified; a dish not for sale is not on
        the menu; returns of an earlier period weigh nothing."""
        loose = self.env["product.product"].create({"name": "Plat sans recette essai", "type": "consu",
                                                    "bf_kitchen_type": "dish", "list_price": 10.0,
                                                    "categ_id": self.mains.id})
        self.dog.product_tmpl_id.sale_ok = False
        _menu, lines = self.compute({self.star: 40, self.horse: 40, self.puzzle: -3, loose: 5})
        self.assertNotIn(self.dog, lines)
        self.assertTrue(lines[loose].no_recipe)
        self.assertFalse(lines[loose].quadrant)
        self.assertEqual(lines[self.puzzle].popularity_pct, 0.0)
        self.assertAlmostEqual(lines[self.star].section_margin_avg, (40 * 18 + 40 * 12) / 80.0)
        menu, _lines = self.compute({self.star: 40, self.horse: 40, self.puzzle: 5, self.dog: 5})
        self.assertEqual(menu.line_ids.filtered(lambda l: l.section == self.mains.display_name)[:1].quadrant, "star",
                         "the stars first")

    def test_r2_no_recipe_left_out_of_the_totals(self):
        """A dish sold without a recipe does not show as pure margin: the menu's food cost and margin
        are those of the dishes with a recipe; its sales are said apart."""
        loose = self.env["product.product"].create({"name": "Plat sans recette essai", "type": "consu",
                                                    "bf_kitchen_type": "dish", "list_price": 10.0,
                                                    "categ_id": self.mains.id})
        menu, lines = self.compute({self.star: 10})
        cost, margin = menu.food_cost_pct, menu.margin
        menu, lines = self.compute({self.star: 10, loose: 30})
        self.assertEqual((menu.food_cost_pct, menu.margin), (cost, margin))
        self.assertEqual(menu.revenue_no_recipe, 300.0)
        self.assertEqual((lines[loose].margin_unit, lines[loose].margin_total), (0.0, 0.0))

    def test_m6_threshold_and_weighted_margin(self):
        """70 % of an even share: a dish at 20 % of a section of four is popular (the even share is 25 %); the
        section's margin is weighted by the portions sold, not a plain average."""
        _menu, lines = self.compute({self.star: 40, self.horse: 40, self.puzzle: 20, self.dog: 0})
        self.assertEqual(lines[self.puzzle].quadrant, "star")
        self.assertAlmostEqual(lines[self.star].section_margin_avg, (40 * 18 + 40 * 12 + 20 * 18) / 100.0)
