from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import ExCase


@tagged("post_install", "-at_install")
class TestEligibility(ExCase):

    def test_empty_rule_targets_everyone(self):
        """Une règle sans critère ne contraint rien."""
        benefit = self._benefit("Café")
        rule = self.env["bf.ex.eligibility.rule"].create({
            "name": "Tout le monde", "benefit_id": benefit.id,
        })
        emp = self._employee("Sans critère", department=self.dept_ti)
        self.assertIn(emp, rule._matching_employees())
        self.assertEqual(rule.criteria_summary, "tout le personnel")

    def test_department_filters(self):
        benefit = self._benefit("Stationnement")
        rule = self.env["bf.ex.eligibility.rule"].create({
            "name": "TI seulement", "benefit_id": benefit.id,
            "department_ids": [(6, 0, self.dept_ti.ids)],
        })
        inside = self._employee("Dans TI", department=self.dept_ti)
        outside = self._employee("Dans Admin", department=self.dept_admin)
        matched = rule._matching_employees()
        self.assertIn(inside, matched)
        self.assertNotIn(outside, matched)

    def test_seniority_needs_a_contract(self):
        """⚠️ Le coeur du trou de schéma.

        Odoo CE ne porte aucune date d'arrivée sur `hr.employee`. Sans
        `hr_contract`, l'ancienneté n'existe pas. Une personne sans contrat ne
        doit donc PAS passer un critère d'ancienneté : une ancienneté inconnue
        n'ouvre aucun droit.
        """
        benefit = self._benefit("Semaine supplémentaire")
        rule = self.env["bf.ex.eligibility.rule"].create({
            "name": "Après six mois", "benefit_id": benefit.id,
            "seniority_months_min": 6,
        })
        veteran = self._employee("Douze mois", months=12)
        rookie = self._employee("Deux mois", months=2)
        unknown = self._employee("Sans contrat")

        self.assertFalse(unknown.sudo().first_contract_date)
        matched = rule._matching_employees()
        self.assertIn(veteran, matched)
        self.assertNotIn(rookie, matched)
        self.assertNotIn(unknown, matched, "une ancienneté inconnue n'ouvre aucun droit")

    def test_criteria_are_and_ed(self):
        """Les critères d'une même règle se cumulent."""
        benefit = self._benefit("Portable haut de gamme")
        rule = self.env["bf.ex.eligibility.rule"].create({
            "name": "TI depuis six mois", "benefit_id": benefit.id,
            "department_ids": [(6, 0, self.dept_ti.ids)],
            "seniority_months_min": 6,
        })
        both = self._employee("TI et ancien", department=self.dept_ti, months=12)
        dept_only = self._employee("TI et neuf", department=self.dept_ti, months=1)
        senior_only = self._employee("Admin et ancien", department=self.dept_admin, months=12)
        matched = rule._matching_employees()
        self.assertIn(both, matched)
        self.assertNotIn(dept_only, matched)
        self.assertNotIn(senior_only, matched)

    def test_employee_type_and_calendar(self):
        benefit = self._benefit("Assurance")
        rule = self.env["bf.ex.eligibility.rule"].create({
            "name": "Permanents à temps plein", "benefit_id": benefit.id,
            "employee_type": "employee",
            "resource_calendar_ids": [(6, 0, self.calendar_full.ids)],
        })
        keep = self._employee("Permanent plein", calendar=self.calendar_full)
        part = self._employee("Permanent partiel", calendar=self.calendar_part)
        contractor = self._employee("Contractuel", employee_type="contractor")
        matched = rule._matching_employees()
        self.assertIn(keep, matched)
        self.assertNotIn(part, matched)
        self.assertNotIn(contractor, matched)

    def test_departed_person_gets_nothing_new(self):
        benefit = self._benefit("Gym")
        rule = self.env["bf.ex.eligibility.rule"].create({
            "name": "Tout le monde", "benefit_id": benefit.id,
        })
        gone = self._employee("Partie", departure=self.today)
        self.assertNotIn(gone, rule._matching_employees())

    def test_negative_seniority_refused(self):
        benefit = self._benefit("Rien")
        with self.assertRaises(ValidationError):
            self.env["bf.ex.eligibility.rule"].create({
                "name": "Absurde", "benefit_id": benefit.id,
                "seniority_months_min": -3,
            })

    def test_summary_is_readable(self):
        """La règle doit se dire en une phrase : c'est le sens du choix des
        critères cochés plutôt que d'un domaine brut."""
        benefit = self._benefit("Formation")
        rule = self.env["bf.ex.eligibility.rule"].create({
            "name": "TI après un an", "benefit_id": benefit.id,
            "department_ids": [(6, 0, self.dept_ti.ids)],
            "seniority_months_min": 12,
        })
        self.assertIn("TI (essai)", rule.criteria_summary)
        self.assertIn("12", rule.criteria_summary)
