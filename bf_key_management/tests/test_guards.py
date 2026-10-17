"""Guards on requests, hand-outs, audits and retention, each kept by a test."""
from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged

from .common import KeyCase


@tagged("post_install", "-at_install")
class TestGuards(KeyCase):

    def _request(self, user, **values):
        values.setdefault("reason", "x")
        return self.env["bf.key.request"].with_user(user).create(values)

    # 1. A holder cannot create an approved request.
    def test_request_born_draft(self):
        with self.assertRaises(AccessError):
            self._request(self.alice_user, type_id=self.gm.id, state="approved",
                          approved_by_id=self.manager.id)
        request = self._request(self.alice_user, type_id=self.gm.id, requested_by_id=self.bob_user.id)
        self.assertEqual(request.state, "draft")
        self.assertEqual(request.requested_by_id, self.alice_user)

    # 2. No write moves a hand-out.
    def test_no_transfer_by_write(self):
        assignment = self.issue(self.aa1_1, self.alice, user=self.responsible)
        for vals in ({"employee_id": self.bob.id}, {"copy_id": self.aa1_2.id},
                     {"external_name": "Someone"}, {"date_issued": fields.Datetime.now()}):
            with self.assertRaises(UserError):
                assignment.with_user(self.responsible).write(vals)
        assignment.with_user(self.responsible).action_return()
        with self.assertRaises(UserError):
            assignment.with_user(self.responsible).write({"date_returned": False})
        # What a manager may still adjust.
        self.issue(self.aa1_1, self.alice).with_user(self.responsible).write(
            {"date_due": fields.Date.today(), "note": "ok", "deposit_refunded": True})

    def test_new_due_date_earns_a_new_reminder(self):
        assignment = self.issue(self.aa1_1, self.alice, date_due=fields.Date.subtract(fields.Date.today(), days=3))
        self.env["bf.key.assignment"]._cron_overdue()
        self.assertTrue(assignment.reminded)
        assignment.write({"date_due": fields.Date.subtract(fields.Date.today(), days=1)})
        self.assertFalse(assignment.reminded)

    # 3. No one approves for themselves, and the state is protected for every role.
    def test_approver_cannot_approve_own_request(self):
        responsible_employee = self.env["hr.employee"].create(
            {"name": "Responsible", "user_id": self.responsible.id})
        request = self._request(self.responsible, type_id=self.aa.id, employee_id=responsible_employee.id)
        request.action_submit()
        with self.assertRaises(AccessError):
            request.with_user(self.responsible).action_approve()
        with self.assertRaises(AccessError):
            request.with_user(self.responsible).write({"state": "approved"})
        request.with_user(self.manager).action_approve()
        self.assertEqual(request.state, "approved")

    # 4. "Access already held" is said only to the person or to an approver.
    def test_existing_access_not_told_to_colleagues(self):
        self.issue(self.aa1_1, self.bob)
        snoop = self._request(self.alice_user, employee_id=self.bob.id, door_ids=[(6, 0, self.d201.ids)])
        self.assertFalse(snoop.existing_access)
        self.assertTrue(snoop.with_user(self.responsible).existing_access)

    # 5. The hierarchy path is reserved.
    def test_parent_path_reserved(self):
        with self.assertRaises(AccessError):
            self.aa1.with_user(self.alice_user).read(["parent_path"])

    # 6. Law 25: no name of a person without an account outlives the retention.
    def test_retention_leaves_no_name_in_messages(self):
        assignment = self.issue(self.aa1_2, holder_kind="external", external_name="Zorro Secretname",
                                external_employer="AcmeCorp",
                                date_due=fields.Date.subtract(fields.Date.today(), days=2))
        self.env["bf.key.assignment"]._cron_overdue()
        audit = self.env["bf.key.audit"].create({"name": "A", "area_id": self.floor2.id})
        audit.action_start()
        audit.action_done()
        assignment.with_user(self.manager).action_return()
        self.company.bf_key_retention_days = 1
        from datetime import timedelta
        from unittest.mock import patch
        with patch("odoo.fields.Datetime.now", return_value=fields.Datetime.now() + timedelta(days=2)):
            self.env["bf.key.assignment"]._cron_retention()
        texts = " ".join(self.env["mail.message"].search([
            "|", "&", ("model", "=", "bf.key.assignment"), ("res_id", "=", assignment.id),
            "&", ("model", "=", "bf.key.audit"), ("res_id", "=", audit.id)]).mapped("body"))
        self.assertNotIn("Zorro", texts)

    # 7. An area manager reads the audits of their areas only.
    def test_audit_visible_by_area(self):
        own = self.env["bf.key.audit"].create({"name": "Mine", "area_id": self.floor2.id})
        other = self.env["bf.key.audit"].create({"name": "Theirs", "area_id": self.other_area.id})
        visible = self.env["bf.key.audit"].with_user(self.responsible).search([])
        self.assertIn(own, visible)
        self.assertNotIn(other, visible)

    # 8. The hand-out matches the approved request.
    def test_issue_matches_the_request(self):
        request = self._request(self.alice_user, type_id=self.aa1.id)
        request.action_submit()
        self.assertEqual(request.state, "approved")
        Wizard = self.env["bf.key.issue.wizard"].with_user(self.manager)
        with self.assertRaises(UserError):
            Wizard.create({"request_id": request.id, "copy_id": self.aa_1.id,
                           "employee_id": self.alice.id}).action_issue()
        with self.assertRaises(UserError):
            Wizard.create({"request_id": request.id, "copy_id": self.aa1_1.id,
                           "employee_id": self.bob.id}).action_issue()
        Wizard.create({"request_id": request.id, "copy_id": self.aa1_1.id,
                       "employee_id": self.alice.id}).action_issue()
        self.assertEqual(request.state, "issued")

    def test_auto_approval_respects_named_doors(self):
        request = self._request(self.alice_user, type_id=self.aa1.id,
                                door_ids=[(6, 0, (self.d201 | self.warehouse).ids)])
        request.action_submit()
        self.assertEqual(request.state, "submitted")

    # 9. Only area managers can be responsible for an area.
    def test_area_responsible_must_be_in_the_group(self):
        with self.assertRaises(ValidationError):
            self.floor2.manager_ids = [(4, self.bob_user.id)]

    def test_area_manager_cannot_move_a_copy_to_another_type(self):
        with self.assertRaises(UserError):
            self.aa1_2.with_user(self.responsible).type_id = self.warehouse_key

    def test_return_by_manager_without_email(self):
        assignment = self.issue(self.aa1_1, self.alice, date_due=fields.Date.subtract(fields.Date.today(), days=2))
        self.env["bf.key.assignment"]._cron_overdue()
        self.assertTrue(assignment.activity_ids)
        nomail = new_test_user(self.env, login="key_nomail", groups="base.group_user,bf_key_management.group_key_manager")
        nomail.partner_id.email = False
        assignment.with_user(nomail).action_return()
        self.assertFalse(assignment.activity_ids)


