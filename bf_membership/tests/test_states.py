from datetime import timedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import MembershipCase


@tagged("post_install", "-at_install", "bf_membership")
class TestStates(MembershipCase):

    def test_automatic_admission_waits_for_payment(self):
        membership = self._membership(self.alice)
        self.assertEqual(membership.state, "waiting")
        self.assertEqual(membership.payment_state, "to_pay")
        self.assertEqual(membership.amount, 70.0)
        self.assertFalse(self.alice.member_number, "Pas de numéro avant la première mise en règle.")
        membership.action_mark_paid()
        self.assertTrue(self.alice.member_number, "La première mise en règle attribue le numéro.")

    def test_payment_from_any_source_makes_member(self):
        """Pas de facture Odoo : le paiement se note, et le membre est en règle."""
        membership = self._membership(self.alice)
        membership.write({"payment_state": "paid", "payment_source": "zeffy"})
        self.assertEqual(membership.state, "active")
        self._recompute_status(self.alice)
        self.assertEqual(self.alice.member_status, "member")
        self.assertEqual(self.alice.current_membership_id, membership)

    def test_reversed_payment_goes_back_to_waiting(self):
        membership = self._membership(self.alice, payment_state="paid")
        self.assertEqual(membership.state, "active")
        membership.payment_state = "to_pay"
        self.assertEqual(membership.state, "waiting")

    def test_decision_admission_stays_draft(self):
        membership = self._membership(self.member_org, self.type_org)
        self.assertEqual(membership.state, "draft")
        membership.action_accept()
        self.assertEqual(membership.state, "waiting")
        self.assertTrue(membership.decided_by_id)
        membership.action_mark_paid()
        self.assertEqual(membership.state, "active")
        self.assertEqual(membership.payment_source, "other")

    def test_number_comes_with_first_good_standing(self):
        """Ni la demande ni l'acceptation ne brûlent un numéro : la mise en règle, oui."""
        membership = self._membership(self.member_org, self.type_org)
        self.assertFalse(self.member_org.member_number)
        membership.action_accept()
        self.assertFalse(self.member_org.member_number, "Acceptée, à payer : pas encore membre.")
        membership.action_mark_paid()
        self.assertTrue(self.member_org.member_number)

    def test_unpaid_membership_can_be_refused_and_deleted_with_its_contact(self):
        """Le pourriel d'un formulaire public : refusé, supprimé, et son contact avec."""
        spam = self.env["res.partner"].create({"name": "Pourriel (essai)", "email": "spam@exemple.test"})
        membership = self._membership(spam)
        self.assertEqual(membership.state, "waiting")
        membership.action_refuse()
        self.assertEqual(membership.state, "refused")
        spam.unlink()
        self.assertFalse(membership.exists())
        self.assertFalse(spam.exists())

    def test_paid_membership_cannot_be_refused(self):
        membership = self._membership(self.alice, payment_state="paid")
        with self.assertRaises(UserError):
            membership.action_refuse()

    def test_unpaid_withdrawal_is_not_a_former_member(self):
        membership = self._membership(self.bruno)
        membership._withdraw(self.today, "Désistement")
        self._recompute_status(self.bruno)
        self.assertEqual(self.bruno.member_status, "none", "Jamais en règle, jamais membre.")

    def test_refuse_then_reset(self):
        membership = self._membership(self.member_org, self.type_org)
        membership.action_refuse()
        self.assertEqual(membership.state, "refused")
        with self.assertRaises(UserError):
            membership.action_mark_paid()
        membership.action_reset_draft()
        self.assertEqual(membership.state, "draft")

    def test_member_kind_enforced(self):
        with self.assertRaises(ValidationError):
            self._membership(self.member_org, self.type_person)
        with self.assertRaises(ValidationError):
            self._membership(self.alice, self.type_org)

    def test_no_overlapping_live_memberships(self):
        self._membership(self.alice, date_start=self._day(days=-10))
        with self.assertRaises(ValidationError):
            self._membership(self.alice, date_start=self._day(days=-5))

    def test_accepted_membership_cannot_be_deleted(self):
        """Le registre garde les anciens membres (C-38, art. 104)."""
        membership = self._membership(self.alice)
        with self.assertRaises(UserError):
            membership.unlink()
        draft = self._membership(self.member_org, self.type_org)
        draft.unlink()

    def test_member_contact_cannot_be_deleted(self):
        self._membership(self.alice)
        with self.assertRaises(UserError):
            self.alice.unlink()

    def test_withdraw_keeps_contact_and_marks_former(self):
        membership = self._membership(self.alice, payment_state="paid")
        wizard = self.env["bf.membership.withdraw"].create({
            "membership_ids": [(6, 0, membership.ids)],
            "reason": "Démission par lettre",
        })
        wizard.action_confirm()
        self.assertEqual(membership.state, "withdrawn")
        self.assertEqual(membership.withdrawal_reason, "Démission par lettre")
        self._recompute_status(self.alice)
        self.assertEqual(self.alice.member_status, "former")
        self.assertTrue(self.alice.exists())

    def test_number_is_kept_on_return(self):
        first = self._membership(self.alice, payment_state="paid",
                                 date_start=self._day(years=-3), date_end=self._day(years=-2))
        number = self.alice.member_number
        first.state = "expired"
        self._membership(self.alice, payment_state="paid")
        self.assertEqual(self.alice.member_number, number)

    def test_grace_then_former(self):
        membership = self._membership(self.alice, payment_state="paid",
                                      date_start=self._day(years=-1), date_end=self._day(days=-10))
        membership.state = "expired"
        self._recompute_status(self.alice)
        self.assertEqual(self.alice.member_status, "grace", "10 jours après l'échéance, délai de 30 jours")
        membership.date_end = self._day(days=-40)
        self._recompute_status(self.alice)
        self.assertEqual(self.alice.member_status, "former")

    def test_unpaid_expired_is_not_a_former_member(self):
        membership = self._membership(self.bruno, date_start=self._day(years=-1), date_end=self._day(days=-60))
        membership.state = "expired"
        self._recompute_status(self.bruno)
        self.assertEqual(self.bruno.member_status, "none")

    def test_renew_starts_next_day(self):
        membership = self._membership(self.alice, payment_state="paid")
        membership.action_renew()
        renewal = membership.renewal_ids
        self.assertEqual(len(renewal), 1)
        self.assertEqual(renewal.date_start, membership.date_end + timedelta(days=1))
        self.assertEqual(renewal.state, "waiting")
        with self.assertRaises(UserError):
            membership.action_renew()

    def test_free_renewal_is_exempt(self):
        free = self.type_person.copy({"code": "GRAT", "fee": 0.0})
        membership = self._membership(self.bruno, free, payment_state="exempt")
        membership.action_renew()
        self.assertEqual(membership.renewal_ids.state, "active")

    def test_is_current_search(self):
        current = self._membership(self.alice, payment_state="paid")
        found = self.env["bf.membership"].search([("is_current", "=", True)])
        self.assertIn(current, found)
        not_found = self.env["bf.membership"].search([("is_current", "=", False)])
        self.assertNotIn(current, not_found)
