from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import KeyCase


@tagged("post_install", "-at_install")
class TestAccess(KeyCase):

    def test_holder_sees_only_their_own(self):
        mine = self.issue(self.aa1_1, self.alice)
        self.issue(self.aa1_2, self.bob)
        Assignment = self.env["bf.key.assignment"].with_user(self.alice_user)
        self.assertEqual(Assignment.search([]), mine)
        self.assertEqual(self.env["bf.key.copy"].with_user(self.alice_user).search([]), self.aa1_1)
        with self.assertRaises(AccessError):
            mine.with_user(self.alice_user).write({"date_due": "2030-01-01"})
        with self.assertRaises(AccessError):
            self.env["bf.key.incident"].with_user(self.alice_user).search([])

    def test_holder_cannot_read_the_symbols(self):
        key_type = self.aa1.with_user(self.alice_user)
        self.assertEqual(key_type.name, "Office 201")
        with self.assertRaises(AccessError):
            key_type.read(["symbol"])
        with self.assertRaises(AccessError):
            self.d201.with_user(self.alice_user).read(["keyed_type_id"])

    def test_area_responsible_sees_their_areas(self):
        in_area = self.issue(self.aa1_1, self.alice)
        outside = self.issue(self.wh_1, self.bob)
        visible = self.env["bf.key.assignment"].with_user(self.responsible).search([])
        self.assertIn(in_area, visible)
        self.assertNotIn(outside, visible)
        # They hand out the keys of their areas.
        self.issue(self.aa1_2, self.carol, user=self.responsible)

    def test_holder_can_open_their_hand_out_and_the_employee_form(self):
        """The web client reads every field of a view: a holder must be able to open their own
        hand-out with the "My keys" view, and an HR user the employee form, without AccessError."""
        mine = self.issue(self.aa1_1, self.alice)
        form = self.env.ref("bf_key_management.view_bf_key_assignment_my_form")
        arch = self.env["bf.key.assignment"].with_user(self.alice_user).get_views(
            [(form.id, "form")])["views"]["form"]["arch"]
        import re
        names = set(re.findall(r'<field name="([a-z_0-9]+)"', arch))
        mine.with_user(self.alice_user).read(sorted(names))
        hr_user = self.env["res.users"].create({
            "name": "HR", "login": "key_hr",
            "groups_id": [(6, 0, [self.env.ref("hr.group_hr_user").id])]})
        # HR users are internal users, hence key holders, but not area responsibles.
        arch = self.env["hr.employee"].with_user(hr_user).get_views([(False, "form")])["views"]["form"]["arch"]
        self.assertNotIn("bf_key_assignment_ids", arch)
        self.assertNotIn("bf_key_count", arch)

    def test_dashboard_counts(self):
        # Measured as a difference: the database may already hold keys.
        # The counts are computed when read: snapshot them as numbers.
        def counts():
            # Through the menu's path: a saved record (onchange would leave zeros).
            action = self.env["bf.key.dashboard"].with_user(self.manager).action_open_overview()
            board = self.env["bf.key.dashboard"].with_user(self.manager).browse(action["res_id"])
            return {f: board[f] for f in ("held_count", "master_count", "departed_count")}
        before = counts()
        self.issue(self.aa_1, self.alice)
        after = counts()
        self.assertEqual(after["held_count"] - before["held_count"], 1)
        self.assertEqual(after["master_count"] - before["master_count"], 1)
        self.alice.write({"active": False})
        self.assertEqual(counts()["departed_count"] - before["departed_count"], 1)

    def test_settings_menu_only_for_administrators(self):
        """A key manager who is not an administrator must not see a menu that refuses them."""
        menu = self.env.ref("bf_key_management.menu_keys_settings")
        visible = self.env["ir.ui.menu"].with_user(self.manager)._visible_menu_ids()
        self.assertNotIn(menu.id, visible)

    def test_settings_show_the_standard_text_when_they_open(self):
        values = self.env["res.config.settings"].onchange({}, [], {
            "bf_key_acknowledgement_standard": {}, "company_id": {}})
        self.assertIn("acknowledge", values["value"]["bf_key_acknowledgement_standard"])
