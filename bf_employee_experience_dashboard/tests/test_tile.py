from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDashboardTile(TransactionCase):

    def test_key_is_present_and_defensive(self):
        data = self.env["bf.dashboard"].get_dashboard_data()
        self.assertIn("employee_experience", data)

    def test_summary_counts_unused_benefits(self):
        company = self.env.company
        employee = self.env["hr.employee"].create({
            "name": "Comptée", "company_id": company.id,
        })
        benefit = self.env["bf.ex.benefit"].create({
            "name": "Avantage boudé", "company_id": company.id,
            "category": "wellness", "cost_model": "per_employee_year",
            "cost_amount": 250.0,
        })
        self.env["bf.ex.entitlement"].create({
            "employee_id": employee.id, "benefit_id": benefit.id,
            "source": "manual", "reason": "Pour l'essai.",
        })
        summary = self.env["bf.dashboard"]._get_ex_summary()
        self.assertTrue(summary)
        self.assertGreaterEqual(summary["unused_count"], 1)
        self.assertGreaterEqual(summary["benefit_count"], 1)
        self.assertIn("%", summary["uptake_display"])

    def test_summary_is_none_without_benefits(self):
        """L'aide est défensive : pas de catalogue, pas de tuile, aucune erreur.

        ⚠️ Ne PAS tester la branche `except` en remplaçant la méthode sur la
        classe du registre : Odoo relève l'attribut ajouté (« Found unexpected
        attributes on bf.dashboard ») et fait échouer tous les tests suivants.
        """
        empty = self.env["res.company"].create({"name": "Société sans avantages"})
        summary = self.env["bf.dashboard"].with_company(empty)._get_ex_summary()
        self.assertIsNone(summary)
        data = self.env["bf.dashboard"].with_company(empty).get_dashboard_data()
        self.assertIn("employee_experience", data)
        self.assertIsNone(data["employee_experience"])
