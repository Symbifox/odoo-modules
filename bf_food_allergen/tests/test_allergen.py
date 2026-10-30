from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestFoodAllergen(TransactionCase):

    def test_priority_list(self):
        names = self.env["bf.food.allergen"].search([]).mapped("name")
        self.assertEqual(len(names), 11)
        self.assertEqual(self.env.ref("bf_food_allergen.allergen_peanut").sequence, 1)
