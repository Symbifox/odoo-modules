from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import KeyCase


@tagged("post_install", "-at_install")
class TestDepartureLoss(KeyCase):

    def test_departure_creates_one_recover_per_item(self):
        key = self.issue(self.aa1_1, self.alice)
        card = self.issue(self.card_1, self.alice)
        self.alice.write({"departure_date": fields.Date.today()})
        recover = self.env.ref("bf_key_management.activity_type_recover")
        self.assertEqual(key.activity_ids.activity_type_id, recover)
        self.assertEqual(key.activity_ids.user_id, self.manager)
        self.assertIn("Deactivate", card.activity_ids.note)
        # Archiving afterwards does not duplicate them.
        self.alice.write({"active": False})
        self.assertEqual(len(key.activity_ids), 1)
        self.assertTrue(key.employee_departed)
        # Archiving is not blocked, and the return closes the activity.
        self.assertFalse(self.alice.active)
        key.with_user(self.manager).action_return()
        self.assertFalse(key.activity_ids)

    def test_departure_with_nothing_held(self):
        self.bob.write({"departure_date": fields.Date.today()})
        self.assertFalse(self.env["mail.activity"].search([
            ("res_model", "=", "bf.key.assignment"), ("user_id", "=", self.manager.id)]))

    def test_loss_closes_and_freezes_compromised_doors(self):
        assignment = self.issue(self.aa_1, self.alice)
        wizard = self.env["bf.key.loss.wizard"].with_user(self.manager).create(
            {"copy_id": self.aa_1.id, "incident_type": "lost", "circumstances": "Bus 55"})
        self.assertEqual(wizard.compromised_count, 2)
        action = wizard.action_declare()
        incident = self.env["bf.key.incident"].browse(action["res_id"])
        self.assertEqual(assignment.end_reason, "lost")
        self.assertEqual(self.aa_1.state, "lost")
        self.assertEqual(incident.compromised_door_ids, self.d201 | self.d202)
        self.assertEqual(incident.decision, "pending")
        self.assertIn("rekey", incident.activity_ids.summary)
        # The system changes later; the incident keeps what was true that day.
        self.d201.keyed_type_id = self.ab1
        self.assertEqual(incident.compromised_door_ids, self.d201 | self.d202)
        self.assertEqual(self.alice.bf_key_incident_count, 1)
        with self.assertRaises(UserError):
            self.issue(self.aa_1, self.bob)

    def test_lost_card_asks_for_deactivation(self):
        self.issue(self.card_1, self.alice)
        action = self.env["bf.key.loss.wizard"].with_user(self.manager).create(
            {"copy_id": self.card_1.id, "incident_type": "stolen", "police_report": "2026-1234"}).action_declare()
        incident = self.env["bf.key.incident"].browse(action["res_id"])
        self.assertEqual(incident.decision, "deactivate")
        self.assertIn("access system", incident.activity_ids.summary)

    def test_found_again(self):
        self.issue(self.aa1_1, self.alice)
        action = self.env["bf.key.loss.wizard"].with_user(self.manager).create(
            {"copy_id": self.aa1_1.id}).action_declare()
        incident = self.env["bf.key.incident"].browse(action["res_id"])
        incident.with_user(self.manager).decision = "found"
        incident.with_user(self.manager).action_resolve()
        self.assertEqual(self.aa1_1.state, "available")
        self.assertFalse(incident.activity_ids)
