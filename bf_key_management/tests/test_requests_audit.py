from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import KeyCase


@tagged("post_install", "-at_install")
class TestRequestsAudit(KeyCase):

    def _request(self, user, **values):
        values.setdefault("reason", "New office")
        return self.env["bf.key.request"].with_user(user).create(values)

    def test_master_request_needs_the_area_responsible(self):
        request = self._request(self.alice_user, type_id=self.aa.id)
        self.assertEqual(request.employee_id, self.alice)
        self.assertEqual(request.approver_ids, self.responsible)
        request.action_submit()
        self.assertEqual(request.state, "submitted")
        self.assertEqual(request.activity_ids.user_id, self.responsible)
        with self.assertRaises(AccessError):
            request.with_user(self.bob_user).action_approve()
        request.with_user(self.responsible).action_approve()
        self.assertEqual(request.state, "approved")
        self.assertFalse(request.activity_ids)
        action = request.with_user(self.responsible).action_issue()
        wizard = self.env["bf.key.issue.wizard"].with_user(self.responsible).with_context(
            action["context"]).create({"copy_id": self.aa_1.id})
        wizard.action_issue()
        self.assertEqual(request.state, "issued")
        self.assertEqual(request.assignment_id.employee_id, self.alice)

    def test_change_key_is_approved_on_submit(self):
        request = self._request(self.alice_user, type_id=self.aa1.id)
        request.action_submit()
        self.assertEqual(request.state, "approved")

    def test_requester_cannot_approve_by_writing(self):
        request = self._request(self.alice_user, type_id=self.aa.id)
        with self.assertRaises(AccessError):
            request.write({"state": "approved"})
        request.action_submit()
        with self.assertRaises(AccessError):
            request.write({"reason": "changed after submit"})

    def test_refusal_needs_a_reason(self):
        request = self._request(self.alice_user, type_id=self.aa.id)
        request.action_submit()
        with self.assertRaises(UserError):
            request.with_user(self.responsible).action_refuse()
        request.with_user(self.responsible).refusal_reason = "Use the card"
        request.with_user(self.responsible).action_refuse()
        self.assertEqual(request.state, "refused")

    def test_warns_when_already_holding_access(self):
        self.issue(self.aa_1, self.alice)
        request = self._request(self.alice_user, door_ids=[(6, 0, self.d201.ids)])
        self.assertIn("Already holds", request.existing_access)
        other = self._request(self.alice_user, door_ids=[(6, 0, self.d301.ids)])
        self.assertFalse(other.existing_access)

    def test_audit_holders_confirm_their_own(self):
        a1 = self.issue(self.aa1_1, self.alice)
        b1 = self.issue(self.aa1_2, self.bob)
        audit = self.env["bf.key.audit"].with_user(self.manager).create(
            {"name": "Audit 2026", "area_id": self.floor2.id})
        audit.action_start()
        self.assertEqual(audit.line_count, 2)
        alice_line = audit.line_ids.filtered(lambda l: l.assignment_id == a1)
        bob_line = audit.line_ids.filtered(lambda l: l.assignment_id == b1)
        self.assertEqual(alice_line.activity_ids.user_id, self.alice_user)
        with self.assertRaises(AccessError):
            bob_line.with_user(self.alice_user).action_holder_confirm()
        alice_line.with_user(self.alice_user).action_holder_confirm()
        self.assertEqual(alice_line.result, "confirmed")
        self.assertFalse(alice_line.activity_ids)
        bob_line.with_user(self.bob_user).action_holder_missing()
        self.assertEqual(audit.missing_count, 1)
        audit.action_done()
        self.assertEqual(audit.state, "done")
        with self.assertRaises(UserError):
            alice_line.with_user(self.alice_user).action_holder_missing()

    def test_audit_sample(self):
        self.issue(self.aa1_1, self.alice)
        self.issue(self.aa1_2, self.bob)
        self.issue(self.card_1, self.carol)
        self.issue(self.wh_1, self.alice)
        audit = self.env["bf.key.audit"].create({"name": "Sample", "scope": "sample", "sample_percent": 30,
                                                 "area_id": self.building.id})
        audit.action_start()
        # 30 % of the three holders of the building: one holder, with all they hold there.
        holders = audit.line_ids.assignment_id.employee_id
        self.assertEqual(len(holders), 1)
        self.assertEqual(len(audit.line_ids), len(self.env["bf.key.assignment"].search(
            [("employee_id", "=", holders.id), ("date_returned", "=", False),
             ("area_id", "child_of", self.building.id)])))

    def test_holder_without_email(self):
        """Many holders have no email address: nothing they press may need one."""
        self.alice_user.partner_id.email = False
        request = self._request(self.alice_user, type_id=self.aa1.id)
        request.action_submit()
        self.assertEqual(request.state, "approved")
        master = self._request(self.alice_user, type_id=self.aa.id)
        master.action_submit()
        self.assertEqual(master.state, "submitted")
        self.alice.write({"departure_date": "2026-10-30"})
