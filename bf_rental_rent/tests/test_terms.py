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

    # ── La fin réelle du bail : saisie, jamais devinée ──

    def test_a_term_after_the_rent_end_is_neither_late_nor_arrears(self):
        """Le bail a pris fin : ce qui tombe après n'est pas une dette."""
        lease = self._lease()
        before = self._term(lease=lease, days_ago=40)
        after = self._term(lease=lease, days_ago=10)
        self.assertEqual(lease.arrears_total, 2000.0)
        lease.rent_due_until = self.today - relativedelta(days=20)
        self.assertEqual(before.state, "late", "dû avant la fin : toujours dû")
        self.assertEqual(after.state, "after_end")
        self.assertEqual(after.days_late, 0)
        self.assertEqual(lease.arrears_total, 1000.0)
        self.assertEqual(lease.arrears_days, 40)

    def test_the_last_day_of_rent_is_still_due(self):
        """« Dû jusqu'au » inclut le jour même : un terme exigible ce jour-là
        est encore une dette."""
        lease = self._lease()
        term = self._term(lease=lease, days_ago=10)
        lease.rent_due_until = term.date_due
        self.assertEqual(term.state, "late")
        self.assertEqual(lease.arrears_total, 1000.0)

    def test_the_lease_end_date_alone_does_not_end_the_rent(self):
        """🔴 Art. 1941 : un bail à durée fixe se reconduit de plein droit.

        Sa date de fin passée, le locataire est toujours là et le loyer
        toujours dû. Lire la fin du bail dans cette date effacerait les
        arrérages de tout bail reconduit, c'est-à-dire du cas courant.
        """
        lease = self._lease(date_start=self.today - relativedelta(years=1, days=30),
                            date_end=self.today - relativedelta(days=30))
        term = self._term(lease=lease, days_ago=10)
        self.assertFalse(lease.rent_due_until)
        self.assertEqual(term.state, "late")
        self.assertEqual(lease.arrears_total, 1000.0)

    def test_clearing_the_rent_end_brings_the_debt_back(self):
        """Une date saisie par erreur s'efface, et la dette revient entière."""
        lease = self._lease()
        term = self._term(lease=lease, days_ago=10)
        lease.rent_due_until = self.today - relativedelta(days=20)
        self.assertEqual(term.state, "after_end")
        lease.rent_due_until = False
        self.assertEqual(term.state, "late")
        self.assertEqual(term.days_late, 10)
        self.assertEqual(lease.arrears_total, 1000.0)

    def test_a_term_paid_or_partly_paid_after_the_end_says_so(self):
        """Payé après la fin : le locateur détient de l'argent pour une période
        qui n'est plus louée, et l'écran le montre au lieu de « Acquitté »."""
        lease = self._lease()
        paid = self._term(lease=lease, days_ago=10)
        partial = self._term(lease=lease, days_ago=5)
        self._pay(paid, 1000.0)
        self._pay(partial, 400.0)
        lease.rent_due_until = self.today - relativedelta(days=20)
        self.assertEqual(paid.state, "after_end")
        self.assertEqual(partial.state, "after_end")
        self.assertEqual(lease.arrears_total, 0.0)

    def test_copying_a_lease_does_not_carry_its_end(self):
        lease = self._lease()
        lease.rent_due_until = self.today - relativedelta(days=20)
        self.assertFalse(lease.copy().rent_due_until)

    def test_the_cron_leaves_an_after_end_term_alone(self):
        lease = self._lease()
        term = self._term(lease=lease, days_ago=10)
        lease.rent_due_until = self.today - relativedelta(days=20)
        self.assertEqual(term.state, "after_end")
        self.env.ref("bf_rental_rent.cron_term_refresh_state").method_direct_trigger()
        self.assertEqual(term.state, "after_end")
        self.assertEqual(lease.arrears_total, 0.0)

    def test_the_rent_cannot_stop_before_the_lease_starts(self):
        from odoo.exceptions import ValidationError
        lease = self._lease()
        with self.assertRaises(ValidationError):
            lease.rent_due_until = lease.date_start - relativedelta(days=1)
