from dateutil.relativedelta import relativedelta

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import UnionCase


@tagged("post_install", "-at_install")
class TestCard(UnionCase):

    def _card(self, membership, **kw):
        vals = {"membership_id": membership.id, "date_signed": self.today}
        vals.update(kw)
        return self.env["bf.labour.card"].create(vals)

    def test_signing_marks_the_membership_but_not_the_coverage(self):
        """🔴 Adhérer ne change pas la couverture.

        Une carte qui toucherait `covered` mélangerait les deux états que le
        socle sépare exprès, et la cotisation se mettrait à suivre l'adhésion.

        ⚠️ Le décor part d'une personne NON couverte exprès : sur quelqu'un de
        déjà couvert, une carte qui écrirait `covered = True` ne changerait
        rien et l'essai passerait en mentant.
        """
        membership = self._membership("Hors unité", covered=False, is_member=False)
        self._card(membership)
        self.assertTrue(membership.is_member, "elle a signé, elle vote")
        self.assertFalse(
            membership.covered,
            "la couverture vient de l'accréditation, jamais d'une carte",
        )

    def test_signing_leaves_an_already_covered_person_covered(self):
        membership = self._membership("Dans l'unité", is_member=False)
        self._card(membership)
        self.assertTrue(membership.is_member)
        self.assertTrue(membership.covered)

    def test_withdrawing_removes_the_vote_not_the_dues(self):
        membership = self._membership("Retirée")
        card = self._card(membership)
        card.action_withdraw()
        self.assertEqual(card.state, "withdrawn")
        self.assertFalse(membership.is_member)
        self.assertTrue(membership.covered, "elle cotise toujours, article 47")

    def test_two_active_cards_are_refused(self):
        membership = self._membership("Deux fois")
        self._card(membership)
        with self.assertRaises(ValidationError):
            self._card(membership, date_signed=self.today)

    def test_rejoining_after_a_withdrawal_is_allowed(self):
        """Une personne quitte et revient ; les deux cartes restent au dossier."""
        membership = self._membership("Revenue")
        first = self._card(membership, date_signed=self.today - relativedelta(years=3))
        first.action_withdraw()
        second = self._card(membership, date_signed=self.today)
        self.assertEqual(second.state, "active")
        self.assertTrue(first.exists())
        self.assertEqual(
            len(self.env["bf.labour.card"].search([
                ("membership_id", "=", membership.id),
            ])), 2,
        )
        self.assertTrue(membership.is_member)

    def test_withdrawal_cannot_precede_the_signature(self):
        membership = self._membership("Dates inversées")
        with self.assertRaises(ValidationError):
            self._card(
                membership,
                date_signed=self.today,
                date_withdrawn=self.today - relativedelta(days=1),
            )
