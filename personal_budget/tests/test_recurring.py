from datetime import date

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import MenageCase


@tagged('post_install', '-at_install', 'personal_budget_menage')
class TestDepensesRecurrentes(MenageCase):

    def setUp(self):
        super().setUp()
        E = self.env_a
        self.book = E['personal.budget.book']._default_book()
        self.cat_abos = E['personal.budget.category'].create(
            {'name': 'Abonnements', 'category_type': 'expense'})
        self.cat_assur = E['personal.budget.category'].create(
            {'name': 'Assurances', 'category_type': 'expense'})
        self.Recurring = E['personal.budget.recurring']

    def _new(self, **vals):
        base = {'name': 'Diffusion', 'category_id': self.cat_abos.id, 'amount': 20.0,
                'frequency': 'monthly', 'date_start': '2031-01-01'}
        base.update(vals)
        return self.Recurring.create(base)

    # --- Échéances -------------------------------------------------------

    def test_echeances_mensuelles_ancrees_sur_la_premiere(self):
        rec = self._new(date_start='2031-01-31')
        days = rec._occurrences(date(2031, 1, 1), date(2031, 12, 31))
        self.assertEqual(len(days), 12)
        self.assertEqual(days[:4], [date(2031, 1, 31), date(2031, 2, 28), date(2031, 3, 31), date(2031, 4, 30)])
        self.assertEqual(days[-1], date(2031, 12, 31))
        self.assertEqual(rec.next_date, date(2031, 1, 31))

    def test_echeances_par_frequence(self):
        year = (date(2031, 1, 1), date(2031, 12, 31))
        counts = {'weekly': 53, 'biweekly': 27, 'monthly': 12, 'quarterly': 4, 'semiannual': 2, 'yearly': 1}
        # 2031-01-01 est un mercredi : 53 mercredis en 2031.
        for frequency, expected in counts.items():
            with self.subTest(frequency=frequency):
                rec = self._new(frequency=frequency)
                self.assertEqual(len(rec._occurrences(*year)), expected)
        self.assertAlmostEqual(self._new(frequency='yearly', amount=1200.0).monthly_amount, 100.0)
        self.assertAlmostEqual(self._new(frequency='weekly', amount=12.0).monthly_amount, 52.0)

    def test_fin_d_abonnement(self):
        rec = self._new(date_end='2031-03-15')
        self.assertEqual(len(rec._occurrences(date(2031, 1, 1), date(2031, 12, 31))), 3)
        rec._post_due(date(2031, 12, 31))
        self.assertFalse(rec.next_date)
        self.assertEqual(len(rec.transaction_ids), 3)

    def test_saisie_des_echeances_sans_doublon(self):
        rec = self._new(date_start='2031-01-10', self_percent=50.0)
        created = rec._post_due(date(2031, 3, 20))
        self.assertEqual(created.mapped('date'), [date(2031, 1, 10), date(2031, 2, 10), date(2031, 3, 10)])
        self.assertEqual(set(created.mapped('gross_amount')), {20.0})
        self.assertEqual(set(created.mapped('self_amount')), {10.0})
        self.assertEqual(created.recurring_id, rec)
        self.assertEqual(created.book_id, self.book)
        self.assertEqual(rec.last_posted_date, date(2031, 3, 10))
        self.assertEqual(rec.next_date, date(2031, 4, 10))
        # Rejouer la même saisie ne crée rien.
        self.assertFalse(rec._post_due(date(2031, 3, 20)))
        # Saisir d'avance la prochaine échéance.
        rec.action_post_next()
        self.assertEqual(rec.last_posted_date, date(2031, 4, 10))
        self.assertEqual(rec.transaction_count, 4)

    def test_categorie_de_revenu_refusee(self):
        rev = self.env_a['personal.budget.category'].create({'name': 'Paie', 'category_type': 'revenue'})
        with self.assertRaises(ValidationError):
            self._new(category_id=rev.id)
        with self.assertRaises(ValidationError):
            self._new(date_start='2031-05-01', date_end='2031-04-01')

    # --- Effet sur le budget ----------------------------------------------

    def _dashboard(self, today, year=2031):
        return self.env_a['personal.budget.dashboard']._get_dashboard_data(self.book, year, today)

    def _row(self, data, name):
        rows = [r for r in data['expense_rows'] if r['category_name'] == name]
        self.assertEqual(len(rows), 1, "catégorie absente du tableau de bord : %s" % name)
        return rows[0]

    def test_abonnements_comptent_au_budget_sans_plan(self):
        self._new(name='Diffusion', amount=20.0)                            # 12 × 20
        self._new(name='Cellulaire', amount=45.0, self_percent=50.0)        # 12 × 22,50
        self._new(name='Assurance habitation', category_id=self.cat_assur.id,
                  amount=1200.0, frequency='yearly', date_start='2031-03-10')
        data = self._dashboard(date(2031, 6, 15))

        abos = self._row(data, 'Abonnements')
        self.assertEqual(abos['plan_source'], 'recurring')
        self.assertEqual(abos['plan_by_month'][1], 42.5)
        self.assertEqual(abos['year_planned'], 510.0)
        assur = self._row(data, 'Assurances')
        self.assertEqual(assur['plan_by_month'][3], 1200.0)
        self.assertEqual(assur['plan_by_month'][4], 0.0)
        # Cumul prévu au 15 juin : janvier à mai en entier + la moitié de juin.
        june_fraction = (date(2031, 6, 15).timetuple().tm_yday / 365) * 12 - 5
        self.assertAlmostEqual(abos['ytd_planned'], round(42.5 * 5 + 42.5 * june_fraction, 2), places=2)
        self.assertEqual(assur['ytd_planned'], 1200.0)
        self.assertEqual(data['summary']['total_expense_planned'],
                         round(abos['ytd_planned'] + assur['ytd_planned'], 2))

        recurring = data['recurring']
        self.assertEqual(recurring['count'], 3)
        self.assertEqual(recurring['monthly_total'], round(20 + 22.5 + 100, 2))
        # Rien n'est saisi : toutes les échéances de l'année restent à payer.
        self.assertEqual(recurring['remaining'], 510.0 + 1200.0)
        overdue = [u for u in recurring['upcoming'] if u['is_overdue']]
        self.assertTrue(overdue, "les échéances passées non saisies doivent paraître en retard")

    def test_saisie_deplace_le_reel_et_le_reste_a_payer(self):
        diffusion = self._new(name='Diffusion', amount=20.0)
        assurance = self._new(name='Assurance habitation', category_id=self.cat_assur.id,
                              amount=1200.0, frequency='yearly', date_start='2031-03-10')
        today = date(2031, 6, 15)
        (diffusion | assurance)._post_due(today)
        data = self._dashboard(today)
        abos = self._row(data, 'Abonnements')
        self.assertEqual([abos['by_month'][m] for m in range(1, 8)], [20.0] * 6 + [0.0])
        self.assertEqual(self._row(data, 'Assurances')['by_month'][3], 1200.0)
        self.assertEqual(data['summary']['total_expense_real'], 120.0 + 1200.0)
        self.assertEqual(data['recurring']['remaining'], 6 * 20.0)            # juillet à décembre
        self.assertEqual(data['summary']['expense_projected'], 1320.0 + 120.0)
        self.assertEqual([u['date'] for u in data['recurring']['upcoming']], ['2031-07-01'])

    def test_un_plan_saisi_l_emporte_sur_la_prevision(self):
        self._new(amount=20.0)
        self.env_a['personal.budget.plan'].create(
            {'year': 2031, 'month': 0, 'category_id': self.cat_abos.id, 'planned_amount': 600.0})
        abos = self._row(self._dashboard(date(2031, 6, 15)), 'Abonnements')
        self.assertEqual(abos['plan_source'], 'plan')
        self.assertEqual(abos['plan_by_month'][1], 50.0)
        self.assertEqual(abos['recurring_by_month'][1], 20.0)

    def test_annee_passee_rien_a_payer(self):
        self._new(amount=20.0)
        data = self._dashboard(date(2032, 2, 1), year=2031)
        self.assertEqual(data['recurring']['remaining'], 0.0)
        self.assertEqual(self._row(data, 'Abonnements')['year_planned'], 240.0)

    # --- Tâche planifiée et ménage ------------------------------------------

    def test_tache_planifiee_saisit_dans_le_bon_budget(self):
        rec = self._new(amount=20.0, auto_post=True, date_start='2020-01-01', date_end='2020-03-31')
        manuel = self._new(name='Manuel', amount=5.0, date_start='2020-01-01', date_end='2020-03-31')
        created = self.env['personal.budget.recurring']._cron_post_due()
        self.assertEqual(created.recurring_id, rec.sudo())
        self.assertEqual(len(created), 3)
        self.assertFalse(manuel.transaction_ids)
        # Créées par le cron, elles restent dans le budget de A : A les voit, B non.
        self.assertEqual(len(self.env_a['personal.budget.transaction'].search([('recurring_id', '=', rec.id)])), 3)
        self.assertFalse(self.env_b['personal.budget.transaction'].search([('recurring_id', '=', rec.id)]))

    def test_abonnements_du_budget_commun(self):
        couple = self.env_a['personal.budget.book'].create({
            'name': 'Budget de couple', 'member_ids': [(6, 0, [self.user_b.id])],
        })
        cat = self.env_a['personal.budget.category'].create(
            {'name': 'Internet', 'category_type': 'expense', 'book_id': couple.id})
        rec = self._new(name='Internet', category_id=cat.id, amount=80.0)
        self.assertEqual(rec.book_id, couple)
        # B voit l'abonnement commun et le tableau de bord commun en tient compte.
        self.assertTrue(rec.with_env(self.env_b).read(['name']))
        data = self.env_b['personal.budget.dashboard']._get_dashboard_data(
            couple.with_env(self.env_b), 2031, date(2031, 6, 15))
        self.assertEqual(data['recurring']['monthly_total'], 80.0)
        # Le budget privé de A n'en compte rien.
        self.assertEqual(self._dashboard(date(2031, 6, 15))['recurring']['count'], 0)
        # C ne le voit pas.
        self.assertFalse(self.env_c['personal.budget.recurring'].search([('id', '=', rec.id)]))
