from dateutil.relativedelta import relativedelta

from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import new_test_user, tagged

from .common import ExCase


@tagged("post_install", "-at_install")
class TestDepartureCost(ExCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Un départ déjà en base (données d'un locataire, essai précédent) se
        # compterait dans nos mois : on sort de la société toute fiche partie.
        elsewhere = cls.env["res.company"].create({"name": "Départs d'ailleurs (essai)"})
        cls.env["hr.employee"].with_context(active_test=False).search([
            ("company_id", "=", cls.env.company.id), ("departure_date", "!=", False),
        ]).write({"company_id": elsewhere.id})
        cls.period = (cls.today - relativedelta(months=1)).replace(day=1)
        cls.month_end = cls.period + relativedelta(months=1, days=-1)
        cls.policy = cls.env["bf.ex.departure.policy"]._for_company(cls.company)
        cls.fired = cls.env["hr.departure.reason"].search([("reason_code", "=", 342)], limit=1)

    def _leaver(self, name, monthly_wage, when=None, archive=True, reason=None):
        employee = self._employee(name, months=24)
        employee.contract_ids.wage = monthly_wage
        employee.write({
            "departure_date": when or self.month_end,
            "departure_reason_id": (reason or self.env["hr.departure.reason"]).id,
        })
        if archive:
            employee.active = False  # ce que fait l'assistant de départ d'Odoo
        return employee

    def _record(self):
        return self.env["bf.ex.indicator"]._compute_period(self.company, self.period)

    # ------------------------------------------------------------ le défaut corrigé

    def test_archived_leaver_still_counts(self):
        self._leaver("Partie archivée", 5000.0)
        record = self._record()
        self.assertEqual(record.departures, 1, "Le départ archive la fiche ; il compte quand même.")
        self.assertGreaterEqual(record.headcount_start, 1)

    # ------------------------------------------------------------ la confidentialité

    def test_single_departure_publishes_nothing(self):
        self.policy.method = "salary_pct"
        self._leaver("Seule", 5000.0)
        record = self._record()
        self.assertTrue(record.departure_cost_hidden)
        self.assertFalse(record.departure_cost)
        self.assertFalse(record.departure_cost_low)

    def test_minimum_never_below_three(self):
        with self.assertRaises(ValidationError):
            self.policy.min_departures = 2

    def test_hr_user_cannot_read_departures_or_policy(self):
        self._leaver("Partie", 5000.0)
        self._record()
        user = new_test_user(self.env, login="rh_simple", groups="base.group_user,hr.group_hr_user")
        for model in ("bf.ex.departure", "bf.ex.departure.policy"):
            with self.assertRaises(AccessError):
                self.env[model].with_user(user).search([]).read(["company_id"])

    # ------------------------------------------------------------ les trois méthodes

    def _three_leavers(self):
        # 3 000 $ par mois = 36 000 $ par année, tranche 1 (16 %).
        return [self._leaver("Partie %s" % i, 3000.0) for i in range(3)]

    def test_salary_pct_range(self):
        self.policy.method = "salary_pct"
        self._three_leavers()
        record = self._record()
        self.assertFalse(record.departure_cost_hidden)
        self.assertAlmostEqual(record.departure_cost_low, 3 * 36000 * 0.16, places=2)
        self.assertAlmostEqual(record.departure_cost, 3 * 36000 * 0.333, places=2)
        self.assertAlmostEqual(record.departure_cost_avg_low, 36000 * 0.16, places=2)

    def test_itemized_adds_estimated_ramp_on_top_of_measured(self):
        self.policy.method = "itemized"
        for employee in self._three_leavers():
            self.env["bf.ex.departure"]._ensure_for(employee).write({
                "recruitment_cost": 1000.0, "training_cost": 500.0, "interim_cost": 200.0,
            })
        record = self._record()
        self.assertAlmostEqual(record.departure_cost_low, 3 * 1700.0, places=2)
        daily = 36000 * 1.15 / 260.0
        self.assertAlmostEqual(record.departure_cost, 3 * (1700.0 + 60 * daily * 0.5), places=2)

    def test_hybrid_never_counts_interim_twice(self):
        self.policy.method = "hybrid"
        for employee in self._three_leavers():
            self.env["bf.ex.departure"]._ensure_for(employee).write({
                "recruitment_cost": 1000.0, "interim_cost": 9999.0,
            })
        record = self._record()
        self.assertAlmostEqual(record.departure_cost_low, 3000.0, places=2)
        self.assertAlmostEqual(record.departure_cost, 3000.0 + 3 * 36000 * 0.22, places=2)

    def test_separation_counts_only_when_involuntary(self):
        self.policy.method = "hybrid"
        leavers = self._three_leavers()
        records = self.env["bf.ex.departure"]._ensure_for(self.env["hr.employee"].browse([e.id for e in leavers]))
        records.write({"separation_cost": 4000.0})
        self.assertEqual(records.mapped("measured_direct"), [0.0, 0.0, 0.0])
        records[0].kind = "involuntary"
        self.assertEqual(records[0].measured_direct, 4000.0)

    def test_fired_reason_makes_departure_involuntary(self):
        leaver = self._leaver("Congédiée", 3000.0, reason=self.fired)
        departure = self.env["bf.ex.departure"]._ensure_for(leaver)
        self.assertEqual(departure.kind, "involuntary")
        record = self._record()
        self.assertEqual(record.departures_involuntary, 1)

    def test_window_is_twelve_months(self):
        self.policy.method = "salary_pct"
        self._leaver("Il y a longtemps", 3000.0, when=self.period - relativedelta(months=13))
        self._leaver("Récente 1", 3000.0)
        self._leaver("Récente 2", 3000.0, when=self.period - relativedelta(months=6))
        record = self._record()
        self.assertEqual(record.departures_12m, 2)
        self.assertTrue(record.departure_cost_hidden)

    def test_method_none_computes_nothing(self):
        self._three_leavers()
        record = self._record()
        self.assertEqual(record.departure_cost_method, "none")
        self.assertFalse(record.departure_cost)
        self.assertFalse(record.departure_cost_hidden)

    # ------------------------------------------------------------ la différence entre deux mois

    def test_hr_user_cannot_read_departure_amounts(self):
        """Deux mois consécutifs, un départ entre les deux : la différence des sommes
        glissantes donnerait le coût de CE départ, donc un salaire."""
        self.policy.method = "salary_pct"
        self._three_leavers()
        record = self._record()
        self.assertTrue(record.departure_cost, "le montant existe : l'essai lirait du vide sinon")
        agent = new_test_user(self.env, login="rh_agent_cout",
                              groups="base.group_user,hr.group_hr_user")
        vu = record.with_user(agent).sudo(False)
        vu.read(["period", "departures_12m", "departure_cost_hidden"])
        for champ in ("departure_cost_low", "departure_cost",
                      "departure_cost_avg_low", "departure_cost_avg"):
            with self.assertRaises(AccessError, msg=champ):
                vu.read([champ])
        arch = self.env["bf.ex.indicator"].with_user(agent).get_views(
            [(False, "form"), (False, "list")])["views"]
        for vue in arch.values():
            self.assertNotIn('name="departure_cost"', vue["arch"])
        responsable = new_test_user(self.env, login="rh_resp_cout",
                                    groups="base.group_user,hr.group_hr_manager")
        self.assertTrue(record.with_user(responsable).sudo(False).departure_cost)
