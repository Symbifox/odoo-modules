from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import MembershipCase


@tagged("post_install", "-at_install", "bf_membership")
class TestDelegates(MembershipCase):

    def setUp(self):
        super().setUp()
        self.membership = self._membership(self.member_org, self.type_org)
        self.membership.action_accept()
        self.membership.action_mark_paid()
        self._recompute_status(self.member_org)
        self.Delegate = self.env["bf.membership.delegate"]

    def test_organization_votes_through_its_delegates(self):
        self.Delegate.create({"organization_id": self.member_org.id, "partner_id": self.carole.id})
        self.Delegate.create({"organization_id": self.member_org.id, "partner_id": self.denis.id, "role": "alternate", "voting": False})
        self.assertEqual(self.member_org._voting_representatives(self.today), self.carole)
        self.assertEqual(self.alice._voting_representatives(self.today), self.alice)

    def test_one_voice_per_organization(self):
        """Une organisation a une voix : deux délégués qui votent en même temps, non."""
        self.Delegate.create({"organization_id": self.member_org.id, "partner_id": self.carole.id})
        with self.assertRaises(ValidationError):
            self.Delegate.create({"organization_id": self.member_org.id, "partner_id": self.denis.id})
        self.Delegate.create({"organization_id": self.member_org.id, "partner_id": self.denis.id,
                              "role": "alternate", "voting": False})
        self.assertEqual(self.member_org._voting_representatives(self.today), self.carole)

    def test_delegates_capped_by_category(self):
        """La catégorie permet deux délégués désignés à la fois ; un troisième est refusé."""
        self.Delegate.create({"organization_id": self.member_org.id, "partner_id": self.carole.id})
        self.Delegate.create({"organization_id": self.member_org.id, "partner_id": self.denis.id, "voting": False})
        with self.assertRaises(ValidationError):
            self.Delegate.create({"organization_id": self.member_org.id, "partner_id": self.emma.id, "voting": False})

    def test_successive_mandates_do_not_count_together(self):
        self.Delegate.create({"organization_id": self.member_org.id, "partner_id": self.carole.id,
                              "date_from": self._day(years=-2), "date_to": self._day(years=-1)})
        self.Delegate.create({"organization_id": self.member_org.id, "partner_id": self.denis.id})
        self.Delegate.create({"organization_id": self.member_org.id, "partner_id": self.emma.id, "voting": False})
        self.assertEqual(self.member_org._voting_representatives(self.today), self.denis)
        self.assertEqual(self.member_org._voting_representatives(self._day(years=-1, days=-10)), self.carole)

    def test_delegate_must_be_a_person_of_an_organization(self):
        with self.assertRaises(ValidationError):
            self.Delegate.create({"organization_id": self.alice.id, "partner_id": self.bruno.id})
        with self.assertRaises(ValidationError):
            self.Delegate.create({"organization_id": self.member_org.id, "partner_id": self.member_org.id})
