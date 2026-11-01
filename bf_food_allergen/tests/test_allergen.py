from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

PRIORITY = ("peanut", "tree_nut", "sesame", "milk", "egg", "fish", "shellfish", "soy", "wheat", "sulphite", "mustard")


@tagged("post_install", "-at_install")
class TestFoodAllergen(TransactionCase):

    def test_priority_list(self):
        """The eleven priority allergens of Health Canada are shipped, active (a database may add others)."""
        allergens = [self.env.ref("bf_food_allergen.allergen_%s" % key) for key in PRIORITY]
        self.assertTrue(all(a.active for a in allergens))
        self.assertEqual(self.env.ref("bf_food_allergen.allergen_peanut").sequence, 1)

    def test_unused_allergen_archived_and_deleted(self):
        """An allergen nobody declares is archived or deleted; one still declared is not (modules tell)."""
        lupin = self.env["bf.food.allergen"].create({"name": "Lupin (essai)"})
        lupin.active = False
        lupin.unlink()
        Allergen = type(self.env["bf.food.allergen"])
        lupin = self.env["bf.food.allergen"].create({"name": "Lupin (essai 2)"})
        with patch.object(Allergen, "_bf_allergen_uses", lambda recs: {"dish(es)": 1}):
            with self.assertRaises(UserError):
                lupin.active = False
            with self.assertRaises(UserError):
                lupin.unlink()
