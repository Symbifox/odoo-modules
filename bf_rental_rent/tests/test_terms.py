"""Ce qu'un terme porte, et ce qu'un versement y change."""
from odoo.tests.common import tagged

from .common import RentCase


@tagged("post_install", "-at_install")
class TestTerms(RentCase):

    def test_an_unpaid_term_due_today_is_not_late(self):
        """⚠️ Le loyer est payable LE premier jour du terme (art. 1903) : il
        n'est pas en retard ce jour-là."""
        term = self._term(days_ago=0)
        self.assertEqual(term.state, "pending")
        self.assertEqual(term.days_late, 0)

    def test_an_unpaid_term_is_late_the_day_after(self):
        term = self._term(days_ago=1)
        self.assertEqual(term.state, "late")
        self.assertEqual(term.days_late, 1)

    def test_a_paid_term_has_no_late_days_even_if_paid_late(self):
        """Un terme acquitté en retard n'a plus de retard : il a été payé. Ce
        qui reste, c'est un historique, pas une dette."""
        term = self._term(days_ago=40)
        self._pay(term, 1000.0)
        self.assertEqual(term.state, "paid")
        self.assertEqual(term.days_late, 0)

    def test_a_partial_payment_leaves_the_balance(self):
        term = self._term(days_ago=10)
        self._pay(term, 400.0)
        self.assertEqual(term.state, "partial")
        self.assertEqual(term.amount_outstanding, 600.0)

    def test_an_overpayment_does_not_make_the_balance_negative(self):
        """Un solde négatif se lirait comme une dette du locateur, ce qu'il
        n'est pas : c'est une avance, et elle s'impute ailleurs."""
        term = self._term(days_ago=5)
        self._pay(term, 1200.0)
        self.assertEqual(term.amount_outstanding, 0.0)

    def test_a_term_deposited_at_court_is_not_late(self):
        """🔴 Art. 1907 : le locataire autorisé dépose son loyer au greffe. Il a
        PAYÉ, ailleurs. Le compter en défaut l'accuserait d'avoir fait ce que la
        loi lui permet."""
        term = self._term(days_ago=45, deposited_at_court=True)
        self.assertEqual(term.state, "deposited")
        self.assertEqual(term.days_late, 0)
