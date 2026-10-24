from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from .common import WorkHoursCase, acting_as


@tagged("post_install", "-at_install")
class TestFirstVisit(WorkHoursCase):
    """The consent screen stops a person at their first visit of the app, once."""

    def home(self, user, **session):
        with acting_as(user.id, **session):
            return self.env["bf.work.choices"].with_user(user)._bf_home()

    def skip(self, user, **session):
        with acting_as(user.id, **session):
            return self.env["bf.work.choices"].with_user(user).bf_welcome_skip()

    def assertScreen(self, action):
        self.assertEqual(action.get("tag"), "bf_work_hours.consent")
        self.assertEqual(action["params"], {"scope": "hours", "mode": "welcome"})

    def assertDays(self, action):
        self.assertEqual(action.get("res_model"), "bf.work.day")

    def test_first_visit_then_never_again(self):
        self.assertScreen(self.home(self.alice))
        self.assertScreen(self.home(self.alice))
        self.assertDays(self.skip(self.alice))
        self.assertDays(self.home(self.alice))
        welcome = self.env["bf.work.welcome"].sudo().search([("user_id", "=", self.alice.id)])
        self.assertEqual(len(welcome), 1)
        self.assertDays(self.skip(self.alice))
        self.assertEqual(self.env["bf.work.welcome"].sudo().search_count([("user_id", "=", self.alice.id)]), 1)
        self.assertScreen(self.home(self.bob))  # each person answers for themselves

    def test_no_screen_once_turned_on_even_if_withdrawn(self):
        self.activate(self.alice)
        self.assertDays(self.home(self.alice))
        self.withdraw(self.alice)
        self.assertDays(self.home(self.alice))

    def test_never_to_someone_impersonating_or_an_api_key(self):
        self.assertDays(self.home(self.alice, bf_impersonate=99))
        self.assertDays(self.skip(self.alice, bf_impersonate=99))
        self.assertDays(self.home(self.alice, path="/xmlrpc/2/object"))
        self.assertDays(self.skip(self.alice, path="/json/2/bf.work.choices/bf_welcome_skip"))
        self.assertFalse(self.env["bf.work.welcome"].sudo().search([("user_id", "=", self.alice.id)]),
                         "The person's first visit is still theirs.")
        self.assertScreen(self.home(self.alice))

    def test_the_answer_is_the_persons_own(self):
        self.skip(self.alice)
        self.assertFalse(self.env["bf.work.welcome"].with_user(self.bob).search([]))
        self.assertFalse(self.env["bf.work.welcome"].with_user(self.admin).search([("user_id", "=", self.alice.id)]))
        self.assertEqual(len(self.env["bf.work.welcome"].with_user(self.alice).search([])), 1)

    def test_the_entry_runs_for_any_employee(self):
        """The menu's server action runs for a plain employee (it needs write access to its model)."""
        action = self.env.ref("bf_work_hours.action_bf_work_home")
        with acting_as(self.alice.id):
            result = action.with_user(self.alice).run()
        self.assertScreen(result)
        menu = self.env.ref("bf_work_hours.menu_bf_work_mine")
        self.assertEqual(menu.action, action)

    def test_not_for_portal_users(self):
        """Both methods of the screen are public, so callable over RPC: internal users only."""
        portal = new_test_user(self.env, "wh_portal_screen", groups="base.group_portal")
        with acting_as(portal.id):
            with self.assertRaises(AccessError):
                self.env["bf.work.choices"].with_user(portal).bf_welcome_skip()
            with self.assertRaises(AccessError):
                self.env["bf.work.activate.wizard"].with_user(portal).bf_screen_data("hours")
        self.assertFalse(self.env["bf.work.welcome"].sudo().search([("user_id", "=", portal.id)]))