@tagged("post_install", "-at_install")
class TestContextAndLifecycle(KeyCase):
    """No context key opens a guard (any user sends any context by RPC)."""

    def test_context_key_opens_nothing(self):
        ctx = {"bf_key_internal": True}
        with self.assertRaises(AccessError):
            self.env["bf.key.request"].with_user(self.alice_user).with_context(ctx).create(
                {"type_id": self.gm.id, "reason": "x", "state": "approved", "approved_by_id": self.manager.id})
        assignment = self.issue(self.aa1_1, self.alice, user=self.responsible)
        with self.assertRaises(UserError):
            assignment.with_user(self.responsible).with_context(ctx).write({"employee_id": self.bob.id})
        assignment.with_user(self.responsible).action_return()
        with self.assertRaises(UserError):
            assignment.with_user(self.responsible).with_context(ctx).write({"date_returned": False})
        request = self.env["bf.key.request"].with_user(self.responsible).create(
            {"type_id": self.aa.id, "reason": "x", "employee_id": self.carol.id})
        with self.assertRaises(AccessError):
            request.with_user(self.responsible).with_context(ctx).write({"state": "approved"})

    def test_holder_cannot_end_their_own_hand_out(self):
        assignment = self.issue(self.aa1_1, self.alice)
        with self.assertRaises(AccessError):
            assignment.with_user(self.alice_user).action_return()
        self.assertFalse(assignment.date_returned)

    def test_create_born_open_and_never_deleted(self):
        Assignment = self.env["bf.key.assignment"].with_user(self.manager)
        with self.assertRaises(UserError):
            Assignment.create({"copy_id": self.aa1_1.id, "employee_id": self.alice.id,
                               "date_returned": fields.Datetime.now()})
        with self.assertRaises(UserError):
            Assignment.create({"copy_id": self.aa1_1.id, "employee_id": self.alice.id, "anonymized": True})
        assignment = Assignment.create({"copy_id": self.aa1_1.id, "employee_id": self.alice.id,
                                        "issued_by_id": self.bob_user.id,
                                        "date_issued": "2020-01-01 09:00:00"})
        self.assertEqual(assignment.issued_by_id, self.manager)
        with self.assertRaises(UserError):
            assignment.unlink()
        with self.assertRaises(UserError):
            assignment.copy()

    def test_retention_clears_record_names(self):
        from datetime import timedelta
        from unittest.mock import patch
        assignment = self.issue(self.aa1_2, holder_kind="external", external_name="Zoé O'Brien",
                                date_due=fields.Date.subtract(fields.Date.today(), days=2))
        self.env["bf.key.assignment"]._cron_overdue()
        assignment._message_log(body="Some note")
        assignment.with_user(self.manager).action_return()
        self.company.bf_key_retention_days = 1
        with patch("odoo.fields.Datetime.now", return_value=fields.Datetime.now() + timedelta(days=2)):
            self.env["bf.key.assignment"]._cron_retention()
        messages = self.env["mail.message"].search([("model", "=", "bf.key.assignment"), ("res_id", "=", assignment.id)])
        self.assertTrue(messages)
        for message in messages:
            self.assertNotIn("Brien", (message.record_name or "") + (message.subject or "") + (message.body or ""))

    def test_activities_from_a_manager_without_email(self):
        """Starting an audit, declaring a loss: the activity goes to someone else, and Odoo
        emails it "from" the current user. A manager without email must not be stopped."""
        nomail = new_test_user(self.env, login="key_nomail2",
                               groups="base.group_user,bf_key_management.group_key_manager")
        nomail.partner_id.email = False
        self.issue(self.aa1_1, self.alice)
        audit = self.env["bf.key.audit"].with_user(nomail).create({"name": "A", "area_id": self.floor2.id})
        audit.action_start()
        self.assertTrue(audit.line_ids.activity_ids)
        self.company.bf_key_manager_id = self.manager
        action = self.env["bf.key.loss.wizard"].with_user(nomail).create({"copy_id": self.aa1_1.id}).action_declare()
        self.assertTrue(self.env["bf.key.incident"].browse(action["res_id"]).activity_ids)

    def test_area_manager_confirms_own_line_of_a_wider_audit(self):
        """An area manager holding a key answers for their own line of an audit covering an
        area they cannot read (regression guard; the stored audit_state keeps it cheap)."""
        responsible_employee = self.env["hr.employee"].create(
            {"name": "Responsible", "user_id": self.responsible.id})
        self.issue(self.wh_1, responsible_employee)
        audit = self.env["bf.key.audit"].create({"name": "Warehouse", "area_id": self.other_area.id})
        audit.action_start()
        line = audit.line_ids.with_user(self.responsible)
        line.action_holder_confirm()
        self.assertEqual(line.result, "confirmed")
        self.env["bf.key.audit.line"].with_user(self.responsible).search(
            [("holder_user_id", "=", self.responsible.id)]).read(["audit_state", "copy_id", "result"])
