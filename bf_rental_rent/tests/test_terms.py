"""Ce qu'un terme porte, et ce qu'un versement y change."""
from dateutil.relativedelta import relativedelta

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

    # ── Le passage du temps (cron quotidien) ──
    #
    # Un essai ne voit pas le temps passer : on recule l'échéance en base, sans
    # écriture de l'ORM, comme le ferait une nuit entre deux ouvertures du bail.

    def _age(self, term, days_ago):
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_rental_term SET date_due = %s WHERE id = %s",
            (self.today - relativedelta(days=days_ago), term.id),
        )
        self.env.invalidate_all()

    def test_the_cron_makes_a_lapsed_term_late(self):
        """Échu hier, impayé : le terme n'est plus « À venir », et le bail le
        compte aux arrérages sans attendre qu'on y touche."""
        lease = self._lease()
        term = self._term(lease=lease, days_ago=-5)
        paid = self._term(lease=lease, days_ago=-5)
        self._pay(paid, 1000.0)
        due_today = self._term(lease=lease, days_ago=0)
        deposited = self._term(lease=lease, days_ago=10, deposited_at_court=True)
        self.assertEqual(term.state, "pending")
        self._age(term, 3)
        self._age(paid, 3)
        self.assertEqual(term.state, "pending", "l'essai doit partir d'un état figé")
        self.assertEqual(lease.arrears_total, 0.0)
        stale = self.env["bf.rental.term"].search([
            ("id", "in", (term | paid).ids),
            ("state", "in", ("pending", "late", "partial")),
        ])
        self.assertEqual(stale, term, "le terme acquitté n'est pas à rafraîchir")
        # Par l'enregistrement du cron lui-même : une coquille dans son code ou
        # son modèle laisserait vert un essai qui appelle la méthode à nu.
        self.env.ref("bf_rental_rent.cron_term_refresh_state").method_direct_trigger()
        self.assertEqual(paid.state, "paid")
        self.assertEqual(due_today.state, "pending", "dû aujourd'hui n'est pas en retard")
        self.assertEqual(deposited.state, "deposited")
        self.assertEqual(term.state, "late")
        self.assertEqual(term.days_late, 3)
        self.assertEqual(lease.arrears_total, 1000.0, "ni le terme du jour ni le dépôt au greffe")
        self.assertEqual(lease.arrears_days, 3)

    def test_the_cron_carries_a_late_term_past_three_weeks(self):
        """🔴 Art. 1973 : passé trois semaines, le tribunal ne peut plus
        accorder de délai. Des jours de retard figés à vingt lui laissaient
        cette discrétion indéfiniment."""
        lease = self._lease()
        term = self._term(lease=lease, days_ago=20)
        self.assertTrue(lease.tribunal_may_grant_time)
        self._age(term, 25)
        self.assertEqual(term.days_late, 20, "l'essai doit partir d'un état figé")
        self.env["bf.rental.term"]._cron_refresh_state()
        self.assertEqual(term.days_late, 25)
        self.assertEqual(lease.arrears_days, 25)
        self.assertFalse(lease.tribunal_may_grant_time)
