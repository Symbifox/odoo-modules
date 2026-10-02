from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSwatch(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, "bfs_alice", groups="base.group_user")
        cls.bob = new_test_user(cls.env, "bfs_bob", groups="base.group_user")
        Swatch = cls.env["bf.color.swatch"]
        cls.shared = Swatch.create({
            "name": "Company", "line_ids": [(0, 0, {"color_hex": "#abc"})],
        })
        cls.mine = Swatch.with_user(cls.alice).create({
            "name": "Alice", "user_id": cls.alice.id,
            "line_ids": [(0, 0, {"color_hex": "#112233", "sequence": 2}),
                         (0, 0, {"color_hex": "#445566", "sequence": 1})],
        })

    def test_available_own_then_shared(self):
        got = self.env["bf.color.swatch"].with_user(self.alice).bf_available()
        names = [s["name"] for s in got]
        self.assertEqual(names[:1], ["Alice"])
        self.assertIn("Company", names)
        self.assertEqual(got[0]["colors"], ["#445566", "#112233"])
        self.assertEqual(self.shared.colors(), ["#AABBCC"])

    def test_personal_swatch_is_private(self):
        got = self.env["bf.color.swatch"].with_user(self.bob).bf_available()
        self.assertNotIn("Alice", [s["name"] for s in got])

    def test_add_to_mine(self):
        Swatch = self.env["bf.color.swatch"].with_user(self.bob)
        first = Swatch.bf_add_to_mine("#abcdef")
        again = Swatch.bf_add_to_mine("#ABCDEF")
        Swatch.bf_add_to_mine("#000000")
        self.assertEqual(first, again)
        swatch = Swatch.browse(first)
        self.assertEqual(swatch.user_id, self.bob)
        self.assertEqual(swatch.colors(), ["#ABCDEF", "#000000"])

    def test_shared_swatch_lines_are_read_only_for_users(self):
        """A user reads a shared swatch's colors but cannot change them."""
        line = self.shared.line_ids.with_user(self.bob)
        self.assertEqual(line.color_hex, "#AABBCC")
        with self.assertRaises(AccessError):
            line.write({"color_hex": "#000000"})
        with self.assertRaises(AccessError):
            line.unlink()
        with self.assertRaises(AccessError):
            self.env["bf.color.swatch.line"].with_user(self.bob).create(
                {"swatch_id": self.shared.id, "color_hex": "#000000"})
        with self.assertRaises(AccessError):
            self.mine.line_ids[:1].with_user(self.bob).write({"color_hex": "#000000"})

    def test_owner_and_admin_edit_lines(self):
        self.mine.line_ids[:1].with_user(self.alice).write({"color_hex": "#010203"})
        admin = new_test_user(self.env, "bfs_admin", groups="base.group_user,base.group_system")
        self.shared.line_ids.with_user(admin).write({"color_hex": "#040506"})
        self.assertEqual(self.shared.colors(), ["#040506"])

    def test_personal_swatch_cannot_become_shared(self):
        """Rules run before a write only: the owner fields are checked after it."""
        with self.assertRaises(AccessError):
            self.mine.with_user(self.alice).write({"user_id": False})
        with self.assertRaises(AccessError):
            self.mine.with_user(self.alice).write({"user_id": self.bob.id})

    def test_own_line_cannot_be_moved_into_a_shared_swatch(self):
        line = self.mine.line_ids[:1].with_user(self.alice)
        with self.assertRaises(AccessError):
            line.write({"swatch_id": self.shared.id})
        line.write({"color_hex": "#0A0B0C"})
        self.assertEqual(line.color_hex, "#0A0B0C")
