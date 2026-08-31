from dateutil.relativedelta import relativedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import ExCase


@tagged("post_install", "-at_install")
class TestUsage(ExCase):

    def setUp(self):
        super().setUp()
        self.benefit = self._benefit("Physiothérapie", cost_model="per_use", cost_amount=80.0)
        self.emp = self._employee("Utilisatrice", department=self.dept_ti)

    def _right(self, start=None, end=None):
        return self.env["bf.ex.entitlement"].create({
            "employee_id": self.emp.id,
            "benefit_id": self.benefit.id,
            "source": "manual",
            "reason": "Pour l'essai.",
            "date_start": start or self.today,
            "date_end": end or False,
        })

    def test_entitled_flag_true_when_right_covers_the_day(self):
        self._right()
        line = self.env["bf.ex.usage"].create({
            "employee_id": self.emp.id, "benefit_id": self.benefit.id, "date": self.today,
        })
        self.assertTrue(line.entitled)

    def test_entitled_flag_false_without_right(self):
        """Un usage sans droit n'est pas bloqué : il est signalé.

        Le bloquer cacherait l'anomalie ; la signaler la met sous les yeux de
        qui administre.
        """
        line = self.env["bf.ex.usage"].create({
            "employee_id": self.emp.id, "benefit_id": self.benefit.id, "date": self.today,
        })
        self.assertTrue(line.exists())
        self.assertFalse(line.entitled)

    def test_entitled_flag_respects_the_closing_date(self):
        self._right(start=self.today - relativedelta(days=30),
                    end=self.today - relativedelta(days=10))
        old = self.env["bf.ex.usage"].create({
            "employee_id": self.emp.id, "benefit_id": self.benefit.id,
            "date": self.today - relativedelta(days=20),
        })
        recent = self.env["bf.ex.usage"].create({
            "employee_id": self.emp.id, "benefit_id": self.benefit.id, "date": self.today,
        })
        self.assertTrue(old.entitled)
        self.assertFalse(recent.entitled)

    def test_confirmed_line_is_frozen(self):
        """Une ligne d'usage appartient à la date où elle a eu lieu."""
        self._right()
        line = self.env["bf.ex.usage"].create({
            "employee_id": self.emp.id, "benefit_id": self.benefit.id,
            "date": self.today, "amount": 80.0,
        })
        line.amount = 90.0  # brouillon : libre
        line.action_confirm()
        self.assertEqual(line.state, "confirmed")
        for vals in ({"amount": 100.0}, {"date": self.today - relativedelta(days=1)},
                     {"quantity": 3.0}, {"benefit_id": self._benefit("Autre").id}):
            with self.assertRaises(UserError):
                line.write(vals)
        # La note, elle, reste modifiable : ce n'est pas un chiffre du budget.
        line.note = "Reçu numérisé le lendemain."
        self.assertEqual(line.note, "Reçu numérisé le lendemain.")

    def test_no_return_to_draft(self):
        self._right()
        line = self.env["bf.ex.usage"].create({
            "employee_id": self.emp.id, "benefit_id": self.benefit.id, "date": self.today,
        })
        line.action_confirm()
        with self.assertRaises(UserError):
            line.action_reset_draft()

    def test_quantity_must_be_positive(self):
        with self.assertRaises(ValidationError):
            self.env["bf.ex.usage"].create({
                "employee_id": self.emp.id, "benefit_id": self.benefit.id,
                "date": self.today, "quantity": 0.0,
            })

    def test_uptake_and_cost_per_use(self):
        """Le coût réel des lignes prime sur le montant de référence."""
        self._right()
        other = self._employee("Ne s'en sert pas", department=self.dept_ti)
        self.env["bf.ex.entitlement"].create({
            "employee_id": other.id, "benefit_id": self.benefit.id,
            "source": "manual", "reason": "Pour l'essai.",
        })
        for amount in (80.0, 120.0):
            line = self.env["bf.ex.usage"].create({
                "employee_id": self.emp.id, "benefit_id": self.benefit.id,
                "date": self.today, "amount": amount,
            })
            line.action_confirm()

        self.benefit.invalidate_recordset()
        self.assertEqual(self.benefit.entitled_count, 2)
        self.assertEqual(self.benefit.user_count, 1)
        self.assertAlmostEqual(self.benefit.uptake_rate, 50.0)
        self.assertAlmostEqual(self.benefit.annual_cost, 200.0)
        self.assertAlmostEqual(self.benefit.cost_per_entitled, 100.0)
        self.assertFalse(self.benefit.unused)

    def test_unused_benefit_is_flagged(self):
        self._right()
        self.benefit.invalidate_recordset()
        self.assertTrue(self.benefit.unused)
        self.assertAlmostEqual(self.benefit.uptake_rate, 0.0)

    def test_cost_models(self):
        flat = self._benefit("Forfait", cost_model="flat_year", cost_amount=12000.0)
        per_head = self._benefit("Par tête", cost_model="per_employee_year", cost_amount=500.0)
        for benefit in (flat, per_head):
            for name in ("A", "B"):
                emp = self._employee("%s %s" % (benefit.name, name))
                self.env["bf.ex.entitlement"].create({
                    "employee_id": emp.id, "benefit_id": benefit.id,
                    "source": "manual", "reason": "Essai.",
                })
        flat.invalidate_recordset()
        per_head.invalidate_recordset()
        self.assertAlmostEqual(flat.annual_cost, 12000.0)
        self.assertAlmostEqual(per_head.annual_cost, 1000.0)

    def test_draft_usage_is_not_counted(self):
        self._right()
        self.env["bf.ex.usage"].create({
            "employee_id": self.emp.id, "benefit_id": self.benefit.id,
            "date": self.today, "amount": 500.0,
        })
        self.benefit.invalidate_recordset()
        self.assertEqual(self.benefit.user_count, 0)
        self.assertAlmostEqual(self.benefit.annual_cost, 0.0)
