from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("privacy_consent", "privacy_destruction_campaign")
class TestPrivacyDestructionCampaign(TransactionCase):
    """Test cases for destruction campaigns (v3.0.0)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Campaign = cls.env["privacy.destruction.campaign"]
        cls.Classification = cls.env["privacy.document.classification"]
        cls.Register = cls.env["privacy.destruction.register"]

        cls.officer_group = cls.env.ref("privacy_consent.group_privacy_officer")
        cls.officer = cls.env["res.users"].create({
            "name": "Campaign Officer",
            "login": "test_campaign_officer",
            "email": "camp-officer@example.com",
            "groups_id": [(4, cls.officer_group.id)],
        })

        cls.calendar = cls.env["privacy.retention.calendar"].create({
            "name": "Expired contracts",
            "code": "CAMP-TEST",
            "document_type": "contract",
            "legal_basis": "Test",
            "active_retention_years": 0,
            "semi_active_retention_years": 0,
            "destruction_method": "delete",
        })
        # Two partners to classify and destroy
        cls.p1 = cls.env["res.partner"].create({"name": "Campaign P1"})
        cls.p2 = cls.env["res.partner"].create({"name": "Campaign P2"})
        for partner in (cls.p1, cls.p2):
            cls.Classification.create({
                "res_model": "res.partner",
                "res_id": partner.id,
                "pi_category": "identification",
                "retention_calendar_id": cls.calendar.id,
            })

    def test_scan_populates_lines(self):
        """action_scan creates one campaign line per past-retention document."""
        campaign = self.Campaign.create({
            "name": "Test campaign scan",
            "retention_calendar_id": self.calendar.id,
            "cutoff_date": fields.Date.today() + timedelta(days=1),
        })
        campaign.action_scan()
        self.assertEqual(campaign.state, "review")
        self.assertEqual(campaign.line_count, 2)

    def test_approve_execute_workflow(self):
        """Workflow draft→scan→approve→execute creates N register entries."""
        campaign = self.Campaign.create({
            "name": "Test full workflow",
            "retention_calendar_id": self.calendar.id,
            "cutoff_date": fields.Date.today() + timedelta(days=1),
        })
        campaign.action_scan()
        campaign.with_user(self.officer).action_approve()
        self.assertEqual(campaign.state, "approved")
        self.assertEqual(campaign.approved_by_id, self.officer)

        initial_register_count = self.Register.search_count([])
        campaign.with_user(self.officer).action_execute()
        self.assertEqual(campaign.state, "completed")
        self.assertEqual(campaign.executed_count, 2)
        self.assertEqual(campaign.failed_count, 0)
        self.assertEqual(
            self.Register.search_count([]) - initial_register_count, 2
        )

    def test_empty_campaign_rejected_on_approve(self):
        """Approving a campaign with 0 lines raises UserError."""
        # Calendar with no matching classifications
        empty_cal = self.env["privacy.retention.calendar"].create({
            "name": "Empty",
            "code": "EMPTY-CAMP",
            "document_type": "other",
            "legal_basis": "Test",
        })
        campaign = self.Campaign.create({
            "name": "Empty campaign",
            "retention_calendar_id": empty_cal.id,
            "cutoff_date": fields.Date.today(),
        })
        campaign.action_scan()
        self.assertEqual(campaign.line_count, 0)
        with self.assertRaises(UserError):
            campaign.with_user(self.officer).action_approve()

    def test_approve_requires_officer(self):
        """Non-officer cannot approve."""
        plain_user = self.env["res.users"].create({
            "name": "Plain",
            "login": "test_plain_camp",
            "email": "plain@example.com",
            "groups_id": [(4, self.env.ref("privacy_consent.group_privacy_user").id)],
        })
        campaign = self.Campaign.create({
            "name": "Approve gate",
            "retention_calendar_id": self.calendar.id,
            "cutoff_date": fields.Date.today() + timedelta(days=1),
        })
        campaign.action_scan()
        with self.assertRaises(UserError):
            campaign.with_user(plain_user).action_approve()

    def test_action_cancel(self):
        """Cancel transitions state to cancelled."""
        campaign = self.Campaign.create({
            "name": "To cancel",
            "cutoff_date": fields.Date.today(),
        })
        result = campaign.action_cancel()
        self.assertTrue(result)
        self.assertEqual(campaign.state, "cancelled")

    def test_cannot_cancel_completed(self):
        """A completed campaign cannot be cancelled."""
        campaign = self.Campaign.create({
            "name": "Done",
            "retention_calendar_id": self.calendar.id,
            "cutoff_date": fields.Date.today() + timedelta(days=1),
        })
        campaign.action_scan()
        campaign.with_user(self.officer).action_approve()
        campaign.with_user(self.officer).action_execute()
        with self.assertRaises(UserError):
            campaign.action_cancel()
