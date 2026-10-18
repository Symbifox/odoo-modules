from datetime import timedelta
from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import KeyCase


@tagged("post_install", "-at_install")
class TestHandout(KeyCase):

    def test_issue_and_return(self):
        assignment = self.issue(self.aa1_1, self.alice)
        self.assertEqual(self.aa1_1.state, "issued")
        self.assertEqual(self.aa1_1.holder_label, "Alice Tremblay")
        self.assertEqual(assignment.holder_user_id, self.alice_user)
        self.aa1_1.with_user(self.manager).action_return()
        self.assertEqual(self.aa1_1.state, "available")
        self.assertEqual(assignment.end_reason, "returned")
        self.assertEqual(assignment.received_by_id, self.manager)
        self.assertEqual(len(self.aa1_1.assignment_ids), 1)

    def test_proposed_return_date_and_deposit(self):
        wizard = self.env["bf.key.issue.wizard"].with_user(self.manager).new({"copy_id": self.aa1_1.id})
        wizard._onchange_copy_id()
        self.assertEqual(wizard.date_due, fields.Date.context_today(wizard) + timedelta(days=30))
        self.assertEqual(wizard.deposit_amount, 20.0)

    def test_no_direct_transfer(self):
        self.issue(self.aa1_1, self.alice)
        with self.assertRaisesRegex(UserError, "Record its return first"):
            self.issue(self.aa1_1, self.bob)
        # The database refuses it too, whatever the path.
        # The unique index fires at the INSERT, before the Python constraint.
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"), \
                self.env.cr.savepoint():
            self.env["bf.key.assignment"].create({"copy_id": self.aa1_1.id, "employee_id": self.bob.id})

    def test_someone_without_an_account(self):
        assignment = self.issue(self.aa1_2, holder_kind="external", external_name="Marc Exemple",
                                external_phone="514 555 0199", external_employer="Serrurerie Exemple")
        self.assertEqual(assignment.holder_label, "Marc Exemple · Serrurerie Exemple")
        with self.assertRaises(UserError):
            self.issue(self.aa1_1, holder_kind="external")

    def test_a_retired_copy_cannot_be_handed_out(self):
        self.aa1_2.with_user(self.manager).action_retire()
        self.assertEqual(self.aa1_2.state, "retired")
        with self.assertRaises(UserError):
            self.issue(self.aa1_2, self.alice)

    def test_retire_refused_while_held(self):
        self.issue(self.aa1_1, self.alice)
        with self.assertRaises(UserError):
            self.aa1_1.with_user(self.manager).action_retire()

    def test_signature_kept(self):
        png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        assignment = self.issue(self.aa1_1, self.alice, signature=png, signed_by="Alice Tremblay")
        self.assertTrue(assignment.signature)
        self.assertEqual(assignment.signed_by, "Alice Tremblay")
        self.assertTrue(assignment.signed_on)

    def test_overdue_reminder_once(self):
        assignment = self.issue(self.aa1_1, self.alice, date_due=fields.Date.today() - timedelta(days=2))
        self.assertTrue(assignment.is_overdue)
        self.assertIn(assignment, self.env["bf.key.assignment"].search([("is_overdue", "=", True)]))
        self.assertNotIn(assignment, self.env["bf.key.assignment"].search([("is_overdue", "=", False)]))
        self.env["bf.key.assignment"]._cron_overdue()
        activities = assignment.activity_ids
        self.assertEqual(len(activities), 1)
        self.assertEqual(activities.user_id, self.alice_user)
        self.assertIn("To return", activities.summary)
        self.env["bf.key.assignment"]._cron_overdue()
        self.assertEqual(len(assignment.activity_ids), 1)
        # The return closes the reminder.
        assignment.with_user(self.manager).action_return()
        self.assertFalse(assignment.activity_ids)

    def test_overdue_without_account_goes_to_manager(self):
        assignment = self.issue(self.aa1_1, self.carol, date_due=fields.Date.today() - timedelta(days=1))
        self.env["bf.key.assignment"]._cron_overdue()
        self.assertEqual(assignment.activity_ids.user_id, self.manager)

    def test_retention_erases_people_without_account(self):
        external = self.issue(self.aa1_2, holder_kind="external", external_name="Marc Exemple",
                              external_phone="514 555 0199", external_employer="Serrurerie Exemple",
                              signature="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
                              signed_by="Marc Exemple")
        employee = self.issue(self.aa1_1, self.alice)
        external.with_user(self.manager).action_return()
        employee.with_user(self.manager).action_return()
        self.company.bf_key_retention_days = 30
        later = fields.Datetime.now() + timedelta(days=31)
        with patch("odoo.fields.Datetime.now", return_value=later):
            self.env["bf.key.assignment"]._cron_retention()
        self.assertTrue(external.anonymized)
        self.assertFalse(external.external_name)
        self.assertFalse(external.external_phone)
        self.assertFalse(external.signature)
        # "(erased)" in the company's language.
        self.assertTrue(external.holder_label)
        self.assertNotIn("Marc", external.holder_label)
        # An employee's link stays (their record has its own retention), the signature goes.
        self.assertEqual(employee.employee_id, self.alice)
        self.assertTrue(employee.anonymized)

    def test_retention_spares_recent_and_open(self):
        held = self.issue(self.aa1_2, holder_kind="external", external_name="Marc Exemple")
        self.company.bf_key_retention_days = 30
        self.env["bf.key.assignment"]._cron_retention()
        self.assertEqual(held.external_name, "Marc Exemple")

    def test_acknowledgement_in_the_reader_language(self):
        """No stored default: an existing company reads the standard text in its own language."""
        self.company.bf_key_acknowledgement = False
        self.assertIn("I acknowledge", self.company.with_context(lang="en_US")._bf_key_acknowledgement_text())
        if self.env["res.lang"]._lang_get("fr_CA").active:
            self.env["ir.module.module"]._load_module_terms(["bf_key_management"], ["fr_CA"], overwrite=True)
            text = self.company.with_context(lang="fr_CA")._bf_key_acknowledgement_text()
            self.assertNotIn("I acknowledge", text)
        # Printed as HTML, not as escaped text.
        self.company.bf_key_acknowledgement = False
        html = self.env["ir.qweb"]._render("bf_key_management.report_acknowledgement", {
            "docs": self.issue(self.aa1_1, self.alice), "company": self.company,
            "doc_ids": [], "doc_model": "bf.key.assignment"})
        self.assertNotIn("&lt;li&gt;", str(html))
        self.company.bf_key_acknowledgement = "<p>Our own text</p>"
        self.assertIn("Our own text", self.company._bf_key_acknowledgement_text())

    def test_wizard_shows_the_acknowledgement_when_it_opens(self):
        """The form opens through onchange: a compute without depends= stays empty there."""
        values = self.env["bf.key.issue.wizard"].with_user(self.manager).with_context(
            default_copy_id=self.aa1_1.id).onchange({}, [], {"acknowledgement": {}, "copy_id": {}})
        self.assertIn("acknowledge", values["value"]["acknowledgement"])
