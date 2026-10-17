from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import KeyCase


@tagged("post_install", "-at_install")
class TestHierarchy(KeyCase):

    def test_levels_follow_the_tree(self):
        self.assertEqual(self.gm.level, "grand")
        self.assertEqual(self.aa.level, "master")
        self.assertEqual(self.aa1.level, "change")
        self.assertFalse(self.card.level)  # a card sits outside the master key system
        self.assertTrue(self.aa.is_master)
        self.assertFalse(self.aa1.is_master)
        # Masters wait for an approval by default, change keys do not.
        self.assertTrue(self.aa.approval_required)
        self.assertFalse(self.aa1.approval_required)

    def test_doors_opened_climb_the_hierarchy(self):
        self.assertEqual(self.aa1._doors_opened(), self.d201)
        # The master opens its change keys' doors, but not the cylinder that is not master keyed.
        self.assertEqual(self.aa._doors_opened(), self.d201 | self.d202)
        # AA2 itself does open its NMK server room.
        self.assertEqual(self.aa2._doors_opened(), self.d202 | self.server)
        # The grand master opens everything master keyed under it, and the front door.
        self.assertEqual(self.gm._doors_opened(), self.d201 | self.d202 | self.d301 | self.front)
        self.assertEqual(self.card._doors_opened(), self.front)

    def test_openers_of_a_door(self):
        self.assertEqual(self.d201._openers(), self.aa1 | self.aa | self.gm)
        self.assertEqual(self.server._openers(), self.aa2)
        self.assertEqual(self.front._openers(), self.gm | self.card)

    def test_symbol_warnings(self):
        self.assertFalse(self.aa1._symbol_problem())
        self.aa1.symbol = "AB7"
        self.assertIn("notation", self.aa1._symbol_problem())
        self.aa1.symbol = "AI1"
        self.assertIn("I, O and Q", self.aa1._symbol_problem())
        self.aa1.symbol = "XAA1"
        self.assertIn("Cross keyed", self.aa1._symbol_problem())

    def test_two_level_notation(self):
        system = self.env["bf.key.system"].create({"name": "Annex", "levels": "2"})
        master = self.env["bf.key.type"].create({"name": "Annex master", "system_id": system.id, "symbol": "AA"})
        change = self.env["bf.key.type"].create({"name": "Annex 1", "system_id": system.id,
                                                 "symbol": "1AA", "parent_id": master.id})
        self.assertEqual(master.level, "master")
        self.assertEqual(change.level, "change")
        self.assertFalse(change._symbol_problem())
        change.symbol = "AA1"
        self.assertTrue(change._symbol_problem())

    def test_hierarchy_guards(self):
        # A cycle: refused by the parent store before our constraint even runs.
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.gm.parent_id = self.aa1
        other = self.env["bf.key.system"].create({"name": "Other"})
        with self.assertRaises(ValidationError):
            self.env["bf.key.type"].create({"name": "Stray", "system_id": other.id, "parent_id": self.aa.id})
        with self.assertRaises(ValidationError):
            # A fourth level in a three-level system.
            self.env["bf.key.type"].create({"name": "Too deep", "system_id": self.system.id,
                                            "parent_id": self.aa1.id})

    def test_label_never_names_the_door(self):
        self.assertEqual(self.aa1_1.label, "K17-1")
        self.assertNotIn("201", self.aa1_1.label)

    def test_area_responsibility_flows_down(self):
        self.assertIn(self.responsible, self.floor2.all_manager_ids)
        self.assertNotIn(self.responsible, self.other_area.all_manager_ids)
