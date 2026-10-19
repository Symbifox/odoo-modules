from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger

#: Le journal de ce pont. Les collecteurs de `bf_home` journalisent sous le leur,
#: que les essais de rôle font taire : ce qu'ils vérifient, c'est la tuile.
TILE_LOGGER = "odoo.addons.bf_employee_experience_dashboard"
HOME_LOGGER = "odoo.addons.bf_home"


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


@tagged("post_install", "-at_install")
class TestDashboardTileRoles(TransactionCase):
    """La tuile pour un employé ordinaire, et pour l'administration.

    Chaque chargement de l'accueil par un employé ordinaire journalisait
    `AccessError … 'Règle d'admissibilité' (bf.ex.eligibility.rule)`, trace
    comprise : le calcul du catalogue lit les règles, que seul `hr.group_hr_user`
    lit. La tuile est un indicateur de l'administration des avantages : pour les
    autres, rien, et rien au journal.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        group_user = cls.env.ref("base.group_user")
        group_hr = cls.env.ref("hr.group_hr_user")
        common = {"company_id": company.id, "company_ids": [(6, 0, [company.id])]}
        cls.plain = cls.env["res.users"].create(dict(
            common, name="Employée ordinaire", login="plain_ex_tile",
            groups_id=[(6, 0, [group_user.id])],
        ))
        cls.officer = cls.env["res.users"].create(dict(
            common, name="Administration des avantages", login="officer_ex_tile",
            groups_id=[(6, 0, [group_user.id, group_hr.id])],
        ))
        employees = cls.env["hr.employee"].create([
            {"name": "Tuile %s" % i, "company_id": company.id} for i in range(2)
        ])
        employees[0].user_id = cls.plain
        cls.benefit = cls.env["bf.ex.benefit"].create({
            "name": "Avantage de la tuile", "company_id": company.id,
            "category": "wellness", "cost_model": "per_employee_year",
            "cost_amount": 100.0,
        })
        cls.env["bf.ex.eligibility.rule"].create({
            "name": "Tout le monde", "benefit_id": cls.benefit.id,
        })
        for employee in employees:
            cls.env["bf.ex.entitlement"].create({
                "employee_id": employee.id, "benefit_id": cls.benefit.id,
                "source": "manual", "reason": "Pour l'essai.",
            })
        cls.env["bf.ex.usage"].create({
            "employee_id": employees[0].id, "benefit_id": cls.benefit.id,
        }).action_confirm()

    def _dashboard(self, user):
        self.env.invalidate_all()
        return self.env["bf.dashboard"].with_user(user)

    def test_plain_employee_reads_the_catalogue_but_gets_no_tile(self):
        """Le catalogue lui est ouvert ; la tuile, non, et sans trace."""
        self.env.invalidate_all()
        self.assertIn(
            self.benefit, self.env["bf.ex.benefit"].with_user(self.plain).search([]),
            "l'essai n'a de sens que si le catalogue est lisible",
        )
        with self.assertNoLogs(TILE_LOGGER, level="WARNING"):
            summary = self._dashboard(self.plain)._get_ex_summary()
        self.assertIsNone(summary)

    def test_plain_employee_home_screen_logs_nothing_from_the_tile(self):
        """Le vrai chemin : `get_dashboard_data()`, appelé par l'accueil."""
        with mute_logger(HOME_LOGGER), self.assertNoLogs(TILE_LOGGER, level="WARNING"):
            data = self._dashboard(self.plain).get_dashboard_data()
        self.assertIn("employee_experience", data)
        self.assertIsNone(data["employee_experience"])
        self.assertNotIn(
            "employee_experience", data.get("failed") or {},
            "ne pas avoir le droit n'est pas une panne",
        )

    def test_hr_officer_still_gets_the_tile(self):
        with mute_logger(HOME_LOGGER), self.assertNoLogs(TILE_LOGGER, level="WARNING"):
            data = self._dashboard(self.officer).get_dashboard_data()
        summary = data["employee_experience"]
        self.assertTrue(summary)
        self.assertGreaterEqual(summary["benefit_count"], 1)

    def test_uptake_is_read_on_a_0_100_scale(self):
        """Un avantage, deux ayants droit, une personne qui s'en sert : 50 %.

        La tuile relit `uptake_rate` tel quel. Si le champ passait un jour à une
        fraction de 0 à 1, elle afficherait « 0 % » : cet essai tomberait.
        """
        others = self.env["bf.ex.benefit"].search([
            ("company_id", "=", self.env.company.id), ("id", "!=", self.benefit.id),
        ])
        others.active = False
        summary = self._dashboard(self.officer)._get_ex_summary()
        self.assertEqual(summary["benefit_count"], 1)
        self.assertEqual(summary["uptake_display"], "50 %")
