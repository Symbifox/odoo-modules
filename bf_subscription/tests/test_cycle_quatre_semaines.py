"""Le cycle aux 4 semaines.

Un forfait prépayé renouvelé aux 28 jours n'est pas un abonnement mensuel :
13 renouvellements par année, et une échéance qui recule de deux ou trois jours
chaque mois. Rangé en mensuel, il donnait une date de renouvellement fausse dès le
deuxième cycle.
"""
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged

from ..models.subscription_subscription import cycle_days, cycle_step


@tagged('post_install', '-at_install')
class TestCycleQuatreSemaines(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.vendor = cls.env['res.partner'].create({'name': 'Fournisseur essai', 'supplier_rank': 1})
        cls.today = fields.Date.context_today(cls.env['subscription.subscription'])

    def _sub(self, **vals):
        base = {
            'name': 'Forfait essai', 'vendor_id': self.vendor.id, 'company_id': self.env.company.id,
            'cycle': 'four_weekly', 'cycle_amount': 120.0, 'state': 'active',
            'start_date': self.today,
        }
        base.update(vals)
        return self.env['subscription.subscription'].create(base)

    def test_le_pas_est_de_28_jours(self):
        self.assertEqual(cycle_step('four_weekly'), relativedelta(weeks=4))
        self.assertEqual(cycle_days('four_weekly'), 28)
        # Les cycles en mois gardent leur pas.
        self.assertEqual(cycle_step('quarterly'), relativedelta(months=3))
        self.assertEqual(cycle_days('monthly'), 30)
        self.assertIsNone(cycle_step('on_demand'))
        self.assertEqual(cycle_days('one_time'), 0)

    def test_le_renouvellement_avance_de_28_jours_et_non_d_un_mois(self):
        start = self.today - relativedelta(days=29)
        sub = self._sub(start_date=start)
        # start + 28 jours est hier ; le suivant tombe dans 27 jours.
        self.assertEqual(sub.next_billing_date, start + relativedelta(days=56))
        self.assertEqual(sub.next_billing_date, self.today + relativedelta(days=27))

    def test_le_renouvellement_du_jour_reste_aujourd_hui(self):
        sub = self._sub(start_date=self.today - relativedelta(days=84))
        self.assertEqual(sub.next_billing_date, self.today)

    def test_le_cout_mensualise_compte_13_renouvellements_par_annee(self):
        sub = self._sub(cycle_amount=120.0)
        self.assertAlmostEqual(sub.monthly_equivalent, 130.0, places=2)

    def test_la_dormance_attend_un_cycle_de_28_jours_plus_la_grace(self):
        sub = self._sub()
        self.assertEqual(
            sub._dormancy_cutoff(),
            self.today - relativedelta(days=28 + sub._DORMANCY_GRACE_DAYS),
        )

    def test_la_date_suit_le_cycle_quand_on_le_change(self):
        start = self.today - relativedelta(days=40)
        sub = self._sub(cycle='monthly', start_date=start)
        self.assertEqual(sub.next_billing_date, start + relativedelta(months=2))
        sub.cycle = 'four_weekly'
        self.assertEqual(sub.next_billing_date, start + relativedelta(days=56))
