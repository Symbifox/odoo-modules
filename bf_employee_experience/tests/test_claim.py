from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import ExCase


@tagged("post_install", "-at_install")
class TestClaim(ExCase):

    def setUp(self):
        super().setUp()
        self.benefit = self._benefit(
            "Remboursement de formation", approval_required=True,
            cost_model="per_use", cost_amount=0.0,
        )
        self.emp = self._employee("Demandeuse", department=self.dept_ti)

    def _right(self):
        return self.env["bf.ex.entitlement"].create({
            "employee_id": self.emp.id, "benefit_id": self.benefit.id,
            "source": "manual", "reason": "Pour l'essai.",
        })

    def test_claim_needs_a_benefit_that_asks_for_approval(self):
        automatic = self._benefit("Assurance", approval_required=False)
        with self.assertRaises(ValidationError):
            self.env["bf.ex.claim"].create({
                "employee_id": self.emp.id, "benefit_id": automatic.id,
            })

    def test_submit_without_a_right_is_refused(self):
        """La porte d'entrée est le droit, pas un solde de points.

        C'est ce qui sépare ce module d'un catalogue de récompenses.
        """
        claim = self.env["bf.ex.claim"].create({
            "employee_id": self.emp.id, "benefit_id": self.benefit.id,
        })
        with self.assertRaises(UserError):
            claim.action_submit()
        self.assertEqual(claim.state, "draft")

    def test_full_cycle(self):
        self._right()
        claim = self.env["bf.ex.claim"].create({
            "employee_id": self.emp.id, "benefit_id": self.benefit.id,
            "amount": 750.0, "quantity": 1.0,
        })
        claim.action_submit()
        self.assertEqual(claim.state, "submitted")
        claim.action_approve()
        self.assertEqual(claim.state, "approved")
        self.assertEqual(claim.approver_id, self.env.user)
        claim.action_consume()
        self.assertEqual(claim.state, "consumed")
        self.assertTrue(claim.usage_id)
        self.assertEqual(claim.usage_id.state, "confirmed")
        self.assertAlmostEqual(claim.usage_id.amount, 750.0)
        self.assertTrue(claim.usage_id.entitled)

    def test_refusal_needs_a_reason(self):
        self._right()
        claim = self.env["bf.ex.claim"].create({
            "employee_id": self.emp.id, "benefit_id": self.benefit.id,
        })
        claim.action_submit()
        with self.assertRaises(UserError):
            claim.action_refuse()
        claim.refusal_reason = "Budget de formation épuisé pour l'exercice."
        claim.action_refuse()
        self.assertEqual(claim.state, "refused")

    def test_approve_only_from_submitted(self):
        self._right()
        claim = self.env["bf.ex.claim"].create({
            "employee_id": self.emp.id, "benefit_id": self.benefit.id,
        })
        with self.assertRaises(UserError):
            claim.action_approve()

    def test_consume_only_from_approved(self):
        self._right()
        claim = self.env["bf.ex.claim"].create({
            "employee_id": self.emp.id, "benefit_id": self.benefit.id,
        })
        claim.action_submit()
        with self.assertRaises(UserError):
            claim.action_consume()

    def test_approver_mode_is_per_benefit(self):
        """« Configurable par avantage » : la décision de conception, pas une valeur codée en dur."""
        boss_user = self.env["res.users"].create({
            "name": "Patronne", "login": "patronne_ex_test",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        boss = self._employee("Patronne", user=boss_user)
        self.emp.parent_id = boss

        self.benefit.approver_mode = "responsible"
        claim = self.env["bf.ex.claim"].create({
            "employee_id": self.emp.id, "benefit_id": self.benefit.id,
        })
        self.assertEqual(claim._allowed_approvers(), self.benefit.responsible_id)

        self.benefit.approver_mode = "manager"
        self.assertEqual(claim._allowed_approvers(), boss_user)

        self.benefit.approver_mode = "both"
        self.assertEqual(
            claim._allowed_approvers(),
            self.benefit.responsible_id | boss_user,
        )
