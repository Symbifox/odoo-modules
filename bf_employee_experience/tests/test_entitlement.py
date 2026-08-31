from dateutil.relativedelta import relativedelta

from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import ExCase


@tagged("post_install", "-at_install")
class TestEntitlement(ExCase):

    def test_sync_opens_for_eligible(self):
        benefit = self._benefit("Massage")
        self.env["bf.ex.eligibility.rule"].create({
            "name": "TI", "benefit_id": benefit.id,
            "department_ids": [(6, 0, self.dept_ti.ids)],
        })
        inside = self._employee("Visée", department=self.dept_ti)
        outside = self._employee("Non visée", department=self.dept_admin)

        opened, closed = self.env["bf.ex.entitlement"]._sync_from_rules(benefits=benefit)
        self.assertEqual(len(closed), 0)
        self.assertIn(inside, opened.employee_id)
        self.assertNotIn(outside, opened.employee_id)
        self.assertEqual(opened.filtered(lambda e: e.employee_id == inside).source, "rule")

    def test_sync_is_idempotent(self):
        """Repasser le cron ne double pas les droits."""
        benefit = self._benefit("Café de spécialité")
        self.env["bf.ex.eligibility.rule"].create({
            "name": "Tous", "benefit_id": benefit.id,
        })
        self._employee("Quelqu'un", department=self.dept_ti)
        first, _ = self.env["bf.ex.entitlement"]._sync_from_rules(benefits=benefit)
        second, closed = self.env["bf.ex.entitlement"]._sync_from_rules(benefits=benefit)
        self.assertTrue(first)
        self.assertFalse(second, "un second passage n'ouvre rien de neuf")
        self.assertFalse(closed)

    def test_lost_eligibility_closes_and_stays_readable(self):
        """Un droit perdu se ferme, il ne disparaît pas.

        Sans ça, « à quoi avait-elle droit en mars dernier » n'a pas de réponse.
        """
        benefit = self._benefit("Voiture")
        self.env["bf.ex.eligibility.rule"].create({
            "name": "TI", "benefit_id": benefit.id,
            "department_ids": [(6, 0, self.dept_ti.ids)],
        })
        emp = self._employee("Mutée", department=self.dept_ti)
        opened, _ = self.env["bf.ex.entitlement"]._sync_from_rules(benefits=benefit)
        ent = opened.filtered(lambda e: e.employee_id == emp)
        self.assertTrue(ent)

        emp.department_id = self.dept_admin
        _, closed = self.env["bf.ex.entitlement"]._sync_from_rules(benefits=benefit)

        self.assertIn(ent, closed)
        self.assertTrue(ent.exists(), "le droit existe toujours")
        self.assertEqual(ent.date_end, self.today)
        # Fermé aujourd'hui veut dire « couvre encore aujourd'hui, plus demain ».
        # C'est ce que dit is_open_on, et ce dont dépend l'admissibilité d'un
        # usage daté du jour même.
        self.assertEqual(ent.state, "active")
        self.assertTrue(ent.is_open_on(self.today))
        self.assertFalse(ent.is_open_on(self.today + relativedelta(days=1)))
        # Le droit a été ouvert aujourd'hui par le cron : il ne couvrait pas hier.
        self.assertFalse(ent.is_open_on(self.today - relativedelta(days=1)))

        # Et le cron ne referme pas ce qui porte déjà une date de fin.
        _, again = self.env["bf.ex.entitlement"]._sync_from_rules(benefits=benefit)
        self.assertNotIn(ent, again, "une fermeture ne se rejoue pas à chaque passage")

    def test_manual_entitlement_survives_the_cron(self):
        """L'exception négociée est justement ce que le cron ne doit pas défaire."""
        benefit = self._benefit("Bureau fermé")
        self.env["bf.ex.eligibility.rule"].create({
            "name": "TI", "benefit_id": benefit.id,
            "department_ids": [(6, 0, self.dept_ti.ids)],
        })
        outsider = self._employee("Exception", department=self.dept_admin)
        manual = self.env["bf.ex.entitlement"].create({
            "employee_id": outsider.id,
            "benefit_id": benefit.id,
            "source": "manual",
            "reason": "Négocié à l'embauche par la direction.",
        })
        self.env["bf.ex.entitlement"]._sync_from_rules(benefits=benefit)
        self.env["bf.ex.entitlement"]._sync_from_rules(benefits=benefit)

        self.assertTrue(manual.exists())
        self.assertFalse(manual.date_end, "le cron n'a pas fermé l'exception")
        self.assertEqual(manual.granted_by_id, self.env.user)

    def test_manual_needs_a_reason(self):
        benefit = self._benefit("Quelque chose")
        emp = self._employee("Quelqu'un")
        with self.assertRaises(ValidationError):
            self.env["bf.ex.entitlement"].create({
                "employee_id": emp.id, "benefit_id": benefit.id, "source": "manual",
            })
        with self.assertRaises(ValidationError):
            self.env["bf.ex.entitlement"].create({
                "employee_id": emp.id, "benefit_id": benefit.id,
                "source": "manual", "reason": "   ",
            })

    def test_rule_does_not_duplicate_a_manual_right(self):
        """Une personne qui a déjà le droit à la main n'en reçoit pas un second."""
        benefit = self._benefit("Télétravail")
        self.env["bf.ex.eligibility.rule"].create({
            "name": "Tous", "benefit_id": benefit.id,
        })
        emp = self._employee("Déjà servie", department=self.dept_ti)
        self.env["bf.ex.entitlement"].create({
            "employee_id": emp.id, "benefit_id": benefit.id,
            "source": "manual", "reason": "Entente antérieure.",
        })
        self.env["bf.ex.entitlement"]._sync_from_rules(benefits=benefit)
        rights = self.env["bf.ex.entitlement"].search([
            ("employee_id", "=", emp.id), ("benefit_id", "=", benefit.id),
        ])
        self.assertEqual(len(rights), 1)
        self.assertEqual(rights.source, "manual")

    def test_end_before_start_refused(self):
        benefit = self._benefit("Rien")
        emp = self._employee("Quelqu'un")
        with self.assertRaises(ValidationError):
            self.env["bf.ex.entitlement"].create({
                "employee_id": emp.id, "benefit_id": benefit.id,
                "source": "manual", "reason": "x",
                "date_start": self.today,
                "date_end": self.today - relativedelta(days=1),
            })

    def test_benefit_no_longer_offered_closes_rights(self):
        benefit = self._benefit("Programme terminé",
                                date_end=self.today - relativedelta(days=1))
        self.env["bf.ex.eligibility.rule"].create({
            "name": "Tous", "benefit_id": benefit.id,
        })
        self._employee("Quelqu'un", department=self.dept_ti)
        opened, _ = self.env["bf.ex.entitlement"]._sync_from_rules(benefits=benefit)
        self.assertFalse(opened, "un avantage qui n'est plus offert n'ouvre rien")
