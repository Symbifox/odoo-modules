"""🔴 Les arrérages, et ce que les trois semaines changent RÉELLEMENT.

Ces essais tiennent la lecture des art. 1971, 1973 et 1883 ensemble. Prises
séparément, chacune se laisse mal comprendre ; c'est leur combinaison qui
interdit d'écrire « trois semaines, vous pouvez résilier ».
"""
from odoo.tests.common import tagged

from .common import RentCase


@tagged("post_install", "-at_install")
class TestArrears(RentCase):

    def test_a_future_term_is_not_arrears(self):
        from dateutil.relativedelta import relativedelta
        lease = self._lease()
        self.env["bf.rental.term"].create({
            "lease_id": lease.id,
            "date_due": self.today + relativedelta(days=15),
            "amount_due": 1000.0,
        })
        self.assertEqual(lease.arrears_total, 0.0)

    def test_arrears_add_up_across_terms(self):
        lease = self._lease()
        self._term(lease=lease, days_ago=40)
        self._term(lease=lease, days_ago=10)
        self.assertEqual(lease.arrears_total, 2000.0)
        self.assertEqual(lease.arrears_days, 40)

    def test_a_deposited_term_is_excluded_from_arrears(self):
        """Art. 1907 : il a payé, ailleurs."""
        lease = self._lease()
        self._term(lease=lease, days_ago=40, deposited_at_court=True)
        self.assertEqual(lease.arrears_total, 0.0)
        self.assertEqual(lease.arrears_days, 0)

    # ── Art. 1973 : ce que le seuil change ──

    def test_below_three_weeks_the_tribunal_may_still_grant_time(self):
        lease = self._lease()
        self._term(lease=lease, days_ago=20)
        self.assertTrue(lease.tribunal_may_grant_time)

    def test_at_exactly_three_weeks_the_tribunal_may_still_grant_time(self):
        """⚠️ Le texte dit « PLUS de trois semaines ». Vingt-et-un jours pile
        n'y sont pas : le tribunal garde sa discrétion. Un `>=` retirerait au
        locataire un jour que la loi lui laisse."""
        lease = self._lease()
        self._term(lease=lease, days_ago=21)
        self.assertTrue(lease.tribunal_may_grant_time)

    def test_past_three_weeks_the_tribunal_loses_that_power(self):
        """🔴 Et c'est TOUT ce que le seuil change. Il ne dit pas que le bail
        peut être résilié : seul le tribunal résilie (art. 1971, « peut
        obtenir »), et le locataire peut encore tout arrêter en payant avant
        jugement (art. 1883)."""
        lease = self._lease()
        self._term(lease=lease, days_ago=22)
        self.assertFalse(lease.tribunal_may_grant_time)

    def test_paying_before_judgment_restores_everything(self):
        """Art. 1883 : le versement efface les arrérages, et la discrétion du
        tribunal revient avec eux. Rien n'est acquis tant qu'un jugement n'est
        pas rendu."""
        lease = self._lease()
        term = self._term(lease=lease, days_ago=45)
        self.assertFalse(lease.tribunal_may_grant_time)
        self._pay(term, 1000.0)
        self.assertEqual(lease.arrears_total, 0.0)
        self.assertTrue(lease.tribunal_may_grant_time)
